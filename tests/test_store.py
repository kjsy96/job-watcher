import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jobwatcher.models import Company, Posting, PostingStatus, Remote, SourceName
from jobwatcher.store import SCHEMA_VERSION, BoardResult, Store, StoreError

ACME = Company(name="Acme Robotics", source=SourceName.GREENHOUSE, board="acme")
OTHER = Company(name="Other Co", source=SourceName.LEVER, board="otherco")

DAY1 = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
DAY2 = DAY1 + timedelta(days=1)
DAY3 = DAY1 + timedelta(days=2)


def posting(job_id: str, company: Company = ACME, **overrides: object) -> Posting:
    fields: dict[str, object] = {
        "source": company.source,
        "board": company.board,
        "source_job_id": job_id,
        "company": company.name,
        "title": f"Implementation Engineer {job_id}",
        "location": "Denver, CO",
        "remote": Remote.UNKNOWN,
        "url": f"https://example.test/{company.board}/{job_id}",
        "description_text": "Commissioning at mining sites.",
        "published_at": None,
    }
    fields.update(overrides)
    return Posting(**fields)  # type: ignore[arg-type]


@pytest.fixture
def store() -> Iterator[Store]:
    with Store.open(":memory:") as s:
        yield s


def status_of(store: Store, posting_id: str) -> PostingStatus:
    stored = store.get(posting_id)
    assert stored is not None
    return stored.status


# --- new, seen again, de-duplication ---


def test_first_run_records_everything_as_new(store: Store) -> None:
    result = store.record_board(ACME, [posting("1"), posting("2")], DAY1)
    assert result == BoardResult(new=2, seen_again=0, reopened=0, closed=0, previously_open=0)


def test_second_identical_run_finds_nothing_new(store: Store) -> None:
    store.record_board(ACME, [posting("1"), posting("2")], DAY1)
    result = store.record_board(ACME, [posting("1"), posting("2")], DAY2)
    assert result == BoardResult(new=0, seen_again=2, reopened=0, closed=0, previously_open=2)


def test_first_seen_is_kept_and_last_seen_moves(store: Store) -> None:
    store.record_board(ACME, [posting("1")], DAY1)
    store.record_board(ACME, [posting("1")], DAY2)

    stored = store.get("greenhouse:acme:1")
    assert stored is not None
    assert stored.first_seen_at == DAY1
    assert stored.last_seen_at == DAY2
    assert stored.status is PostingStatus.OPEN


def test_round_trip_keeps_every_field(store: Store) -> None:
    original = posting(
        "1",
        title="Field Engineer",
        location="Remote, US",
        remote=Remote.YES,
        published_at=datetime(2026, 9, 30, 12, 0, tzinfo=UTC),
    )
    store.record_board(ACME, [original], DAY1)
    stored = store.get(original.id)
    assert stored is not None
    assert stored.posting == original


def test_edited_posting_gets_latest_content(store: Store) -> None:
    store.record_board(ACME, [posting("1", title="Old title")], DAY1)
    store.record_board(ACME, [posting("1", title="New title")], DAY2)

    stored = store.get("greenhouse:acme:1")
    assert stored is not None
    assert stored.posting.title == "New title"
    assert stored.first_seen_at == DAY1


def test_unknown_id_returns_none(store: Store) -> None:
    assert store.get("greenhouse:acme:404") is None


# --- closing and reopening ---


def test_missing_posting_is_closed_not_deleted(store: Store) -> None:
    store.record_board(ACME, [posting("1"), posting("2")], DAY1)
    result = store.record_board(ACME, [posting("1")], DAY2)

    assert result.closed == 1
    assert status_of(store, "greenhouse:acme:2") is PostingStatus.CLOSED
    stored = store.get("greenhouse:acme:2")
    assert stored is not None
    assert stored.last_seen_at == DAY1  # when it was last actually seen


def test_closed_posting_that_returns_is_reopened(store: Store) -> None:
    store.record_board(ACME, [posting("1")], DAY1)
    store.record_board(ACME, [], DAY2)
    result = store.record_board(ACME, [posting("1")], DAY3)

    assert result == BoardResult(new=0, seen_again=0, reopened=1, closed=0, previously_open=0)
    stored = store.get("greenhouse:acme:1")
    assert stored is not None
    assert stored.status is PostingStatus.OPEN
    assert stored.first_seen_at == DAY1  # reopening is not "new"


def test_empty_fetch_closes_all_and_reports_previously_open(store: Store) -> None:
    # The "zero jobs where there used to be some" signal for issue 1.8.
    store.record_board(ACME, [posting("1"), posting("2")], DAY1)
    result = store.record_board(ACME, [], DAY2)
    assert result.closed == 2
    assert result.previously_open == 2


def test_already_closed_postings_are_not_closed_again(store: Store) -> None:
    store.record_board(ACME, [posting("1")], DAY1)
    store.record_board(ACME, [], DAY2)
    result = store.record_board(ACME, [], DAY3)
    assert result == BoardResult(new=0, seen_again=0, reopened=0, closed=0, previously_open=0)


# --- boards are independent ---


def test_one_board_never_touches_another(store: Store) -> None:
    store.record_board(ACME, [posting("1")], DAY1)
    store.record_board(OTHER, [posting("1", OTHER)], DAY1)

    store.record_board(ACME, [], DAY2)  # Acme emptied

    assert status_of(store, "greenhouse:acme:1") is PostingStatus.CLOSED
    assert status_of(store, "lever:otherco:1") is PostingStatus.OPEN
    assert store.count(OTHER, PostingStatus.OPEN) == 1


def test_same_job_number_on_two_boards_are_separate(store: Store) -> None:
    store.record_board(ACME, [posting("7")], DAY1)
    result = store.record_board(OTHER, [posting("7", OTHER)], DAY1)
    assert result.new == 1


def test_count_by_status(store: Store) -> None:
    store.record_board(ACME, [posting("1"), posting("2"), posting("3")], DAY1)
    store.record_board(ACME, [posting("1")], DAY2)
    assert store.count(ACME, PostingStatus.OPEN) == 1
    assert store.count(ACME, PostingStatus.CLOSED) == 2


# --- bad input is rejected, and nothing is written ---


def test_posting_from_another_board_is_rejected(store: Store) -> None:
    with pytest.raises(ValueError, match="does not belong"):
        store.record_board(ACME, [posting("1"), posting("2", OTHER)], DAY1)
    assert store.get("greenhouse:acme:1") is None


def test_duplicate_id_in_one_fetch_is_rejected(store: Store) -> None:
    with pytest.raises(ValueError, match="appears twice"):
        store.record_board(ACME, [posting("1"), posting("1")], DAY1)


def test_naive_seen_at_is_rejected(store: Store) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        store.record_board(ACME, [posting("1")], datetime(2026, 10, 1, 7, 0))


def test_failure_midway_rolls_back_the_whole_board(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.record_board(ACME, [posting("1"), posting("2")], DAY1)

    def fail(*_: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    # Day 2: posting 3 is new (inserted first), then updating posting 1 fails.
    monkeypatch.setattr(store, "_update", fail)
    with pytest.raises(sqlite3.OperationalError):
        store.record_board(ACME, [posting("3"), posting("1")], DAY2)

    # Nothing from the failed run was kept: no new row, no closures.
    assert store.get("greenhouse:acme:3") is None
    assert status_of(store, "greenhouse:acme:2") is PostingStatus.OPEN
    assert store.count(ACME, PostingStatus.OPEN) == 2


# --- timestamps ---


def test_last_seen_never_moves_backward(store: Store) -> None:
    store.record_board(ACME, [posting("1")], DAY2)
    store.record_board(ACME, [posting("1")], DAY1)  # clock went back
    stored = store.get("greenhouse:acme:1")
    assert stored is not None
    assert stored.last_seen_at == DAY2


def test_timestamps_are_stored_in_one_fixed_format(store: Store) -> None:
    # isoformat() drops ".000000" when microseconds are zero; the store
    # always writes the full form, in UTC.
    store.record_board(ACME, [posting("1")], datetime(2026, 10, 1, 7, 0, 0, tzinfo=UTC))
    row = store._conn.execute("SELECT first_seen_at FROM postings").fetchone()
    assert row[0] == "2026-10-01T07:00:00.000000+00:00"


def test_last_seen_compares_by_time_across_time_zones(store: Store) -> None:
    from datetime import timezone

    # 07:00 UTC, then 02:00 in UTC-6 (= 08:00 UTC, later), then 06:00 UTC.
    # As raw local text, "02:00" would sort first; stored as UTC it doesn't.
    store.record_board(ACME, [posting("1")], DAY1)
    later = datetime(2026, 10, 1, 2, 0, tzinfo=timezone(timedelta(hours=-6)))
    store.record_board(ACME, [posting("1")], later)
    store.record_board(ACME, [posting("1")], datetime(2026, 10, 1, 6, 0, tzinfo=UTC))
    stored = store.get("greenhouse:acme:1")
    assert stored is not None
    assert stored.last_seen_at == datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def test_non_utc_times_are_stored_as_utc(store: Store) -> None:
    from datetime import timezone

    denver = timezone(timedelta(hours=-6))
    store.record_board(ACME, [posting("1")], datetime(2026, 10, 1, 1, 0, tzinfo=denver))
    stored = store.get("greenhouse:acme:1")
    assert stored is not None
    assert stored.first_seen_at == datetime(2026, 10, 1, 7, 0, tzinfo=UTC)


# --- database file and schema ---


def test_data_survives_reopening_the_file(tmp_path: Path) -> None:
    db = tmp_path / "data" / "jobwatcher.db"  # parent folder doesn't exist yet
    with Store.open(db) as first:
        first.record_board(ACME, [posting("1")], DAY1)
    with Store.open(db) as second:
        assert second.record_board(ACME, [posting("1")], DAY2).new == 0


def test_schema_constraints_reject_bad_values(store: Store) -> None:
    store.record_board(ACME, [posting("1")], DAY1)
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE postings SET status = 'archived'")


def test_unexpected_schema_version_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "jobwatcher.db"
    with Store.open(db):
        pass
    conn = sqlite3.connect(db)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()

    with pytest.raises(StoreError, match="schema version"):
        Store.open(db)


def test_unrelated_database_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "something_else.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE notes (body TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(StoreError, match="schema version is 0"):
        Store.open(db)


def test_replace_keeps_posting_identity() -> None:
    # Sanity check for the helper used above: changing content keeps the ID.
    assert replace(posting("1"), title="X").id == posting("1").id


# --- new postings and filter results (issue 2.5) ---


def test_first_seen_in_returns_only_that_runs_new_postings(store: Store) -> None:
    store.record_board(ACME, [posting("1"), posting("2")], DAY1)
    store.record_board(ACME, [posting("1"), posting("2"), posting("3")], DAY2)

    assert [s.posting.source_job_id for s in store.first_seen_in(DAY1)] == ["1", "2"]
    assert [s.posting.source_job_id for s in store.first_seen_in(DAY2)] == ["3"]
    assert store.first_seen_in(DAY3) == []


def test_first_seen_in_matches_the_same_moment_in_any_time_zone(store: Store) -> None:
    from datetime import timezone

    local = DAY1.astimezone(timezone(timedelta(hours=11)))
    store.record_board(ACME, [posting("1")], local)
    assert len(store.first_seen_in(DAY1)) == 1  # same instant, UTC


def test_reopened_posting_is_not_new(store: Store) -> None:
    store.record_board(ACME, [posting("1")], DAY1)
    store.record_board(ACME, [], DAY2)
    store.record_board(ACME, [posting("1")], DAY3)
    assert store.first_seen_in(DAY3) == []


def test_filter_results_round_trip(store: Store) -> None:
    store.record_board(ACME, [posting("1"), posting("2")], DAY1)
    assert store.filter_result("greenhouse:acme:1") == (None, [])

    # Non-English text in a reason must survive the JSON round trip.
    reasons = [
        "title matches no role term",
        "location: 'Montr\N{LATIN SMALL LETTER E WITH ACUTE}al'",
    ]
    store.set_filter_results(
        [("greenhouse:acme:1", "excluded", reasons), ("greenhouse:acme:2", "match", [])]
    )

    assert store.filter_result("greenhouse:acme:1") == ("excluded", reasons)
    assert store.filter_result("greenhouse:acme:2") == ("match", [])


def test_filter_result_rejects_unknown_outcome(store: Store) -> None:
    store.record_board(ACME, [posting("1")], DAY1)
    with pytest.raises(sqlite3.IntegrityError):
        store.set_filter_results([("greenhouse:acme:1", "maybe", [])])


def test_filter_results_for_unknown_posting_write_nothing(store: Store) -> None:
    store.record_board(ACME, [posting("1")], DAY1)
    with pytest.raises(ValueError, match="no stored posting"):
        store.set_filter_results(
            [("greenhouse:acme:1", "match", []), ("greenhouse:acme:404", "match", [])]
        )
    assert store.filter_result("greenhouse:acme:1") == (None, [])  # rolled back
    with pytest.raises(ValueError, match="no stored posting"):
        store.filter_result("greenhouse:acme:404")
