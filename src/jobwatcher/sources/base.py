"""The interface every job board source implements.

Fetching (one HTTP request) and parsing (JSON to Postings) are separate
methods on purpose. parse() is a pure function, so tests feed it saved
JSON fixtures and never touch the network. fetch() is written once here,
so every source gets identical request and error handling.
"""

from abc import ABC, abstractmethod
from typing import ClassVar

import httpx

from jobwatcher.models import Company, Posting, SourceName


class SourceError(Exception):
    """A company's board could not be read or its response could not be used.

    Every failure mode (network error, HTTP error status, non-JSON body,
    unexpected shape) becomes this one type, so the run summary can catch
    it per company and report it. A failure must never look like an empty
    job list.

    network is True when no HTTP response arrived at all (DNS failure,
    refused connection, timeout). If every company fails that way, the
    machine is offline rather than the boards being broken.
    """

    def __init__(self, company: Company, reason: str, *, network: bool = False) -> None:
        self.company = company
        self.reason = reason
        self.network = network
        super().__init__(f"{company.name} ({company.source}:{company.board}): {reason}")


class Source(ABC):
    """Base class for a job board platform.

    Subclasses set ``name`` and implement ``url_for`` and ``parse``.
    """

    name: ClassVar[SourceName]

    @abstractmethod
    def url_for(self, company: Company) -> str:
        """The single public endpoint that returns every job on this board."""

    @abstractmethod
    def parse(self, company: Company, payload: object) -> list[Posting]:
        """Turn a decoded JSON response into Postings.

        ``payload`` is typed as ``object`` rather than a specific shape
        because it comes from the network and can be anything. Each
        implementation must check the shape it relies on and raise
        SourceError when it doesn't match.
        """

    def fetch(self, company: Company, client: httpx.Client) -> list[Posting]:
        """Make one GET request for the company's board and parse the result.

        The client is passed in rather than created here. The caller owns
        timeouts and the user agent in one place, and tests pass a client
        with a fake transport so nothing reaches the network.
        """
        if company.source != self.name:
            # A programming error (wrong source chosen), not a board failure.
            raise ValueError(f"{type(self).__name__} cannot fetch a {company.source} company")
        try:
            response = client.get(self.url_for(company))
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SourceError(company, f"HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            # Timeouts, DNS failures, refused connections, and similar.
            raise SourceError(
                company, f"request failed: {type(exc).__name__}: {exc}", network=True
            ) from exc
        try:
            payload: object = response.json()
        except ValueError as exc:
            raise SourceError(company, "response was not valid JSON") from exc
        return self.parse(company, payload)
