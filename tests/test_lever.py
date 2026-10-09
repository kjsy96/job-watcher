"""Lever fetcher tests.

The fixture is a real response from Lever's own demo board (leverdemo,
the example in Lever's README), captured 2026-10-03 and trimmed to 5
postings. See tests/fixtures/README.md.
"""

import copy
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from jobwatcher.models import Company, Remote, SourceName
from jobwatcher.sources.base import SourceError
from jobwatcher.sources.lever import LeverSource

COMPANY = Company(name="Lever Demo", source=SourceName.LEVER, board="leverdemo")


@pytest.fixture
def payload(load_fixture: Callable[[str], object]) -> list[dict[str, object]]:
    data = load_fixture("lever_postings.json")
    assert isinstance(data, list)
    return data


def test_url_requests_json_without_paging() -> None:
    assert LeverSource().url_for(COMPANY) == "https://api.lever.co/v0/postings/leverdemo?mode=json"


def test_parses_every_posting_in_fixture(payload: list[dict[str, object]]) -> None:
    postings = LeverSource().parse(COMPANY, payload)
    assert [p.title for p in postings] == [
        "Sales Engineer AH",
        "Staff Accountant",
        "Dentist",
        "Senior Sales Engineer",
        "Director of Diversity, Equity, and Inclusion",
    ]


def test_maps_fields_for_one_posting(payload: list[dict[str, object]]) -> None:
    first = LeverSource().parse(COMPANY, payload)[0]

    assert first.id == "lever:leverdemo:58db3f8e-b108-47d1-9e6e-87a1712497cd"
    assert first.source is SourceName.LEVER
    assert first.company == "Lever Demo"
    assert first.url == ("https://jobs.lever.co/leverdemo/58db3f8e-b108-47d1-9e6e-87a1712497cd")
    # createdAt 1788288308535 ms since 1970
    assert first.published_at == datetime(2026, 9, 1, 18, 45, 8, 535000, tzinfo=UTC)


def test_all_locations_are_kept(payload: list[dict[str, object]]) -> None:
    first = LeverSource().parse(COMPANY, payload)[0]
    assert first.location == "Atlanta, Georgia; Arlington, TX; Boston, MA"


def test_falls_back_to_primary_location(payload: list[dict[str, object]]) -> None:
    categories = payload[1]["categories"]
    assert isinstance(categories, dict)
    del categories["allLocations"]
    assert LeverSource().parse(COMPANY, payload)[1].location == "New York, New York"


def test_missing_location_gives_empty_string(payload: list[dict[str, object]]) -> None:
    payload[1]["categories"] = {"team": "Finance"}
    assert LeverSource().parse(COMPANY, payload)[1].location == ""


@pytest.mark.parametrize(
    ("workplace", "expected"),
    [
        ("remote", Remote.YES),
        ("onsite", Remote.NO),  # what the real API sends
        ("on-site", Remote.NO),  # what the docs say
        ("hybrid", Remote.NO),
        ("unspecified", Remote.UNKNOWN),
        ("something-new", Remote.UNKNOWN),
        (None, Remote.UNKNOWN),
    ],
)
def test_remote_comes_from_workplace_type(
    payload: list[dict[str, object]], workplace: str | None, expected: Remote
) -> None:
    payload[0]["workplaceType"] = workplace
    assert LeverSource().parse(COMPANY, payload)[0].remote is expected


def test_fixture_covers_each_real_workplace_type(payload: list[dict[str, object]]) -> None:
    remotes = [p.remote for p in LeverSource().parse(COMPANY, payload)]
    assert remotes == [Remote.YES, Remote.NO, Remote.NO, Remote.NO, Remote.NO]


def test_description_includes_lists_and_closing(payload: list[dict[str, object]]) -> None:
    first = LeverSource().parse(COMPANY, payload)[0]
    text = first.description_text
    # Every lists[] heading reaches the stored text, in order.
    headings = ["What You'll Do", "What We're Looking For", "Nice to Have"]
    positions = [text.index(h) for h in headings]
    assert positions == sorted(positions)
    for leftover in ("<", ">", "&amp;", "&nbsp;", "\N{NO-BREAK SPACE}"):
        assert leftover not in text


def test_description_from_lists_only(payload: list[dict[str, object]]) -> None:
    # The real "Dentist" posting has an empty description; all its content is in lists.
    dentist = LeverSource().parse(COMPANY, payload)[2]
    assert dentist.description_text.startswith("Key Responsibilities")
    assert "Qualifications" in dentist.description_text


def test_list_items_stay_on_separate_lines() -> None:
    raw: list[dict[str, object]] = [
        {
            "id": "abc",
            "text": "Field Engineer",
            "categories": {"location": "Boise, ID"},
            "hostedUrl": "https://jobs.lever.co/x/abc",
            "description": "<div>Intro</div>",
            "lists": [{"text": "Requirements", "content": "<li>Up to 25% travel</li><li>CO</li>"}],
            "additional": "",
        }
    ]
    posting = LeverSource().parse(COMPANY, raw)[0]
    assert posting.description_text == "Intro\nRequirements\nUp to 25% travel\nCO"


def test_empty_posting_content_is_allowed(payload: list[dict[str, object]]) -> None:
    # The real DEI posting has no description, lists, or closing; filters flag it later.
    assert LeverSource().parse(COMPANY, payload)[4].description_text == ""


def test_missing_created_at_gives_none(payload: list[dict[str, object]]) -> None:
    del payload[0]["createdAt"]
    assert LeverSource().parse(COMPANY, payload)[0].published_at is None


def test_empty_board_is_valid() -> None:
    assert LeverSource().parse(COMPANY, []) == []


# --- Shape problems must raise, never return a partial or empty list ---


@pytest.mark.parametrize(
    "bad_payload",
    [{}, {"postings": []}, None, "not a list"],
    ids=["object", "wrapped", "null", "string"],
)
def test_wrong_top_level_shape_raises(bad_payload: object) -> None:
    with pytest.raises(SourceError, match="unexpected response shape"):
        LeverSource().parse(COMPANY, bad_payload)


@pytest.mark.parametrize("field", ["id", "text", "hostedUrl", "description", "lists", "categories"])
def test_posting_missing_required_field_raises(
    payload: list[dict[str, object]], field: str
) -> None:
    del payload[3][field]
    with pytest.raises(SourceError, match=rf"\[3\]\.{field}"):
        LeverSource().parse(COMPANY, payload)


def test_malformed_list_section_raises(payload: list[dict[str, object]]) -> None:
    payload[0]["lists"] = [{"text": "Requirements"}]  # no content
    with pytest.raises(SourceError, match=r"lists\[0\]\.content"):
        LeverSource().parse(COMPANY, payload)


def test_non_string_location_entries_raise(payload: list[dict[str, object]]) -> None:
    payload[0]["categories"] = {"allLocations": ["Boise, ID", 42]}
    with pytest.raises(SourceError, match="allLocations"):
        LeverSource().parse(COMPANY, payload)


@pytest.mark.parametrize("bad", ["2026-09-01", True, 1.5e12])
def test_unreadable_created_at_raises(payload: list[dict[str, object]], bad: object) -> None:
    payload[0]["createdAt"] = bad
    with pytest.raises(SourceError, match="createdAt"):
        LeverSource().parse(COMPANY, payload)


def test_fetch_end_to_end_with_mock_transport(payload: list[dict[str, object]]) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=copy.deepcopy(payload))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    postings = LeverSource().fetch(COMPANY, client)

    assert len(seen) == 1
    assert str(seen[0].url) == LeverSource().url_for(COMPANY)
    assert len(postings) == 5
