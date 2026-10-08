"""Job board detection for discovered employers (issue 2b.3). No network."""

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest

from jobwatcher.detection import (
    SECONDS_BETWEEN_REQUESTS,
    detect_board,
    detect_boards,
    guess_board_name,
    titles_match,
)
from jobwatcher.discovery import AdzunaJob, Candidate, employer_key
from jobwatcher.models import SourceName
from jobwatcher.store import BoardCheck, Store

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
GH = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
LEVER = "https://api.lever.co/v0/postings/{}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{}"


def greenhouse(*titles: str) -> httpx.Response:
    return httpx.Response(200, json={"jobs": [{"title": t} for t in titles], "meta": {}})


def lever(*titles: str) -> httpx.Response:
    return httpx.Response(200, json=[{"text": t} for t in titles])


def ashby(*titles: str) -> httpx.Response:
    return httpx.Response(200, json={"apiVersion": "1", "jobs": [{"title": t} for t in titles]})


class Boards:
    """A fake set of public boards: registered URLs answer, everything else is 404."""

    def __init__(self, routes: dict[str, httpx.Response] | None = None) -> None:
        self.routes = routes or {}
        self.requests: list[str] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        return self.routes.get(str(request.url), httpx.Response(404))


# --- guessing the board name ---


@pytest.mark.parametrize(
    ("employer", "board"),
    [
        ("Heidelberg Materials", "heidelbergmaterials"),
        ("Amazon Data Services, Inc.", "amazondataservices"),
        ("The BIG Jobsite", "bigjobsite"),
        ("Theorem Robotics", "theoremrobotics"),  # "The" only as a separate word
        ("Geek+", "geek"),
        ("Job-Room", "jobroom"),
        ("Siemens AG", "siemens"),
        ("The Company", ""),  # nothing usable left
    ],
)
def test_guess_board_name(employer: str, board: str) -> None:
    assert guess_board_name(employer) == board


# --- matching titles ---


@pytest.mark.parametrize(
    ("board_titles", "ad_titles", "expected"),
    [
        (["Field Engineer"], ["field engineer"], "Field Engineer"),
        (["Field Engineer (Remote)"], ["Field Engineer - Remote"], "Field Engineer (Remote)"),
        # Containment counts once titles are long enough to be specific.
        (
            ["Commissioning Engineer"],
            ["Senior Commissioning Engineer III"],
            "Commissioning Engineer",
        ),
        # Short titles must match exactly: "Engineer" isn't evidence.
        (["Engineer"], ["Field Engineer"], None),
        (["Accountant", "Paralegal"], ["Field Engineer"], None),
        ([], ["Field Engineer"], None),
    ],
)
def test_titles_match(board_titles: list[str], ad_titles: list[str], expected: str | None) -> None:
    assert titles_match(board_titles, ad_titles) == expected


# --- detecting one employer's board ---


def test_confirmed_on_greenhouse_stops_early() -> None:
    boards = Boards({GH.format("acmerobotics"): greenhouse("Field Engineer", "Accountant")})
    result = detect_board("Acme Robotics", ["Field Engineer"], boards.client, NOW, lambda _: None)

    assert result.error is None and result.requests == 1
    assert result.check == BoardCheck(
        employer_key("Acme Robotics"),
        "Acme Robotics",
        "confirmed",
        SourceName.GREENHOUSE,
        "acmerobotics",
        "board has 'Field Engineer'",
        NOW,
    )


def test_possible_board_does_not_stop_the_search_for_a_confirmed_one() -> None:
    boards = Boards(
        {
            GH.format("acmerobotics"): greenhouse("Accountant"),  # a different "Acme Robotics"?
            ASHBY.format("acmerobotics"): ashby("Field Engineer"),
        }
    )
    result = detect_board("Acme Robotics", ["Field Engineer"], boards.client, NOW, lambda _: None)

    assert result.check is not None
    assert (result.check.status, result.check.source) == ("confirmed", SourceName.ASHBY)
    assert result.requests == 3


def test_board_without_matching_titles_is_only_possible() -> None:
    boards = Boards({LEVER.format("acmerobotics"): lever("Accountant", "Paralegal")})
    result = detect_board("Acme Robotics", ["Field Engineer"], boards.client, NOW, lambda _: None)

    assert result.check is not None
    assert result.check.status == "possible"
    assert result.check.source is SourceName.LEVER
    assert result.check.detail == "board exists with 2 jobs, none matching the Adzuna ads"


def test_empty_board_is_possible() -> None:
    boards = Boards({ASHBY.format("acmerobotics"): ashby()})
    result = detect_board("Acme Robotics", ["Field Engineer"], boards.client, NOW, lambda _: None)
    assert result.check is not None
    assert (result.check.status, result.check.detail) == (
        "possible",
        "board exists but has no open jobs",
    )


def test_no_board_anywhere_is_not_found_after_three_requests() -> None:
    boards = Boards()
    pauses: list[float] = []
    result = detect_board("Acme Robotics", ["Field Engineer"], boards.client, NOW, pauses.append)

    assert result.check is not None
    assert result.check.status == "not_found"
    assert result.check.detail == "no Greenhouse, Lever, or Ashby board named 'acmerobotics'"
    assert boards.requests == [
        GH.format("acmerobotics"),
        LEVER.format("acmerobotics"),
        ASHBY.format("acmerobotics"),
    ]
    assert pauses == [SECONDS_BETWEEN_REQUESTS, SECONDS_BETWEEN_REQUESTS]


def test_unusable_name_is_not_found_without_requests() -> None:
    boards = Boards()
    result = detect_board("The Company", ["Field Engineer"], boards.client, NOW, lambda _: None)
    assert result.check is not None and result.check.status == "not_found"
    assert boards.requests == [] and result.requests == 0


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(500), "greenhouse: HTTP 500"),
        (httpx.Response(429), "greenhouse: HTTP 429"),
        (httpx.Response(200, text="<html>"), "greenhouse: unexpected response"),
        (httpx.Response(200, json={"oops": 1}), "greenhouse: unexpected response"),
    ],
)
def test_failed_lookup_is_an_error_not_a_result(response: httpx.Response, error: str) -> None:
    boards = Boards({GH.format("acmerobotics"): response})
    result = detect_board("Acme Robotics", ["Field Engineer"], boards.client, NOW, lambda _: None)
    assert result.check is None  # nothing to remember; retried next run
    assert result.error == error


def test_network_failure_is_an_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    result = detect_board("Acme Robotics", ["x"], client, NOW, lambda _: None)
    assert (result.check, result.error) == (None, "greenhouse: request failed: ConnectError")


# --- checking many candidates: cache and per-run limit ---


@pytest.fixture
def store() -> Iterator[Store]:
    with Store.open(":memory:") as s:
        yield s


def candidate(name: str, title: str = "Field Engineer") -> Candidate:
    ad = AdzunaJob(name, title, "Boise", "us", "", "", "")
    return Candidate(name, employer_key(name), [ad], ["mining"])


def test_limit_spends_lookups_on_the_top_ranked_and_remembers_them(store: Store) -> None:
    boards = Boards({GH.format("first"): greenhouse("Field Engineer")})
    ranked = [candidate("First"), candidate("Second"), candidate("Third")]

    summary = detect_boards(ranked, store, boards.client, NOW, limit=2, pause=lambda _: None)

    assert (summary.checked, summary.from_earlier, summary.not_checked) == (2, 0, 1)
    assert summary.requests == 1 + 3  # First confirmed at once; Second tried all three
    assert ranked[0].board is not None and ranked[0].board.status == "confirmed"
    assert ranked[1].board is not None and ranked[1].board.status == "not_found"
    assert ranked[2].board is None  # left for the next run
    assert store.board_check("first") is not None and store.board_check("third") is None


def test_remembered_employers_are_never_looked_up_again(store: Store) -> None:
    store.record_board_check(
        BoardCheck("first", "First", "not_found", None, None, "checked last week", NOW)
    )
    boards = Boards()
    ranked = [candidate("First"), candidate("Second")]

    summary = detect_boards(ranked, store, boards.client, NOW, limit=1, pause=lambda _: None)

    assert (summary.checked, summary.from_earlier, summary.not_checked) == (1, 1, 0)
    assert ranked[0].board is not None and ranked[0].board.detail == "checked last week"
    assert all("/first" not in url for url in boards.requests)


def test_failed_lookups_are_not_remembered(store: Store) -> None:
    boards = Boards({GH.format("first"): httpx.Response(503)})
    ranked = [candidate("First")]

    summary = detect_boards(ranked, store, boards.client, NOW, limit=5, pause=lambda _: None)

    assert summary.errors == ["First: greenhouse: HTTP 503"]
    assert ranked[0].board is None and ranked[0].board_error == "greenhouse: HTTP 503"
    assert store.board_check("first") is None  # tried again next run


def test_limit_zero_checks_nothing(store: Store) -> None:
    boards = Boards()
    summary = detect_boards([candidate("First")], store, boards.client, NOW, 0, lambda _: None)
    assert (summary.checked, summary.not_checked, boards.requests) == (0, 1, [])
