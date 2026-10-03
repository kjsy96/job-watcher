"""Issue 1.8: every failure is reported, and one company never stops another.

Each test puts one misbehaving board between two healthy ones (the
GitLab and Lever demo fixtures) and checks that the healthy ones were
still recorded and the bad one was reported and left untouched.
"""

import io
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from jobwatcher.fetch import CompanyResult, Outcome, fetch_all
from jobwatcher.models import Company, Posting, PostingStatus, SourceName
from jobwatcher.sources import SOURCES
from jobwatcher.sources.base import Source
from jobwatcher.store import Store

FIXTURES = Path(__file__).parent / "fixtures"

GITLAB = Company("GitLab", SourceName.GREENHOUSE, "gitlab")
LEVER = Company("Lever Demo", SourceName.LEVER, "leverdemo")
ASHBY = Company("Ashby", SourceName.ASHBY, "Ashby")

FIXTURE_FOR = {
    GITLAB: "greenhouse_jobs.json",
    LEVER: "lever_postings.json",
    ASHBY: "ashby_jobs.json",
}

DAY1 = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
DAY2 = DAY1 + timedelta(days=1)
DAY3 = DAY1 + timedelta(days=2)

Behavior = Callable[[httpx.Request], httpx.Response]


def fixture_json(company: Company) -> object:
    return json.loads((FIXTURES / FIXTURE_FOR[company]).read_text(encoding="utf-8"))


def serve_fixture(company: Company) -> Behavior:
    return lambda _: httpx.Response(200, json=fixture_json(company))


def client_for(behaviors: dict[Company, Behavior]) -> httpx.Client:
    """A client whose answer for each company's URL is chosen by the test."""
    by_url = {SOURCES[c.source].url_for(c): b for c, b in behaviors.items()}

    def handle(request: httpx.Request) -> httpx.Response:
        return by_url[str(request.url)](request)

    return httpx.Client(transport=httpx.MockTransport(handle))


@pytest.fixture
def store() -> Iterator[Store]:
    with Store.open(":memory:") as s:
        yield s


def run(
    store: Store,
    middle: Behavior,
    seen_at: datetime = DAY1,
    sources: dict[SourceName, Source] | None = None,
    errors: io.StringIO | None = None,
) -> list[CompanyResult]:
    """GitLab (healthy), then Ashby (behaves as given), then Lever (healthy)."""
    client = client_for({GITLAB: serve_fixture(GITLAB), ASHBY: middle, LEVER: serve_fixture(LEVER)})
    return fetch_all(
        [GITLAB, ASHBY, LEVER], store, client, seen_at, sources=sources or SOURCES, errors=errors
    )


def assert_neighbors_recorded(results: list[CompanyResult], store: Store) -> None:
    gitlab, _, lever = results
    assert gitlab.outcome is Outcome.OK and gitlab.board is not None and gitlab.board.new == 4
    assert lever.outcome is Outcome.OK and lever.board is not None and lever.board.new == 5
    assert store.count(GITLAB, PostingStatus.OPEN) == 4
    assert store.count(LEVER, PostingStatus.OPEN) == 5


def raising(exc_type: type[httpx.TransportError], message: str) -> Behavior:
    def behave(request: httpx.Request) -> httpx.Response:
        raise exc_type(message, request=request)

    return behave


# --- board failures: reported, not recorded, neighbors unaffected ---


@pytest.mark.parametrize(
    ("behavior", "expected"),
    [
        (raising(httpx.ConnectTimeout, "timed out"), "ConnectTimeout"),
        (raising(httpx.ReadTimeout, "timed out"), "ReadTimeout"),
        (raising(httpx.ConnectError, "connection refused"), "ConnectError"),
        (lambda _: httpx.Response(404), "HTTP 404"),
        (lambda _: httpx.Response(429), "HTTP 429"),
        (lambda _: httpx.Response(500), "HTTP 500"),
        (lambda _: httpx.Response(503), "HTTP 503"),
        (lambda _: httpx.Response(200, text="<html>Down for maintenance</html>"), "not valid JSON"),
        (lambda _: httpx.Response(200, json={"error": "gone"}), "unexpected response shape"),
        (lambda _: httpx.Response(200, json={"jobs": [{}]}), "unexpected response shape"),
    ],
    ids=[
        "connect-timeout",
        "read-timeout",
        "connection-refused",
        "404",
        "429",
        "500",
        "503",
        "html",
        "wrong-shape",
        "job-missing-fields",
    ],
)
def test_board_failure_is_reported_and_neighbors_still_run(
    store: Store, behavior: Behavior, expected: str
) -> None:
    results = run(store, behavior)

    failed = results[1]
    assert failed.company == ASHBY
    assert failed.outcome is Outcome.FAILED
    assert failed.message is not None and expected in failed.message
    assert failed.board is None
    assert store.count(ASHBY, PostingStatus.OPEN) == 0
    assert_neighbors_recorded(results, store)


def test_failure_after_a_good_run_leaves_postings_open(store: Store) -> None:
    run(store, serve_fixture(ASHBY), DAY1)
    run(store, lambda _: httpx.Response(500), DAY2)

    assert store.count(ASHBY, PostingStatus.OPEN) == 4
    assert store.count(ASHBY, PostingStatus.CLOSED) == 0


def test_every_company_failing_still_reports_each_one(store: Store) -> None:
    client = client_for({c: (lambda _: httpx.Response(503)) for c in (GITLAB, ASHBY, LEVER)})
    results = fetch_all([GITLAB, ASHBY, LEVER], store, client, DAY1)

    assert [r.company for r in results] == [GITLAB, ASHBY, LEVER]
    assert all(r.outcome is Outcome.FAILED and r.message == "HTTP 503" for r in results)


# --- "zero jobs where there used to be some" ---


def empty_ashby(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"apiVersion": "1", "jobs": []})


def test_empty_board_with_open_postings_is_a_warning_and_not_recorded(store: Store) -> None:
    run(store, serve_fixture(ASHBY), DAY1)

    results = run(store, empty_ashby, DAY2)

    warned = results[1]
    assert warned.outcome is Outcome.WARNING
    assert warned.fetched == 0
    assert warned.board is None
    assert warned.message is not None and "returned 0 jobs but 4 were open" in warned.message
    # Nothing was closed: the database is as it was after day 1.
    assert store.count(ASHBY, PostingStatus.OPEN) == 4
    assert store.count(ASHBY, PostingStatus.CLOSED) == 0
    stored = store.get("ashby:Ashby:55078a90-f1f0-4190-9d2a-c14d1cbc5486")
    assert stored is not None and stored.last_seen_at == DAY1


def test_board_that_comes_back_after_a_warning_is_seen_again(store: Store) -> None:
    run(store, serve_fixture(ASHBY), DAY1)
    run(store, empty_ashby, DAY2)
    results = run(store, serve_fixture(ASHBY), DAY3)

    board = results[1].board
    assert results[1].outcome is Outcome.OK
    assert board is not None
    assert (board.new, board.reopened, board.seen_again) == (0, 0, 4)


def test_empty_board_with_nothing_open_is_ok(store: Store) -> None:
    # A newly added company with no openings yet is not suspicious.
    results = run(store, empty_ashby, DAY1)

    assert results[1].outcome is Outcome.OK
    assert results[1].fetched == 0
    assert_neighbors_recorded(results, store)


def test_empty_board_after_every_posting_closed_normally_is_ok(store: Store) -> None:
    # Postings that disappeared one by one were closed normally; once none
    # are open, an empty board is just an empty board.
    store.record_board(ASHBY, [], DAY1)
    assert run(store, empty_ashby, DAY2)[1].outcome is Outcome.OK


# --- unexpected errors are isolated too ---


class CrashingSource(Source):
    """A source with a bug: parse() raises something other than SourceError."""

    name = SourceName.ASHBY

    def url_for(self, company: Company) -> str:
        return SOURCES[SourceName.ASHBY].url_for(company)

    def parse(self, company: Company, payload: object) -> list[Posting]:
        raise KeyError("descriptionHtml")


def test_unexpected_error_is_reported_with_traceback_and_neighbors_run(store: Store) -> None:
    errors = io.StringIO()
    sources: dict[SourceName, Source] = {**SOURCES, SourceName.ASHBY: CrashingSource()}

    results = run(store, serve_fixture(ASHBY), sources=sources, errors=errors)

    crashed = results[1]
    assert crashed.outcome is Outcome.FAILED
    assert crashed.message == "unexpected error: KeyError: 'descriptionHtml'"
    assert "Unexpected error for Ashby:" in errors.getvalue()
    assert "Traceback" in errors.getvalue()
    assert_neighbors_recorded(results, store)


def test_store_rejecting_a_fetch_is_isolated(store: Store) -> None:
    # Two jobs with the same id: the store refuses the whole board.
    data = fixture_json(ASHBY)
    assert isinstance(data, dict) and isinstance(data["jobs"], list)
    data["jobs"][1]["id"] = data["jobs"][0]["id"]

    results = run(store, lambda _: httpx.Response(200, json=data))

    assert results[1].outcome is Outcome.FAILED
    assert results[1].message is not None and "appears twice in one fetch" in results[1].message
    assert store.count(ASHBY, PostingStatus.OPEN) == 0
    assert_neighbors_recorded(results, store)


def test_keyboard_interrupt_still_stops_the_run(store: Store) -> None:
    def interrupt(_: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(store, interrupt)
