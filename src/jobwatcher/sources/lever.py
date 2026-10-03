"""Lever Postings API. See docs/sources.md for the confirmed endpoint and fields."""

from datetime import UTC, datetime
from typing import override

from jobwatcher.models import Company, Posting, Remote, SourceName
from jobwatcher.sources.base import Source, SourceError
from jobwatcher.sources.payload import (
    ShapeError,
    require_dict,
    require_id,
    require_list,
    require_str,
)
from jobwatcher.sources.text import html_to_text

API_BASE = "https://api.lever.co/v0/postings"

# The docs list "on-site", but real responses send "onsite". Both are
# accepted so neither spelling silently becomes "unknown". Hybrid means
# regular office attendance, so it is not remote.
_REMOTE_BY_WORKPLACE = {
    "remote": Remote.YES,
    "onsite": Remote.NO,
    "on-site": Remote.NO,
    "hybrid": Remote.NO,
}


class LeverSource(Source):
    name = SourceName.LEVER

    @override
    def url_for(self, company: Company) -> str:
        # No skip/limit: one request returned all 319 postings on a large
        # board (docs/sources.md), so paging is not needed.
        return f"{API_BASE}/{company.board}?mode=json"

    @override
    def parse(self, company: Company, payload: object) -> list[Posting]:
        try:
            postings = require_list(payload, "response")
            return [self._posting(company, raw, f"[{i}]") for i, raw in enumerate(postings)]
        except (ShapeError, ValueError) as exc:
            # ValueError also covers a Posting rejecting its own fields.
            raise SourceError(company, f"unexpected response shape: {exc}") from exc

    def _posting(self, company: Company, raw: object, where: str) -> Posting:
        job = require_dict(raw, where)
        categories = require_dict(job.get("categories"), f"{where}.categories")
        workplace = job.get("workplaceType")

        return Posting(
            source=self.name,
            board=company.board,
            source_job_id=require_id(job, "id", where),
            company=company.name,
            title=require_str(job, "text", where).strip(),
            location=_location(categories, f"{where}.categories"),
            remote=_REMOTE_BY_WORKPLACE.get(workplace, Remote.UNKNOWN)
            if isinstance(workplace, str)
            else Remote.UNKNOWN,
            url=require_str(job, "hostedUrl", where),
            description_text=_description(job, where),
            published_at=_created_at(job, where),
        )


def _location(categories: dict[str, object], where: str) -> str:
    """Every listed location, so the filters can pick the best tier.

    allLocations includes the primary location. It is joined with "; ",
    the same separator Greenhouse uses for multi-location postings.
    """
    all_locations = categories.get("allLocations")
    if all_locations is not None:
        names = require_list(all_locations, f"{where}.allLocations")
        if all(isinstance(n, str) for n in names):
            joined = "; ".join(n.strip() for n in names if isinstance(n, str) and n.strip())
            if joined:
                return joined
        else:
            raise ShapeError(f"{where}.allLocations: expected a list of strings")
    primary = categories.get("location")
    return primary.strip() if isinstance(primary, str) else ""


def _description(job: dict[str, object], where: str) -> str:
    """Opening and body, every list section, then the closing section.

    Lever keeps responsibilities and requirements in ``lists``, outside
    ``description``. Travel and duty wording often lives there, so leaving
    them out would hide it from the filters. The HTML fields are converted
    with the same html_to_text used for every source, so stored text is
    consistent (Lever's own *Plain fields keep non-breaking spaces).
    """
    parts = [html_to_text(require_str(job, "description", where))]

    for i, raw in enumerate(require_list(job.get("lists"), f"{where}.lists")):
        section = require_dict(raw, f"{where}.lists[{i}]")
        heading = require_str(section, "text", f"{where}.lists[{i}]").strip()
        body = html_to_text(require_str(section, "content", f"{where}.lists[{i}]"))
        parts.extend([heading, body])

    additional = job.get("additional")
    if isinstance(additional, str):
        parts.append(html_to_text(additional))

    return "\n".join(part for part in parts if part)


def _created_at(job: dict[str, object], where: str) -> datetime | None:
    """createdAt is milliseconds since 1970 (undocumented, present in practice)."""
    value = job.get("createdAt")
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    raise ShapeError(f"{where}.createdAt: expected milliseconds since 1970, got {value!r}")
