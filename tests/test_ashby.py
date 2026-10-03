"""Ashby fetcher tests.

The fixture is a real response from Ashby's own job board (the example
in Ashby's docs), captured 2026-10-03 and trimmed to 4 jobs with
shortened descriptions. Jobs naming people were left out. See
tests/fixtures/README.md.
"""

import copy
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from jobwatcher.models import Company, Remote, SourceName
from jobwatcher.sources.ashby import AshbySource
from jobwatcher.sources.base import SourceError

COMPANY = Company(name="Ashby", source=SourceName.ASHBY, board="Ashby")


@pytest.fixture
def payload(load_fixture: Callable[[str], object]) -> dict[str, object]:
    data = load_fixture("ashby_jobs.json")
    assert isinstance(data, dict)
    return data


def jobs_of(payload: dict[str, object]) -> list[dict[str, object]]:
    jobs = payload["jobs"]
    assert isinstance(jobs, list)
    return jobs


def test_url_has_no_compensation_parameter() -> None:
    assert AshbySource().url_for(COMPANY) == "https://api.ashbyhq.com/posting-api/job-board/Ashby"


def test_parses_every_job_in_fixture(payload: dict[str, object]) -> None:
    titles = [p.title for p in AshbySource().parse(COMPANY, payload)]
    assert titles == [
        "Product Support Specialist - Australia",
        "Product Support Specialist - EMEA",
        "Enterprise Account Executive - Americas (East)",
        "GTM Systems Analyst",
    ]


def test_maps_fields_for_one_job(payload: dict[str, object]) -> None:
    first = AshbySource().parse(COMPANY, payload)[0]

    assert first.id == "ashby:Ashby:55078a90-f1f0-4190-9d2a-c14d1cbc5486"
    assert first.source is SourceName.ASHBY
    assert first.company == "Ashby"
    assert first.location == "Australia"
    assert first.url == "https://jobs.ashbyhq.com/Ashby/55078a90-f1f0-4190-9d2a-c14d1cbc5486"
    assert first.published_at == datetime(2026, 2, 20, 17, 26, 27, 704000, tzinfo=UTC)


def test_id_matches_job_url_in_real_data(payload: dict[str, object]) -> None:
    # The undocumented id was equal to jobUrl's last segment on all 62 real jobs.
    for posting in AshbySource().parse(COMPANY, payload):
        assert posting.url.endswith("/" + posting.source_job_id)


def test_secondary_locations_are_appended(payload: dict[str, object]) -> None:
    analyst = AshbySource().parse(COMPANY, payload)[3]
    assert analyst.location == "Remote - US; Remote - European Union; Remote - Canada; Ireland"


def test_duplicate_secondary_location_is_not_repeated(payload: dict[str, object]) -> None:
    job = jobs_of(payload)[0]
    job["secondaryLocations"] = [{"location": "Australia"}, {"location": "New Zealand"}]
    assert AshbySource().parse(COMPANY, payload)[0].location == "Australia; New Zealand"


def test_description_comes_from_html(payload: dict[str, object]) -> None:
    for posting in AshbySource().parse(COMPANY, payload):
        text = posting.description_text
        assert text
        for leftover in ("<p", "</", "&amp;", "&nbsp;", "\N{NO-BREAK SPACE}"):
            assert leftover not in text


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        # Real Ashby text: a literal "<" written as &lt;.
        (
            "<p>Engineers are in &lt;2h meetings per week.</p>",
            "Engineers are in <2h meetings per week.",
        ),
        # A literal "<" before a letter. Unescaping first (as Greenhouse needs)
        # would turn this into a tag and silently drop the words.
        ("<p>Email &lt;team lead&gt; with questions.</p>", "Email <team lead> with questions."),
    ],
)
def test_escaped_text_is_not_unescaped_twice(
    payload: dict[str, object], html: str, expected: str
) -> None:
    jobs_of(payload)[0]["descriptionHtml"] = html
    assert AshbySource().parse(COMPANY, payload)[0].description_text == expected


@pytest.mark.parametrize(
    ("workplace", "is_remote", "expected"),
    [
        ("Remote", True, Remote.YES),
        ("OnSite", False, Remote.NO),
        ("Hybrid", False, Remote.NO),
        ("Hybrid", True, Remote.NO),  # workplaceType wins
        (None, True, Remote.YES),
        (None, False, Remote.UNKNOWN),  # can't tell on-site from hybrid
        (None, None, Remote.UNKNOWN),
        ("SomethingNew", None, Remote.UNKNOWN),
    ],
)
def test_remote_mapping(
    payload: dict[str, object],
    workplace: str | None,
    is_remote: bool | None,
    expected: Remote,
) -> None:
    job = jobs_of(payload)[0]
    job["workplaceType"] = workplace
    job["isRemote"] = is_remote
    assert AshbySource().parse(COMPANY, payload)[0].remote is expected


def test_unlisted_jobs_are_skipped(payload: dict[str, object]) -> None:
    jobs_of(payload)[1]["isListed"] = False
    titles = [p.title for p in AshbySource().parse(COMPANY, payload)]
    assert "Product Support Specialist - EMEA" not in titles
    assert len(titles) == 3


def test_missing_is_listed_counts_as_listed(payload: dict[str, object]) -> None:
    del jobs_of(payload)[1]["isListed"]
    assert len(AshbySource().parse(COMPANY, payload)) == 4


def test_missing_published_at_gives_none(payload: dict[str, object]) -> None:
    del jobs_of(payload)[0]["publishedAt"]
    assert AshbySource().parse(COMPANY, payload)[0].published_at is None


def test_empty_board_is_valid() -> None:
    assert AshbySource().parse(COMPANY, {"apiVersion": "1", "jobs": []}) == []


# --- Shape problems must raise, never return a partial or empty list ---


@pytest.mark.parametrize(
    "bad_payload",
    [[], {"apiVersion": "1"}, {"jobs": None}, "error"],
    ids=["list", "no-jobs-key", "jobs-null", "string"],
)
def test_wrong_top_level_shape_raises(bad_payload: object) -> None:
    with pytest.raises(SourceError, match="unexpected response shape"):
        AshbySource().parse(COMPANY, bad_payload)


@pytest.mark.parametrize("field", ["id", "title", "jobUrl", "descriptionHtml"])
def test_job_missing_required_field_raises(payload: dict[str, object], field: str) -> None:
    del jobs_of(payload)[2][field]
    with pytest.raises(SourceError, match=rf"jobs\[2\]\.{field}"):
        AshbySource().parse(COMPANY, payload)


def test_missing_id_never_falls_back_to_title(payload: dict[str, object]) -> None:
    # Two postings can share a title, so a title-based ID could merge them.
    del jobs_of(payload)[0]["id"]
    with pytest.raises(SourceError, match="expected a job ID"):
        AshbySource().parse(COMPANY, payload)


def test_malformed_secondary_location_raises(payload: dict[str, object]) -> None:
    jobs_of(payload)[0]["secondaryLocations"] = [{"address": {}}]
    with pytest.raises(SourceError, match=r"secondaryLocations\[0\]\.location"):
        AshbySource().parse(COMPANY, payload)


def test_unreadable_published_at_raises(payload: dict[str, object]) -> None:
    jobs_of(payload)[0]["publishedAt"] = "2026-02-20"  # no timezone
    with pytest.raises(SourceError, match="publishedAt"):
        AshbySource().parse(COMPANY, payload)


def test_fetch_end_to_end_with_mock_transport(payload: dict[str, object]) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=copy.deepcopy(payload))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    postings = AshbySource().fetch(COMPANY, client)

    assert len(seen) == 1
    assert str(seen[0].url) == AshbySource().url_for(COMPANY)
    assert len(postings) == 4
