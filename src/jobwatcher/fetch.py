"""The fetch step: every company's board, fetched once and recorded."""

import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TextIO

import httpx

from jobwatcher import __version__
from jobwatcher.models import Company, PostingStatus, SourceName
from jobwatcher.sources import SOURCES
from jobwatcher.sources.base import Source, SourceError
from jobwatcher.store import BoardResult, Store

USER_AGENT = f"job-watcher/{__version__} (+https://github.com/kjsy96/job-watcher)"
TIMEOUT_SECONDS = 30.0


class Outcome(StrEnum):
    OK = "ok"
    # Answered, but with nothing while postings were open. Not recorded.
    WARNING = "warning"
    # Could not be read, or something went wrong handling it. Not recorded.
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CompanyResult:
    """The outcome for one company: recorded counts, or why it wasn't recorded."""

    company: Company
    outcome: Outcome
    fetched: int | None = None
    board: BoardResult | None = None
    message: str | None = None
    network_error: bool = False  # failed with no HTTP response (see SourceError)


def looks_offline(results: list[CompanyResult]) -> bool:
    """True when every company failed without any HTTP response.

    An HTTP error (404, 503) proves the network works, so it never counts.
    """
    return bool(results) and all(r.network_error for r in results)


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
    errors: TextIO | None = None,
) -> list[CompanyResult]:
    """Fetch and record each company in turn. One company never stops another.

    A company is only recorded when its board was read successfully and the
    result is believable. Everything else is reported and leaves the
    database untouched for that company, so its postings are never marked
    closed because of a failure.
    """
    results: list[CompanyResult] = []
    for company in companies:
        try:
            results.append(_fetch_one(company, store, client, seen_at, sources))
        except Exception as exc:
            # Not a board problem we anticipated, e.g. a bug in a parser or
            # the store rejecting the data. Report it, keep the traceback for
            # debugging, and move on. KeyboardInterrupt is not an Exception,
            # so Ctrl+C still stops the run.
            if errors is not None:
                print(f"Unexpected error for {company.name}:", file=errors)
                traceback.print_exception(exc, file=errors)
            results.append(
                CompanyResult(
                    company,
                    Outcome.FAILED,
                    message=f"unexpected error: {type(exc).__name__}: {exc}",
                )
            )
    return results


def _fetch_one(
    company: Company,
    store: Store,
    client: httpx.Client,
    seen_at: datetime,
    sources: Mapping[SourceName, Source],
) -> CompanyResult:
    try:
        postings = sources[company.source].fetch(company, client)
    except SourceError as exc:
        return CompanyResult(company, Outcome.FAILED, message=exc.reason, network_error=exc.network)

    if not postings:
        open_before = store.count(company, PostingStatus.OPEN)
        if open_before:
            # "Zero jobs where there used to be some." Usually the board moved
            # or broke, not that every job closed at once. Recording it would
            # close every posting and look like "no new jobs", so flag it and
            # leave the database as it was.
            return CompanyResult(
                company,
                Outcome.WARNING,
                fetched=0,
                message=(
                    f"returned 0 jobs but {open_before} were open; not recorded, "
                    "check the board or the config"
                ),
            )

    board = store.record_board(company, postings, seen_at)
    return CompanyResult(company, Outcome.OK, fetched=len(postings), board=board)
