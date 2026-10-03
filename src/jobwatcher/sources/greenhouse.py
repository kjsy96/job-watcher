"""Greenhouse Job Board API. See docs/sources.md for the confirmed endpoint and fields."""

import html
import re
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

API_BASE = "https://boards-api.greenhouse.io/v1/boards"

# Greenhouse has no remote field, so only location text can say "remote".
# Anything else is unknown, not "no": "Denver, CO" doesn't rule out remote.
_REMOTE_WORD = re.compile(r"\bremote\b", re.IGNORECASE)


class GreenhouseSource(Source):
    name = SourceName.GREENHOUSE

    @override
    def url_for(self, company: Company) -> str:
        # content=true is required: without it the list has no descriptions.
        return f"{API_BASE}/{company.board}/jobs?content=true"

    @override
    def parse(self, company: Company, payload: object) -> list[Posting]:
        try:
            return self._parse(company, payload)
        except (ShapeError, ValueError) as exc:
            # ValueError also covers a Posting rejecting its own fields.
            raise SourceError(company, f"unexpected response shape: {exc}") from exc

    def _parse(self, company: Company, payload: object) -> list[Posting]:
        body = require_dict(payload, "response")
        jobs = require_list(body.get("jobs"), "response.jobs")

        # meta.total is the board's own job count. If it disagrees with the
        # list we received, the response was cut short and must not be
        # treated as complete.
        meta = body.get("meta")
        if isinstance(meta, dict):
            total = meta.get("total")
            if isinstance(total, int) and total != len(jobs):
                raise ShapeError(f"meta.total is {total} but {len(jobs)} jobs were returned")

        return [self._posting(company, job, f"jobs[{i}]") for i, job in enumerate(jobs)]

    def _posting(self, company: Company, raw: object, where: str) -> Posting:
        job = require_dict(raw, where)
        location_obj = job.get("location")
        location = ""
        if isinstance(location_obj, dict):
            name = location_obj.get("name")
            location = name.strip() if isinstance(name, str) else ""

        # Greenhouse entity-escapes the description HTML, sometimes twice
        # (&amp;nbsp;). Unescape once to get real HTML; the HTML parser then
        # decodes whatever entities remain inside the text.
        content = html.unescape(require_str(job, "content", where))

        return Posting(
            source=self.name,
            board=company.board,
            source_job_id=require_id(job, "id", where),
            company=company.name,
            title=require_str(job, "title", where).strip(),
            location=location,
            remote=Remote.YES if _REMOTE_WORD.search(location) else Remote.UNKNOWN,
            url=require_str(job, "absolute_url", where),
            description_text=html_to_text(content),
            # Undocumented in the list response but present in practice
            # (docs/sources.md). Optional, so its absence is not an error.
            published_at=optional_datetime(job, "first_published", where),
        )
