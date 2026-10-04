"""SQLite storage for postings: de-duplication, first/last seen, open/closed.

The store is only ever given the result of a fetch that succeeded. A board
whose fetch failed is never passed in, so a network error can never mark
that company's postings as closed. That rule lives with the caller (the
CLI), and is why record_board takes a list of postings rather than doing
any fetching itself.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from jobwatcher.models import Company, Posting, PostingStatus, Remote, SourceName

SCHEMA_VERSION = 1

# STRICT makes SQLite enforce column types (by default it stores whatever it
# is given). The CHECK constraints keep enum columns to their known values,
# so a bug that writes a bad value fails immediately instead of corrupting
# later queries. filter_result and filter_reasons are filled in Phase 2.
_SCHEMA = """
CREATE TABLE postings (
    id               TEXT PRIMARY KEY,
    source           TEXT NOT NULL CHECK (source IN ('greenhouse', 'lever', 'ashby')),
    board            TEXT NOT NULL,
    source_job_id    TEXT NOT NULL,
    company          TEXT NOT NULL,
    title            TEXT NOT NULL,
    location         TEXT NOT NULL,
    remote           TEXT NOT NULL CHECK (remote IN ('yes', 'no', 'unknown')),
    url              TEXT NOT NULL,
    description_text TEXT NOT NULL,
    published_at     TEXT,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    filter_result    TEXT CHECK (filter_result IN ('match', 'flagged', 'possible', 'excluded')),
    filter_reasons   TEXT
) STRICT;

CREATE INDEX postings_by_board ON postings (source, board, status);
"""


class StoreError(Exception):
    """The database can't be used safely, e.g. an unexpected schema version."""


@dataclass(frozen=True, slots=True)
class BoardResult:
    """What one board's update changed. Printed by the fetch summary.

    previously_open is how many postings were open on this board before the
    update. A board that had open postings but returned none is the "zero
    jobs where there used to be some" case, which the run must report.
    """

    new: int
    seen_again: int
    reopened: int
    closed: int
    previously_open: int


@dataclass(frozen=True, slots=True)
class StoredPosting:
    """A posting plus the tracking fields only the store knows."""

    posting: Posting
    first_seen_at: datetime
    last_seen_at: datetime
    status: PostingStatus


def _to_text(moment: datetime) -> str:
    """UTC ISO 8601 with fixed microsecond precision.

    Always converting to UTC is what makes text comparison in SQL (MAX,
    ORDER BY) match time order. Fixed precision keeps every stored value
    the same shape; isoformat() otherwise drops the fraction when it is zero.
    """
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _from_text(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Store:
    """Owns one SQLite connection. Use as a context manager to close it."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    @classmethod
    def open(cls, path: Path | str) -> Self:
        """Open (and if needed create) the database file. ":memory:" also works."""
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # autocommit=False: every change happens inside a transaction that is
        # only saved by an explicit commit, which `with self._conn:` does.
        return cls(sqlite3.connect(path, autocommit=False))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version == SCHEMA_VERSION:
            return
        has_tables = self._conn.execute(
            "SELECT count(*) FROM sqlite_schema WHERE type = 'table'"
        ).fetchone()[0]
        if version != 0 or has_tables:
            # Never guess with someone else's (or an older) database.
            raise StoreError(
                f"database schema version is {version}, expected {SCHEMA_VERSION}; "
                "it was created by a different version of this tool"
            )
        with self._conn:
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    self._conn.execute(statement)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def record_board(
        self, company: Company, postings: list[Posting], seen_at: datetime
    ) -> BoardResult:
        """Record the complete, successful fetch of one company's board.

        All-or-nothing: the whole board is updated in one transaction, so a
        failure midway leaves the database exactly as it was.
        """
        now = _to_text(seen_at)
        fetched_ids: set[str] = set()
        for posting in postings:
            if posting.source != company.source or posting.board != company.board:
                raise ValueError(
                    f"posting {posting.id} does not belong to {company.source}:{company.board}"
                )
            if posting.id in fetched_ids:
                raise ValueError(f"posting {posting.id} appears twice in one fetch")
            fetched_ids.add(posting.id)

        new = seen_again = reopened = 0
        with self._conn:
            existing = {
                str(row["id"]): PostingStatus(row["status"])
                for row in self._conn.execute(
                    "SELECT id, status FROM postings WHERE source = ? AND board = ?",
                    (company.source, company.board),
                )
            }
            previously_open = {
                posting_id
                for posting_id, status in existing.items()
                if status is PostingStatus.OPEN
            }

            for posting in postings:
                status = existing.get(posting.id)
                if status is None:
                    self._insert(posting, now)
                    new += 1
                else:
                    self._update(posting, now)
                    if status is PostingStatus.CLOSED:
                        reopened += 1
                    else:
                        seen_again += 1

            to_close = previously_open - fetched_ids
            self._conn.executemany(
                "UPDATE postings SET status = 'closed' WHERE id = ?",
                [(posting_id,) for posting_id in sorted(to_close)],
            )

        return BoardResult(
            new=new,
            seen_again=seen_again,
            reopened=reopened,
            closed=len(to_close),
            previously_open=len(previously_open),
        )

    def _insert(self, posting: Posting, now: str) -> None:
        self._conn.execute(
            """
            INSERT INTO postings (
                id, source, board, source_job_id, company, title, location, remote,
                url, description_text, published_at, first_seen_at, last_seen_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                posting.id,
                posting.source,
                posting.board,
                posting.source_job_id,
                posting.company,
                posting.title,
                posting.location,
                posting.remote,
                posting.url,
                posting.description_text,
                _to_text(posting.published_at) if posting.published_at else None,
                now,
                now,
            ),
        )

    def _update(self, posting: Posting, now: str) -> None:
        # Content is refreshed because boards edit postings. first_seen_at is
        # never touched: it is what makes a posting "new" exactly once.
        # MAX() keeps last_seen_at from moving backward if the clock does.
        self._conn.execute(
            """
            UPDATE postings SET
                company = ?, title = ?, location = ?, remote = ?, url = ?,
                description_text = ?, published_at = ?,
                last_seen_at = MAX(last_seen_at, ?), status = 'open'
            WHERE id = ?
            """,
            (
                posting.company,
                posting.title,
                posting.location,
                posting.remote,
                posting.url,
                posting.description_text,
                _to_text(posting.published_at) if posting.published_at else None,
                now,
                posting.id,
            ),
        )

    def get(self, posting_id: str) -> StoredPosting | None:
        row = self._conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
        return None if row is None else _stored(row)

    def first_seen_in(self, seen_at: datetime) -> list[StoredPosting]:
        """Postings first seen in the run that used this timestamp: that run's new postings.

        Exact equality works because a run stamps every posting it records
        with the same seen_at, written in one fixed text format.
        """
        rows = self._conn.execute(
            "SELECT * FROM postings WHERE first_seen_at = ? ORDER BY id", (_to_text(seen_at),)
        )
        return [_stored(row) for row in rows]

    def open_postings(self) -> list[StoredPosting]:
        """Every posting currently open on its board, in id order."""
        rows = self._conn.execute("SELECT * FROM postings WHERE status = 'open' ORDER BY id")
        return [_stored(row) for row in rows]

    def set_filter_results(self, results: list[tuple[str, str, list[str]]]) -> None:
        """Record (posting id, outcome, reasons) for each posting, in one transaction.

        Reasons are stored as a JSON list so excluded postings stay reviewable
        later (and readable by the Phase 3 MCP server).
        """
        with self._conn:
            for posting_id, outcome, reasons in results:
                updated = self._conn.execute(
                    "UPDATE postings SET filter_result = ?, filter_reasons = ? WHERE id = ?",
                    (outcome, json.dumps(reasons), posting_id),
                ).rowcount
                if updated != 1:
                    raise ValueError(f"no stored posting with id {posting_id!r}")

    def filter_result(self, posting_id: str) -> tuple[str | None, list[str]]:
        """(outcome, reasons) as stored; (None, []) if not filtered yet."""
        row = self._conn.execute(
            "SELECT filter_result, filter_reasons FROM postings WHERE id = ?", (posting_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no stored posting with id {posting_id!r}")
        reasons = json.loads(row["filter_reasons"]) if row["filter_reasons"] else []
        return (row["filter_result"], [str(r) for r in reasons])

    def count(self, company: Company, status: PostingStatus) -> int:
        row = self._conn.execute(
            "SELECT count(*) FROM postings WHERE source = ? AND board = ? AND status = ?",
            (company.source, company.board, status),
        ).fetchone()
        return int(row[0])


def _stored(row: sqlite3.Row) -> StoredPosting:
    published = row["published_at"]
    posting = Posting(
        source=SourceName(row["source"]),
        board=str(row["board"]),
        source_job_id=str(row["source_job_id"]),
        company=str(row["company"]),
        title=str(row["title"]),
        location=str(row["location"]),
        remote=Remote(row["remote"]),
        url=str(row["url"]),
        description_text=str(row["description_text"]),
        published_at=_from_text(published) if published is not None else None,
    )
    return StoredPosting(
        posting=posting,
        first_seen_at=_from_text(str(row["first_seen_at"])),
        last_seen_at=_from_text(str(row["last_seen_at"])),
        status=PostingStatus(row["status"]),
    )
