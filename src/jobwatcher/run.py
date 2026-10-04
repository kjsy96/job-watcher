"""The daily run: fetch every board, filter the new postings, write the report.

Built to be started every hour by Task Scheduler on a laptop that is shut
or offline at unpredictable times (issue 2.6). Only one real run counts
per local day:

- already ran today:  skip, nothing fetched
- offline:            every company failed with no HTTP response; nothing
                      recorded and the day is not counted, so the next
                      attempt tries again
- partial failure:    counts; the report lists the failures first
- full success:       counts
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import TextIO

import httpx

from jobwatcher import fetch
from jobwatcher.fetch import CompanyResult
from jobwatcher.filter_config import FilterRules
from jobwatcher.filters import evaluate
from jobwatcher.models import Company
from jobwatcher.report import Change, ReportItem, render_refilter_report, render_report
from jobwatcher.store import Store


class RunStatus(StrEnum):
    DONE = "done"  # fetched, filtered, reported, and counted as today's run
    ALREADY_RAN = "already ran"  # today already has a counted run; nothing fetched
    OFFLINE = "offline"  # every company failed without any HTTP response


@dataclass(frozen=True, slots=True)
class RunResult:
    status: RunStatus
    companies: list[CompanyResult]
    items: list[ReportItem]  # this run's new postings, each with its filter result
    report_path: Path | None  # today's existing report when ALREADY_RAN; None if OFFLINE


def run(
    companies: Sequence[Company],
    rules: FilterRules,
    store: Store,
    client: httpx.Client,
    seen_at: datetime,
    reports_dir: Path,
    errors: TextIO | None = None,
    force: bool = False,
) -> RunResult:
    """Fetch, filter, record, and report, at most once per local day.

    seen_at is both the run's timestamp in the store and the time shown in
    the report; pass it as a local, timezone-aware time so "today", the
    report's date, and its file name match the owner's calendar day.
    force runs even if today already has a counted run.
    """
    if not force and (earlier := store.run_on(seen_at.date())) is not None:
        return RunResult(RunStatus.ALREADY_RAN, [], [], Path(earlier[1]))

    results = fetch.fetch_all(list(companies), store, client, seen_at, errors=errors)
    if fetch.looks_offline(results):
        # Failed companies are never recorded, so nothing was written; leave
        # the day uncounted for the next hourly attempt.
        return RunResult(RunStatus.OFFLINE, results, [], None)

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

    all_ok = all(r.outcome is fetch.Outcome.OK for r in results)
    store.record_run(seen_at, "ok" if all_ok else "partial", str(path))
    return RunResult(RunStatus.DONE, results, items, path)


@dataclass(frozen=True, slots=True)
class RefilterResult:
    items: list[ReportItem]
    changes: list[Change]
    first_filtered: int
    report_path: Path


def refilter(
    companies: Sequence[Company],
    rules: FilterRules,
    store: Store,
    run_at: datetime,
    reports_dir: Path,
) -> RefilterResult:
    """Re-run the current rules over every open stored posting. No network.

    Used after editing filters.toml (the tuning week), and to filter postings
    recorded before filtering existed. Outcome changes against each posting's
    previously stored result are listed, so the effect of a rule edit is
    visible at once.
    """
    sectors = {(c.source, c.board): c.sector for c in companies}
    items: list[ReportItem] = []
    changes: list[Change] = []
    first_filtered = 0
    for stored in store.open_postings():
        posting = stored.posting
        result = evaluate(posting, rules, sectors.get((posting.source, posting.board), ""))
        before, _ = store.filter_result(posting.id)
        if before is None:
            first_filtered += 1
        elif before != result.outcome.value:
            changes.append(Change(posting, before, result.outcome.value))
        items.append(ReportItem(posting, result))

    store.set_filter_results(
        [(i.posting.id, i.result.outcome.value, i.result.reasons) for i in items]
    )
    path = next_report_path(reports_dir, run_at.date(), prefix="refilter-")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_refilter_report(run_at, items, changes, first_filtered), encoding="utf-8"
    )
    return RefilterResult(items, changes, first_filtered, path)


def next_report_path(reports_dir: Path, day: date, prefix: str = "") -> Path:
    """reports/YYYY-MM-DD.md, or -2, -3, ... if that day already has a report.

    A second run on the same day must never replace the first report: the
    later run usually has no new postings, and overwriting would lose the
    morning's shortlist.
    """
    path = reports_dir / f"{prefix}{day:%Y-%m-%d}.md"
    n = 2
    while path.exists():
        path = reports_dir / f"{prefix}{day:%Y-%m-%d}-{n}.md"
        n += 1
    return path
