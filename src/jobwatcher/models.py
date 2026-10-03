"""Core data types shared by every stage: fetch, store, filter, report."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SourceName(StrEnum):
    """Job board platforms this tool can read.

    A StrEnum member is also a plain string, so it can be written straight
    into SQLite or TOML and compared against config values without
    conversion, while typos in code are still caught by mypy.
    """

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"


class Remote(StrEnum):
    """Whether a posting is remote.

    Three values, not a bool, because "the board didn't say" is a real
    answer (Greenhouse has no remote field at all). Collapsing it into
    False would be guessing, which the design principles forbid.
    """

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Company:
    """One target company, as listed in companies.toml."""

    name: str
    source: SourceName
    board: str  # the company's identifier on that platform, e.g. its board slug
    sector: str = ""


@dataclass(frozen=True, slots=True)
class Posting:
    """One job posting, normalized to the same shape for every source.

    Holds only what a job board returns. Tracking fields (first/last seen,
    open/closed) are added by the store, and filter results by the rule
    engine, so a Posting means the same thing no matter which stage holds it.

    frozen=True makes instances immutable: once a fetcher builds a posting,
    no later stage can change it by accident.
    """

    source: SourceName
    board: str
    source_job_id: str
    company: str
    title: str
    location: str  # raw text as published
    remote: Remote
    url: str
    description_text: str  # HTML already stripped
    published_at: datetime | None = None  # None when the board doesn't provide it

    def __post_init__(self) -> None:
        # Fail loudly on unusable data instead of storing a broken row.
        for field_name in ("board", "source_job_id", "title", "url"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"Posting.{field_name} must not be empty")
        if self.published_at is not None and self.published_at.tzinfo is None:
            # A naive datetime can't be compared safely with first_seen_at.
            raise ValueError("Posting.published_at must be timezone-aware")

    @property
    def id(self) -> str:
        """Stable key across runs: ``{source}:{board}:{source_job_id}``.

        Including source and board means two platforms (or two companies)
        that happen to reuse the same job number can never collide.
        """
        return f"{self.source}:{self.board}:{self.source_job_id}"
