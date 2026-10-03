from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobwatcher.fetch import CompanyResult
from jobwatcher.fetch import Outcome as FetchOutcome
from jobwatcher.filter_config import FilterRules, load_filter_rules
from jobwatcher.filters import FilterResult, Outcome
from jobwatcher.models import Company, Posting, Remote, SourceName
from jobwatcher.report import ReportItem, escape, render_report
from jobwatcher.store import BoardResult

RULES: FilterRules = load_filter_rules(Path(__file__).parent / "fixtures" / "filters_test.toml")
TIER1, TIER2, TIER3 = RULES.tiers
RUN_AT = datetime(2026, 10, 4, 7, 30, tzinfo=UTC)

ACME = Company("Acme", SourceName.GREENHOUSE, "acme")
BETA = Company("Beta", SourceName.LEVER, "beta")
DOWN = Company("Down Co", SourceName.ASHBY, "downco")
EMPTY = Company("Empty Co", SourceName.GREENHOUSE, "emptyco")


def ok(company: Company, fetched: int, new: int, closed: int = 0) -> CompanyResult:
    return CompanyResult(
        company,
        FetchOutcome.OK,
        fetched=fetched,
        board=BoardResult(
            new=new,
            seen_again=fetched - new,
            reopened=0,
            closed=closed,
            previously_open=fetched - new + closed,
        ),
    )


FAILED = CompanyResult(DOWN, FetchOutcome.FAILED, message="HTTP 503")
WARNED = CompanyResult(
    EMPTY,
    FetchOutcome.WARNING,
    fetched=0,
    message="returned 0 jobs but 7 were open; not recorded, check the board or the config",
)


def item(
    title: str,
    outcome: Outcome,
    score: int = 0,
    tier: object = TIER1,
    reasons: list[str] | None = None,
    company: str = "Acme",
    pathway: str = "",
    url: str | None = None,
) -> ReportItem:
    posting = Posting(
        source=SourceName.GREENHOUSE,
        board="acme",
        source_job_id=title,
        company=company,
        title=title,
        location="Remote - US",
        remote=Remote.UNKNOWN,
        url=url or f"https://example.test/{len(title)}",
        description_text="",
    )
    result = FilterResult(
        outcome,
        score,
        tier,  # type: ignore[arg-type]
        reasons=reasons or [f"reason for {title}"],
        pathway_note=pathway,
    )
    return ReportItem(posting, result)


def sections(report: str) -> list[str]:
    return [line for line in report.splitlines() if line.startswith("## ")]


def section(report: str, name: str) -> str:
    start = report.index(f"## {name}\n")
    nxt = report.find("\n## ", start + 1)
    return report[start : nxt if nxt != -1 else len(report)]


FULL = [
    item("Low Match", Outcome.MATCH, score=2),
    item("High Match", Outcome.MATCH, score=9),
    item("Tier Two Match", Outcome.MATCH, score=20, tier=TIER2),
    item("Flag Plain", Outcome.FLAGGED, score=8, reasons=["travel not stated", "title: x"]),
    item("Flag Pathway", Outcome.FLAGGED, score=1, pathway="Possible engineer route"),
    item("Maybe", Outcome.POSSIBLE),
    item("Wrong Title", Outcome.EXCLUDED, reasons=["title matches no role term"]),
    item(
        "Far Away",
        Outcome.EXCLUDED,
        reasons=[
            "title matches no role term",
            "location: 'Remote - India' is remote tied to india",
        ],
    ),
    item("Too Much Travel", Outcome.EXCLUDED, reasons=["travel '75%' exceeds the 40% limit"]),
]
COMPANIES = [ok(ACME, 12, 9, closed=1), FAILED, WARNED, ok(BETA, 3, 0)]


@pytest.fixture
def report() -> str:
    return render_report(RUN_AT, COMPANIES, FULL)


def test_sections_in_plan_order(report: str) -> None:
    assert report.startswith("# Job Watcher report: 2026-10-04\n")
    assert sections(report) == [
        "## Source problems",
        "## Matches",
        "## Flagged",
        "## Possible",
        "## Excluded",
        "## Companies",
    ]


def test_summary_counts(report: str) -> None:
    assert "Run at 2026-10-04 07:30 (UTC+00:00)" in report
    assert "- **Companies:** 4 checked: 2 ok, 1 warning, 1 failed" in report
    assert "- **Postings:** 15 fetched, 9 new, 0 reopened, 1 closed" in report
    assert "- **New postings:** 3 match, 2 flagged, 1 possible, 3 excluded" in report


def test_source_problems_come_before_any_results(report: str) -> None:
    problems = section(report, "Source problems")
    assert "- **ERROR: Down Co** (`ashby:downco`): HTTP 503" in problems
    assert "- **WARNING: Empty Co** (`greenhouse:emptyco`): returned 0 jobs but 7 were open" in (
        problems
    )
    assert "were **not** recorded this run" in problems
    assert report.index("## Source problems") < report.index("## Matches")


def test_source_problems_show_even_with_no_new_postings() -> None:
    report = render_report(RUN_AT, [ok(ACME, 12, 0), FAILED], [])
    assert "ERROR: Down Co" in section(report, "Source problems")
    assert "No new matches." in report


def test_no_source_problems_is_stated() -> None:
    report = render_report(RUN_AT, [ok(ACME, 12, 0)], [])
    assert "None. Every company's board was read and recorded." in report


def test_matches_grouped_by_tier_then_sorted_by_score(report: str) -> None:
    matches = section(report, "Matches")
    order = [
        matches.index("### Tier 1: Remote US / Mountain"),
        matches.index("[High Match]"),
        matches.index("[Low Match]"),
        matches.index("### Tier 2: Quebec / Remote Example Country"),
        matches.index("[Tier Two Match]"),
    ]
    assert order == sorted(order)  # tier 1 first even though tier 2 scores higher


def test_entry_shows_link_company_location_score_and_reasons(report: str) -> None:
    matches = section(report, "Matches")
    assert "- **[High Match](<https://example.test/10>)**: Acme \N{MIDDLE DOT} " in matches
    assert "Remote - US \N{MIDDLE DOT} score 9" in matches
    assert "  - reason for High Match" in matches


def test_flagged_pathway_first_then_by_score_with_reason_first(report: str) -> None:
    flagged = section(report, "Flagged")
    assert flagged.index("(pathway) **[Flag Pathway]") < flagged.index("**[Flag Plain]")
    plain = flagged[flagged.index("**[Flag Plain]") :]
    assert plain.splitlines()[1] == "  - travel not stated"


def test_possible_is_a_compact_list(report: str) -> None:
    possible = section(report, "Possible")
    assert "- [Maybe](<https://example.test/5>): Acme \N{MIDDLE DOT} Remote - US" in possible
    assert "score" not in possible
    assert "reason for Maybe" not in possible


def test_excluded_is_a_count_with_reasons_available(report: str) -> None:
    excluded = section(report, "Excluded")
    # "Far Away" has two reasons: it counts once under title and once under location.
    assert "3 new postings excluded (by reason: title 2, location 1, travel 1)." in excluded
    assert "<details>" in excluded and "</details>" in excluded
    assert "[Too Much Travel]" in excluded
    assert "title matches no role term; location: " in excluded


def test_company_table_accounts_for_every_source(report: str) -> None:
    table = section(report, "Companies")
    assert "| Acme | `greenhouse:acme` | 12 | 9 | 1 | ok |" in table
    assert "| Down Co | `ashby:downco` | - | - | - | failed: HTTP 503 |" in table
    assert "| Empty Co | `greenhouse:emptyco` | - | - | - | warning: returned 0 jobs" in table
    assert "| Beta | `lever:beta` | 3 | 0 | 0 | ok |" in table


def test_empty_sections_say_so() -> None:
    report = render_report(RUN_AT, [], [])
    for message in (
        "No new matches.",
        "No new flagged postings.",
        "No new possible postings.",
        "No new postings were excluded.",
        "No companies were checked.",
    ):
        assert message in report


# --- Markdown safety ---


@pytest.mark.parametrize(
    ("text", "escaped"),
    [
        ("Engineer [Remote]", r"Engineer \[Remote\]"),
        ("Ops | Field", r"Ops \| Field"),
        ("*Senior* _Lead_", r"\*Senior\* \_Lead\_"),
        ("C++ / C#", "C++ / C#"),  # harmless mid-line, left readable
        ("Engineer (Remote)", "Engineer (Remote)"),  # harmless without [ ]
        ("set non_us_remote_terms", "set non_us_remote_terms"),  # inside a word
        ("_Lead_ engineer", r"\_Lead\_ engineer"),  # at word edges: could be emphasis
        ("<script>", r"\<script\>"),
        ("Multi\nline   title", "Multi line title"),
    ],
)
def test_escape(text: str, escaped: str) -> None:
    assert escape(text) == escaped


def test_hostile_title_cannot_break_the_link_or_table() -> None:
    hostile = item(
        "Lead](javascript:x) | **Boss** [x",
        Outcome.MATCH,
        score=3,
        url="https://example.test/jobs/(42)",
    )
    report = render_report(RUN_AT, [ok(ACME, 1, 1)], [hostile])
    line = next(row for row in report.splitlines() if "javascript" in row)
    assert line.startswith(
        r"- **[Lead\](javascript:x) \| \*\*Boss\*\* \[x](<https://example.test/jobs/(42)>)**"
    )


def test_company_name_with_pipe_does_not_break_the_table() -> None:
    piped = Company("A | B Corp", SourceName.LEVER, "ab")
    table = section(render_report(RUN_AT, [ok(piped, 1, 0)], []), "Companies")
    assert r"| A \| B Corp |" in table


def test_report_ends_with_one_newline(report: str) -> None:
    assert report.endswith("\n") and not report.endswith("\n\n")


def test_clock_uses_a_numeric_offset() -> None:
    from datetime import timedelta, timezone

    sydney = datetime(2026, 10, 4, 18, 30, tzinfo=timezone(timedelta(hours=11)))
    report = render_report(sydney, [], [])
    assert "Run at 2026-10-04 18:30 (UTC+11:00)" in report
