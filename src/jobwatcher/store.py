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
from datetime import UTC, date, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from jobwatcher.models import Company, Posting, PostingStatus, Remote, SourceName

SCHEMA_VERSION = 3

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


# Each later schema version is reached by running its migration once.
# A brand-new database is built the same way (version 1 schema, then every
# migration in order), so a fresh database and a migrated one are identical.
_MIGRATIONS: dict[int, str] = {
    # Version 2 (issue 2.6): one row per counted daily run, so "already ran
    # today" survives restarts and the run history is queryable later.
    2: """
CREATE TABLE runs (
    run_at      TEXT NOT NULL,
    local_date  TEXT NOT NULL,
    outcome     TEXT NOT NULL CHECK (outcome IN ('ok', 'partial')),
    report_path TEXT NOT NULL
) STRICT;

CREATE INDEX runs_by_date ON runs (local_date);
""",
    # Version 3 (issue 2b.3): job board detection results for discovered
    # employers, so no employer's board is looked up twice. Only definite
    # answers are stored; a failed request is retried on a later run.
    3: """
CREATE TABLE board_checks (
    employer_key  TEXT PRIMARY KEY,
    employer_name TEXT NOT NULL,
    checked_at    TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('confirmed', 'possible', 'not_found')),
    source        TEXT CHECK (source IN ('greenhouse', 'lever', 'ashby')),
    board         TEXT,
    detail        TEXT NOT NULL
) STRICT;
""",
}


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
class BoardCheck:
    """Where a discovered employer's job board was found, if anywhere (issue 2b.3).

    status: 'confirmed' (a board job title matches one of the employer's ads),
    'possible' (a board exists under the guessed name, but no title matches),
    or 'not_found' (no board under the guessed name on any platform).
    """

    employer_key: str
    employer_name: str
    status: str
    source: SourceName | None
    board: str | None
    detail: str
    checked_at: datetime


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
        if (version == 0 and has_tables) or version > SCHEMA_VERSION or version < 0:
            # Never guess with someone else's (or a newer) database.
            raise StoreError(
                f"database schema version is {version}, expected {SCHEMA_VERSION}; "
                "it was created by a different version of this tool"
            )
        # All-or-nothing: a failed migration leaves the database at its old
        # version, untouched.
        with self._conn:
            if version == 0:
                _run_script(self._conn, _SCHEMA)
                version = 1
            for target in range(version + 1, SCHEMA_VERSION + 1):
                _run_script(self._conn, _MIGRATIONS[target])
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def record_run(self, run_at: datetime, outcome: str, report_path: str) -> None:
        """Record a counted daily run: 'ok', or 'partial' if some companies failed."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs (run_at, local_date, outcome, report_path) VALUES (?, ?, ?, ?)",
                (_to_text(run_at), f"{run_at:%Y-%m-%d}", outcome, report_path),
            )

    def run_on(self, local_date: date) -> tuple[str, str] | None:
        """(outcome, report path) of the latest counted run on that local date."""
        row = self._conn.execute(
            "SELECT outcome, report_path FROM runs WHERE local_date = ? "
            "ORDER BY run_at DESC LIMIT 1",
            (f"{local_date:%Y-%m-%d}",),
        ).fetchone()
        return None if row is None else (str(row["outcome"]), str(row["report_path"]))

    def board_check(self, employer_key: str) -> BoardCheck | None:
        """The stored job board detection for an employer, if it was checked."""
        row = self._conn.execute(
            "SELECT * FROM board_checks WHERE employer_key = ?", (employer_key,)
        ).fetchone()
        if row is None:
            return None
        return BoardCheck(
            employer_key=str(row["employer_key"]),
            employer_name=str(row["employer_name"]),
            status=str(row["status"]),
            source=SourceName(row["source"]) if row["source"] else None,
            board=str(row["board"]) if row["board"] else None,
            detail=str(row["detail"]),
            checked_at=_from_text(str(row["checked_at"])),
        )

    def record_board_check(self, check: BoardCheck) -> None:
        """Store a definite detection result. Rechecking replaces the old one."""
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO board_checks (employer_key, employer_name, checked_at, "
                "status, source, board, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    check.employer_key,
                    check.employer_name,
                    _to_text(check.checked_at),
                    check.status,
                    check.source,
                    check.board,
                    check.detail,
                ),
            )

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


def _run_script(conn: sqlite3.Connection, script: str) -> None:
    # Statement by statement (not executescript), so it stays inside the
    # caller's transaction and rolls back with it.
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)
