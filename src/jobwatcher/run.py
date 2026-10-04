"""The daily run: fetch every board, filter the new postings, write the report."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TextIO

import httpx

from jobwatcher import fetch
from jobwatcher.fetch import CompanyResult
from jobwatcher.filter_config import FilterRules
from jobwatcher.filters import evaluate
from jobwatcher.models import Company
from jobwatcher.report import ReportItem, render_report
from jobwatcher.store import Store


@dataclass(frozen=True, slots=True)
class RunResult:
    companies: list[CompanyResult]
    items: list[ReportItem]  # this run's new postings, each with its filter result
    report_path: Path


def run(
    companies: Sequence[Company],
    rules: FilterRules,
    store: Store,
    client: httpx.Client,
    seen_at: datetime,
    reports_dir: Path,
    errors: TextIO | None = None,
) -> RunResult:
    """Fetch, filter, record, and report.

    seen_at is both the run's timestamp in the store and the time shown in
    the report; pass it as a local, timezone-aware time so the report's
    date and file name match the owner's calendar day.
    """
    results = fetch.fetch_all(list(companies), store, client, seen_at, errors=errors)

    sectors = {(c.source, c.board): c.sector for c in companies}
    items = [
        ReportItem(stored.posting, evaluate(stored.posting, rules, sectors.get(key, "")))
        for stored in store.first_seen_in(seen_at)
        for key in [(stored.posting.source, stored.posting.board)]
    ]
    store.set_filter_results(
        [(i.posting.id, i.result.outcome.value, i.result.reasons) for i in items]
    )

    path = next_report_path(reports_dir, seen_at.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(seen_at, results, items), encoding="utf-8")
    return RunResult(results, items, path)


def next_report_path(reports_dir: Path, day: date) -> Path:
    """reports/YYYY-MM-DD.md, or -2, -3, ... if that day already has a report.

    A second run on the same day must never replace the first report: the
    later run usually has no new postings, and overwriting would lose the
    morning's shortlist.
    """
    path = reports_dir / f"{day:%Y-%m-%d}.md"
    n = 2
    while path.exists():
        path = reports_dir / f"{day:%Y-%m-%d}-{n}.md"
        n += 1
    return path
