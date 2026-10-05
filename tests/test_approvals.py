"""Discovery report, approve, and reject (issue 2b.4). No network."""

import io
import tomllib
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from jobwatcher.__main__ import EXIT_OK, EXIT_UNUSABLE, run_approve, run_reject
from jobwatcher.approvals import approve, reject
from jobwatcher.config import ConfigError, load_companies
from jobwatcher.detection import DetectionSummary
from jobwatcher.discovery import (
    AdzunaJob,
    Candidate,
    DiscoveryConfig,
    DiscoveryResult,
    SearchBlock,
    employer_key,
    known_employer_names,
)
from jobwatcher.discovery_report import render_discovery_report
from jobwatcher.matching import Terms
from jobwatcher.models import Company, SourceName
from jobwatcher.store import BoardCheck, Store

TODAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)

COMPANIES = """# My real targets. Comments must survive approvals.
[[company]]
name = "Existing Co"
source = "lever"
board = "existing"
"""


def check(
    name: str,
    status: str,
    source: SourceName | None = None,
    board: str | None = None,
    detail: str = "detail",
) -> BoardCheck:
    return BoardCheck(employer_key(name), name, status, source, board, detail, NOW)


@pytest.fixture
def store() -> Iterator[Store]:
    with Store.open(":memory:") as s:
        s.record_board_check(
            check(
                "Mine Tech", "confirmed", SourceName.ASHBY, "minetech", "board has 'Field Engineer'"
            )
        )
        s.record_board_check(check("Maybe Co", "possible", SourceName.GREENHOUSE, "maybeco"))
        s.record_board_check(check("Workday Corp", "not_found", detail="no board named 'workday'"))
        yield s


@pytest.fixture
def companies(tmp_path: Path) -> Path:
    path = tmp_path / "companies.toml"
    path.write_text(COMPANIES, encoding="utf-8")
    return path


# --- approve ---


def test_approve_confirmed_board_appends_and_keeps_comments(store: Store, companies: Path) -> None:
    approval = approve("Mine Tech", companies, store, TODAY)

    assert (approval.name, approval.source, approval.board) == (
        "Mine Tech",
        SourceName.ASHBY,
        "minetech",
    )
    assert approval.warning is None
    text = companies.read_text(encoding="utf-8")
    assert text.startswith("# My real targets. Comments must survive approvals.")
    assert (
        "# Added by 'jobwatcher approve' on 2026-10-05 "
        "(board confirmed: board has 'Field Engineer')" in text
    )
    assert load_companies(companies)[-1] == Company("Mine Tech", SourceName.ASHBY, "minetech", "")


def test_approve_matches_names_loosely(store: Store, companies: Path) -> None:
    # Typed differently from the report, same employer.
    assert approve("MINE TECH, Inc.", companies, store, TODAY).name == "Mine Tech"


def test_approve_possible_board_warns(store: Store, companies: Path) -> None:
    approval = approve("Maybe Co", companies, store, TODAY)
    assert approval.warning is not None and "only a possible match" in approval.warning


def test_approve_with_explicit_board_for_not_found(store: Store, companies: Path) -> None:
    approval = approve(
        "Workday Corp", companies, store, TODAY, source=SourceName.LEVER, board="workdaycorp"
    )
    assert (approval.source, approval.board) == (SourceName.LEVER, "workdaycorp")
    assert "(board given by the owner)" in companies.read_text(encoding="utf-8")


def test_approve_sets_a_sector_only_when_given(store: Store, companies: Path) -> None:
    approve("Mine Tech", companies, store, TODAY, sector="mining tech")
    assert load_companies(companies)[-1].sector == "mining tech"


@pytest.mark.parametrize(
    ("name", "kwargs", "message"),
    [
        ("Existing Co", {}, "already in"),
        ("Unknown Co", {}, "no job board lookup is remembered"),
        ("Workday Corp", {}, "no readable board was found"),
        ("Mine Tech", {"source": SourceName.LEVER}, "both --source and --board"),
        (
            "Mine Tech",
            {"source": SourceName.LEVER, "board": "existing"},
            "lever:existing is already used by 'Existing Co'",
        ),
    ],
)
def test_approve_refusals_change_nothing(
    store: Store, companies: Path, name: str, kwargs: dict[str, object], message: str
) -> None:
    before = companies.read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        approve(name, companies, store, TODAY, **kwargs)  # type: ignore[arg-type]
    assert companies.read_text(encoding="utf-8") == before


def test_approve_rolls_back_if_the_file_would_become_invalid(store: Store, companies: Path) -> None:
    # A board name with a slash is rejected by the company config validator.
    before = companies.read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match="left unchanged"):
        approve("New Co", companies, store, TODAY, source=SourceName.LEVER, board="bad/board")
    assert companies.read_text(encoding="utf-8") == before


def test_approve_quotes_and_backslashes_are_escaped(store: Store, companies: Path) -> None:
    name = 'Quote "Q" \\ Co'
    approve(name, companies, store, TODAY, source=SourceName.LEVER, board="quoteco")
    assert load_companies(companies)[-1].name == name


# --- reject ---


def test_reject_creates_the_file_and_discovery_skips_it(tmp_path: Path, companies: Path) -> None:
    rejected = tmp_path / "config" / "rejected_companies.toml"
    reject("Robert Half", "recruiter, not an employer", rejected, companies, TODAY)
    reject("Bank Co", "finance, not industrial", rejected, companies, TODAY)

    with rejected.open("rb") as f:
        data = tomllib.load(f)
    assert data["company"][0] == {
        "name": "Robert Half",
        "reason": "recruiter, not an employer",
        "rejected_on": "2026-10-05",
    }
    assert rejected.read_text(encoding="utf-8").startswith("# PRIVATE: gitignored.")
    assert "Robert Half" in known_employer_names(companies, rejected)


@pytest.mark.parametrize(
    ("name", "reason", "message"),
    [
        ("Some Co", "   ", "give a reason"),
        ("Existing Co", "nope", "remove it there first"),
    ],
)
def test_reject_refusals(
    tmp_path: Path, companies: Path, name: str, reason: str, message: str
) -> None:
    rejected = tmp_path / "rejected.toml"
    with pytest.raises(ConfigError, match=message):
        reject(name, reason, rejected, companies, TODAY)
    assert not rejected.exists()


def test_reject_twice_is_refused(tmp_path: Path, companies: Path) -> None:
    rejected = tmp_path / "rejected.toml"
    reject("Robert Half", "recruiter", rejected, companies, TODAY)
    with pytest.raises(ConfigError, match="already rejected"):
        reject("robert half inc", "again", rejected, companies, TODAY)


# --- the commands ---


def test_approve_command(tmp_path: Path, companies: Path) -> None:
    db = tmp_path / "jobwatcher.db"
    with Store.open(db) as s:
        s.record_board_check(check("Mine Tech", "confirmed", SourceName.ASHBY, "minetech"))
    out = io.StringIO()

    assert run_approve("Mine Tech", companies, db, out) == EXIT_OK
    assert "Approved Mine Tech: added ashby:minetech" in out.getvalue()

    out = io.StringIO()
    assert run_approve("Mine Tech", companies, db, out) == EXIT_UNUSABLE
    assert out.getvalue().startswith("Not approved:")


def test_reject_command(tmp_path: Path, companies: Path) -> None:
    rejected = tmp_path / "rejected.toml"
    out = io.StringIO()
    assert run_reject("Robert Half", "recruiter", rejected, companies, out) == EXIT_OK
    assert "won't propose it again" in out.getvalue()


# --- the discovery report ---


def candidate(
    name: str,
    board: BoardCheck | None,
    titles: tuple[str, ...] = ("Field Engineer",),
    hits: tuple[str, ...] = ("mining",),
) -> Candidate:
    jobs = [AdzunaJob(name, t, "Denver", "us", "", "", "") for t in titles]
    return Candidate(name, employer_key(name), jobs, list(hits), board)


def render(candidates: list[Candidate], errors: list[str] | None = None) -> str:
    config = DiscoveryConfig((SearchBlock(("us",), ("x",)),), Terms(["mining"]), 7, 200)
    result = DiscoveryResult(candidates, 3, 50, 2, 1, errors or [])
    boards = DetectionSummary(2, 1, 1, 4, [])
    return render_discovery_report(NOW, config, result, boards)


REPORT_CANDIDATES = [
    candidate(
        "Ready Co",
        check("Ready Co", "confirmed", SourceName.ASHBY, "readyco", "board has 'Field Engineer'"),
    ),
    candidate("Maybe Co", check("Maybe Co", "possible", SourceName.LEVER, "maybeco")),
    candidate("Workday Corp", check("Workday Corp", "not_found", detail="no board named 'x'")),
    candidate("Later Co", None),
]


def test_report_groups_by_next_action() -> None:
    report = render(REPORT_CANDIDATES)
    order = [
        report.index("## Problems"),
        report.index("## Ready to approve"),
        report.index("**Ready Co**"),
        report.index("## Check first"),
        report.index("**Maybe Co**"),
        report.index("## No readable board found"),
        report.index("**Workday Corp**"),
        report.index("## Not checked yet"),
        report.index("**Later Co**"),
    ]
    assert order == sorted(order)
    assert (
        "- **Job boards:** 1 ready to approve, 1 to check first, "
        "1 with no readable board found, 1 not checked yet" in report
    )


def test_report_shows_board_titles_and_approve_command() -> None:
    ready = render(REPORT_CANDIDATES).split("## Ready to approve")[1].split("## Check first")[0]
    assert "  - board: `ashby:readyco`, board has 'Field Engineer'" in ready
    assert "  - Field Engineer" in ready
    assert '  - approve: `python -m jobwatcher approve "Ready Co"`' in ready


def test_not_found_employers_get_no_approve_command() -> None:
    section = render(REPORT_CANDIDATES).split("## No readable board found")[1].split("##")[0]
    assert "approve:" not in section


def test_report_credits_adzuna_and_lists_problems() -> None:
    report = render(REPORT_CANDIDATES, errors=["us / 'x': HTTP 503"])
    assert report.rstrip().endswith("Source: [The Adzuna API](https://www.adzuna.co.uk/)")
    assert "- us / 'x': HTTP 503" in report.split("## Problems")[1]


def test_report_escapes_employer_text() -> None:
    report = render([candidate("Evil [Co](x)", None, titles=("*Lead* | Ops",))])
    assert "**Evil \\[Co\\](x)**" in report


def test_empty_report_says_so() -> None:
    report = render([])
    assert report.count("None.") >= 4
    assert "None. Every search and board lookup succeeded." in report
