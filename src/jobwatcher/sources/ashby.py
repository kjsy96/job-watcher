"""Ashby public job posting API. See docs/sources.md for the confirmed endpoint and fields."""

from typing import override

from jobwatcher.models import Company, Posting, Remote, SourceName
from jobwatcher.sources.base import Source, SourceError
from jobwatcher.sources.payload import (
    ShapeError,
    optional_datetime,
    require_dict,
    require_id,
    require_list,
    require_str,
)
from jobwatcher.sources.text import html_to_text

API_BASE = "https://api.ashbyhq.com/posting-api/job-board"

# Hybrid means regular office attendance, so it is not remote (same as Lever).
_REMOTE_BY_WORKPLACE = {
    "Remote": Remote.YES,
    "OnSite": Remote.NO,
    "Hybrid": Remote.NO,
}


class AshbySource(Source):
    name = SourceName.ASHBY

    @override
    def url_for(self, company: Company) -> str:
        # includeCompensation is left off: pay data isn't used by the filters,
        # and leaving it off keeps the response smaller.
        return f"{API_BASE}/{company.board}"

    @override
    def parse(self, company: Company, payload: object) -> list[Posting]:
        try:
            body = require_dict(payload, "response")
            jobs = require_list(body.get("jobs"), "response.jobs")
            postings: list[Posting] = []
            for i, raw in enumerate(jobs):
                job = require_dict(raw, f"jobs[{i}]")
                # Unlisted jobs are hidden from the company's own board and only
                # reachable by direct link, so they are not part of the board.
                if job.get("isListed") is False:
                    continue
                postings.append(self._posting(company, job, f"jobs[{i}]"))
            return postings
        except (ShapeError, ValueError) as exc:
            # ValueError also covers a Posting rejecting its own fields.
            raise SourceError(company, f"unexpected response shape: {exc}") from exc

    def _posting(self, company: Company, job: dict[str, object], where: str) -> Posting:
        return Posting(
            source=self.name,
            board=company.board,
            # Undocumented, but present on every real job and equal to the last
            # segment of jobUrl (docs/sources.md). Required: if it ever
            # disappears, fetching fails loudly instead of guessing an ID.
            source_job_id=require_id(job, "id", where),
            company=company.name,
            title=require_str(job, "title", where).strip(),
            location=_location(job, where),
            remote=_remote(job),
            url=require_str(job, "jobUrl", where),
            # descriptionHtml is real HTML, not entity-escaped like Greenhouse.
            # An "&lt;" in it is a literal "<" in the text ("&lt;2h meetings"),
            # so it must NOT be unescaped before parsing.
            description_text=html_to_text(require_str(job, "descriptionHtml", where)),
            published_at=optional_datetime(job, "publishedAt", where),
        )


def _location(job: dict[str, object], where: str) -> str:
    """Primary location, then each secondary location, joined with "; "."""
    names: list[str] = []
    primary = job.get("location")
    if isinstance(primary, str) and primary.strip():
        names.append(primary.strip())

    secondary = job.get("secondaryLocations")
    if secondary is not None:
        for i, raw in enumerate(require_list(secondary, f"{where}.secondaryLocations")):
            entry = require_dict(raw, f"{where}.secondaryLocations[{i}]")
            name = require_str(entry, "location", f"{where}.secondaryLocations[{i}]").strip()
            if name and name not in names:
                names.append(name)

    return "; ".join(names)


def _remote(job: dict[str, object]) -> Remote:
    """workplaceType first, since it distinguishes hybrid; then isRemote.

    isRemote=False alone gives unknown, not "no": without workplaceType it
    can't tell on-site from hybrid, and the filters treat those differently.
    """
    workplace = job.get("workplaceType")
    if isinstance(workplace, str) and workplace in _REMOTE_BY_WORKPLACE:
        return _REMOTE_BY_WORKPLACE[workplace]
    if job.get("isRemote") is True:
        return Remote.YES
    return Remote.UNKNOWN
