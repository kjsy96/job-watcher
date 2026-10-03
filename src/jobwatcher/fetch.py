"""The fetch step: every company's board, fetched once and recorded."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import httpx

from jobwatcher import __version__
from jobwatcher.models import Company, SourceName
from jobwatcher.sources import SOURCES
from jobwatcher.sources.base import Source, SourceError
from jobwatcher.store import BoardResult, Store

USER_AGENT = f"job-watcher/{__version__} (+https://github.com/kjsy96/job-watcher)"
TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class CompanyResult:
    """The outcome for one company: either recorded counts or an error."""

    company: Company
    fetched: int | None = None
    board: BoardResult | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def make_client() -> httpx.Client:
    """The one HTTP client used for a whole run.

    Timeouts and the user agent are set here and nowhere else, so every
    request the tool makes is identifiable and can't hang forever.
    """
    return httpx.Client(
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def fetch_all(
    companies: list[Company],
    store: Store,
    client: httpx.Client,
    seen_at: datetime,
    sources: Mapping[SourceName, Source] = SOURCES,
) -> list[CompanyResult]:
    """Fetch and record each company in turn.

    A company whose board can't be read is reported and skipped. Its
    postings are never passed to the store, so a failed fetch can't mark
    them closed, and the remaining companies still run.
    """
    results: list[CompanyResult] = []
    for company in companies:
        try:
            postings = sources[company.source].fetch(company, client)
        except SourceError as exc:
            results.append(CompanyResult(company, error=exc.reason))
            continue
        board = store.record_board(company, postings, seen_at)
        results.append(CompanyResult(company, fetched=len(postings), board=board))
    return results
