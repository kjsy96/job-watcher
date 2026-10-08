"""Tests for the shared fetch() logic, using httpx's MockTransport.

MockTransport answers requests with a local function, so these tests make
no network calls.
"""

from collections.abc import Callable

import httpx
import pytest

from jobwatcher.models import Company, Posting, Remote, SourceName
from jobwatcher.sources.base import Source, SourceError

COMPANY = Company(name="Example Co", source=SourceName.GREENHOUSE, board="exampleco")


class FakeSource(Source):
    """Minimal Source that records what parse() received."""

    name = SourceName.GREENHOUSE

    def __init__(self) -> None:
        self.parsed: list[object] = []

    def url_for(self, company: Company) -> str:
        return f"https://example.test/boards/{company.board}/jobs"

    def parse(self, company: Company, payload: object) -> list[Posting]:
        self.parsed.append(payload)
        return [
            Posting(
                source=self.name,
                board=company.board,
                source_job_id="1",
                company=company.name,
                title="Implementation Engineer",
                location="Boise, ID",
                remote=Remote.UNKNOWN,
                url="https://example.test/jobs/1",
                description_text="",
            )
        ]


def client_returning(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(record)), seen


def test_fetch_makes_one_get_to_the_source_url_and_parses_json() -> None:
    client, seen = client_returning(lambda _: httpx.Response(200, json={"jobs": []}))
    source = FakeSource()

    postings = source.fetch(COMPANY, client)

    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert str(seen[0].url) == "https://example.test/boards/exampleco/jobs"
    assert source.parsed == [{"jobs": []}]
    assert [p.id for p in postings] == ["greenhouse:exampleco:1"]


@pytest.mark.parametrize("status", [404, 429, 500, 503])
def test_http_error_status_raises_source_error(status: int) -> None:
    client, _ = client_returning(lambda _: httpx.Response(status))
    source = FakeSource()

    with pytest.raises(SourceError, match=f"HTTP {status}") as exc:
        source.fetch(COMPANY, client)

    assert exc.value.company == COMPANY
    assert source.parsed == []  # never parsed, never mistaken for "no jobs"


def test_timeout_raises_source_error() -> None:
    def time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client, _ = client_returning(time_out)

    with pytest.raises(SourceError, match="ReadTimeout"):
        FakeSource().fetch(COMPANY, client)


def test_connection_failure_raises_source_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, _ = client_returning(refuse)

    with pytest.raises(SourceError, match="ConnectError"):
        FakeSource().fetch(COMPANY, client)


def test_non_json_body_raises_source_error() -> None:
    client, _ = client_returning(lambda _: httpx.Response(200, text="<html>Maintenance</html>"))

    with pytest.raises(SourceError, match="not valid JSON"):
        FakeSource().fetch(COMPANY, client)


def test_source_error_message_names_the_company() -> None:
    error = SourceError(COMPANY, "HTTP 404")
    assert str(error) == "Example Co (greenhouse:exampleco): HTTP 404"


def test_fetch_rejects_company_from_another_platform() -> None:
    lever_company = Company(name="Lever Co", source=SourceName.LEVER, board="leverco")
    client, seen = client_returning(lambda _: httpx.Response(200, json=[]))

    with pytest.raises(ValueError, match="cannot fetch a lever company"):
        FakeSource().fetch(lever_company, client)
    assert seen == []  # no request sent


def test_source_cannot_be_instantiated_without_parse() -> None:
    class Incomplete(Source):
        name = SourceName.LEVER

        def url_for(self, company: Company) -> str:
            return ""

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]
