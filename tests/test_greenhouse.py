"""Greenhouse fetcher tests.

The fixture is a real response from GitLab's public board, captured
2026-10-03 and trimmed to 4 jobs with shortened descriptions. See
tests/fixtures/README.md.
"""

import copy
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from jobwatcher.models import Company, Remote, SourceName
from jobwatcher.sources.base import SourceError
from jobwatcher.sources.greenhouse import GreenhouseSource

COMPANY = Company(name="GitLab", source=SourceName.GREENHOUSE, board="gitlab")


@pytest.fixture
def payload(load_fixture: Callable[[str], object]) -> dict[str, object]:
    data = load_fixture("greenhouse_jobs.json")
    assert isinstance(data, dict)
    return data


def jobs_of(payload: dict[str, object]) -> list[dict[str, object]]:
    jobs = payload["jobs"]
    assert isinstance(jobs, list)
    return jobs


def test_url_requests_content() -> None:
    assert (
        GreenhouseSource().url_for(COMPANY)
        == "https://boards-api.greenhouse.io/v1/boards/gitlab/jobs?content=true"
    )


def test_parses_every_job_in_fixture(payload: dict[str, object]) -> None:
    postings = GreenhouseSource().parse(COMPANY, payload)
    assert [p.source_job_id for p in postings] == [
        "8638232002",
        "8857185002",
        "8626772002",
        "8790102002",
    ]


def test_maps_fields_for_one_job(payload: dict[str, object]) -> None:
    first = GreenhouseSource().parse(COMPANY, payload)[0]

    assert first.id == "greenhouse:gitlab:8638232002"
    assert first.source is SourceName.GREENHOUSE
    assert first.company == "GitLab"  # from our config, not the response
    assert first.title == "AI Transformation Owner, CRO"
    assert first.location == "Remote, United States"
    assert first.url == "https://job-boards.greenhouse.io/gitlab/jobs/8638232002"
    assert first.published_at == datetime(2026, 7, 22, 17, 38, 40, tzinfo=UTC)


def test_title_whitespace_is_trimmed(payload: dict[str, object]) -> None:
    # The real response has trailing spaces on this title.
    second = GreenhouseSource().parse(COMPANY, payload)[1]
    assert second.title == "Associate Renewals Manager"


def test_description_is_unescaped_and_stripped(payload: dict[str, object]) -> None:
    for posting in GreenhouseSource().parse(COMPANY, payload):
        text = posting.description_text
        assert text.startswith("GitLab is the intelligent orchestration platform")
        # No tags or entities survive, including the double-escaped ones.
        for leftover in ("<", ">", "&lt;", "&gt;", "&amp;", "&nbsp;", "&quot;", "&#39;"):
            assert leftover not in text, f"{leftover!r} left in {posting.id}"
        assert "\N{NO-BREAK SPACE}" not in text


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Remote, United States", Remote.YES),
        ("Remote", Remote.YES),
        ("remote - EMEA", Remote.YES),
        ("Bangalore, India", Remote.UNKNOWN),
        ("Remoteville, TX", Remote.UNKNOWN),  # whole word only
        ("", Remote.UNKNOWN),
    ],
)
def test_remote_comes_only_from_location_text(
    payload: dict[str, object], location: str, expected: Remote
) -> None:
    jobs_of(payload)[0]["location"] = {"name": location}
    assert GreenhouseSource().parse(COMPANY, payload)[0].remote is expected


def test_missing_location_gives_empty_string(payload: dict[str, object]) -> None:
    del jobs_of(payload)[0]["location"]
    posting = GreenhouseSource().parse(COMPANY, payload)[0]
    assert posting.location == ""
    assert posting.remote is Remote.UNKNOWN


def test_missing_first_published_gives_none(payload: dict[str, object]) -> None:
    del jobs_of(payload)[0]["first_published"]
    assert GreenhouseSource().parse(COMPANY, payload)[0].published_at is None


def test_empty_board_is_valid() -> None:
    assert GreenhouseSource().parse(COMPANY, {"jobs": [], "meta": {"total": 0}}) == []


# --- Shape problems must raise, never return a partial or empty list ---


@pytest.mark.parametrize(
    "bad_payload",
    [[], {"error": "not found"}, {"jobs": None}, {"jobs": {"id": 1}}],
    ids=["list", "no-jobs-key", "jobs-null", "jobs-object"],
)
def test_wrong_top_level_shape_raises(bad_payload: object) -> None:
    with pytest.raises(SourceError, match="unexpected response shape"):
        GreenhouseSource().parse(COMPANY, bad_payload)


def test_meta_total_mismatch_raises(payload: dict[str, object]) -> None:
    payload["meta"] = {"total": 211}
    with pytest.raises(SourceError, match=r"meta\.total is 211 but 4 jobs"):
        GreenhouseSource().parse(COMPANY, payload)


@pytest.mark.parametrize("field", ["id", "title", "absolute_url", "content"])
def test_job_missing_required_field_raises(payload: dict[str, object], field: str) -> None:
    del jobs_of(payload)[2][field]
    with pytest.raises(SourceError, match=rf"jobs\[2\]\.{field}"):
        GreenhouseSource().parse(COMPANY, payload)


def test_blank_title_raises(payload: dict[str, object]) -> None:
    jobs_of(payload)[0]["title"] = "   "
    with pytest.raises(SourceError, match="title must not be empty"):
        GreenhouseSource().parse(COMPANY, payload)


def test_unreadable_first_published_raises(payload: dict[str, object]) -> None:
    jobs_of(payload)[0]["first_published"] = "last Tuesday"
    with pytest.raises(SourceError, match="first_published"):
        GreenhouseSource().parse(COMPANY, payload)


def test_string_id_is_accepted_and_bool_id_rejected(payload: dict[str, object]) -> None:
    jobs = jobs_of(payload)
    jobs[0]["id"] = "abc-123"
    assert GreenhouseSource().parse(COMPANY, payload)[0].source_job_id == "abc-123"

    jobs[0]["id"] = True
    with pytest.raises(SourceError, match="expected a job ID"):
        GreenhouseSource().parse(COMPANY, payload)


def test_fetch_end_to_end_with_mock_transport(payload: dict[str, object]) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=copy.deepcopy(payload))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    postings = GreenhouseSource().fetch(COMPANY, client)

    assert len(seen) == 1
    assert str(seen[0].url) == GreenhouseSource().url_for(COMPANY)
    assert len(postings) == 4
