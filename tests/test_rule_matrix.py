"""The rule test matrix (issue 2.2).

CLAUDE.md: every filter rule has tests for match, no match, and the
"can't tell, so flag" case. Each row below runs one case through
evaluate(), and its test id reads "<rule> | <case>", so the pytest output
is the matrix and a missing row is visible at a glance. docs/rules.md
explains each rule in plain language.

Rules where "can't tell" does not apply say why in a row marked n/a
instead of being silently left out. Travel and sponsorship rows arrive
with those rules in issue 2.3.

The baseline posting is a clean Match: role title, remote US, and two
work terms plus one domain term in the description. Each row changes one
thing.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jobwatcher.filter_config import FilterRules, load_filter_rules
from jobwatcher.filters import Outcome, evaluate
from jobwatcher.models import Posting, Remote, SourceName

RULES: FilterRules = load_filter_rules(Path(__file__).parent / "fixtures" / "filters_test.toml")

MATCH, FLAGGED, POSSIBLE, EXCLUDED = (
    Outcome.MATCH,
    Outcome.FLAGGED,
    Outcome.POSSIBLE,
    Outcome.EXCLUDED,
)


@dataclass(frozen=True)
class Row:
    rule: str
    case: str  # "match", "no match", "can't tell", or "n/a: why"
    outcome: Outcome
    reason: str  # must appear in one of the result's reasons
    title: str = "Implementation Engineer"
    location: str = "Remote - US"
    description: str = "Commissioning and data validation at mining sites."
    remote: Remote = Remote.UNKNOWN
    sector: str = ""
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.rule} | {self.case}"


ROWS = [
    # --- Role title (title_include) ---
    Row("role title", "match", MATCH, "title: implementation"),
    Row("role title", "no match", EXCLUDED, "title matches no role term", title="Office Manager"),
    Row(
        "role title",
        "n/a: a posting always has a title (the model rejects a blank one)",
        MATCH,
        "title: implementation",
    ),
    # --- Excluded title words (title_exclude) ---
    Row(
        "title exclude",
        "match",
        EXCLUDED,
        "title contains excluded term(s): intern",
        title="Implementation Intern",
    ),
    Row("title exclude", "no match", MATCH, "title: implementation"),
    Row(
        "title exclude",
        "n/a: a word is in the title or it isn't",
        MATCH,
        "title: implementation",
        title="Implementation Lead (VPN team)",  # "vp" is not "VPN"
    ),
    # --- Domain terms (description or company sector) ---
    Row("domain", "match in description", MATCH, "domain: mining"),
    Row(
        "domain",
        "match in company sector",
        MATCH,
        "domain: manufacturing",
        description="Commissioning for customers.",
        sector="manufacturing",
    ),
    Row(
        "domain",
        "no match (work terms still give a Match)",
        MATCH,
        "work: commissioning",
        description="Commissioning for customers.",
    ),
    Row(
        "domain",
        "n/a: absence is not uncertainty; no domain and no work terms is Possible",
        POSSIBLE,
        "no domain or work terms found",
        description="Join our team.",
    ),
    # --- Work terms (description) ---
    Row("work", "match", MATCH, "work: commissioning, data validation"),
    Row(
        "work",
        "no match (domain terms still give a Match)",
        MATCH,
        "domain: mining",
        description="Work at mining sites.",
    ),
    Row(
        "work",
        "n/a: absence is not uncertainty; covered by Possible above",
        POSSIBLE,
        "no domain or work terms found",
        description="Join our team.",
    ),
    # --- Description flag terms ---
    Row(
        "description flag",
        "match",
        FLAGGED,
        "description mentions: webinar",
        description="Commissioning at mining sites. Hosts a weekly webinar.",
    ),
    Row("description flag", "no match", MATCH, "work: commissioning"),
    Row(
        "description flag",
        "n/a: the rule itself means 'needs a look', so a hit is the flag",
        FLAGGED,
        "description mentions: train the trainer",
        description="Mining commissioning, then train the trainer sessions.",
    ),
    # --- Location: tier placement ---
    Row("location tier", "match", MATCH, "tier 1 (Remote US / Mountain)"),
    Row(
        "location tier",
        "no match",
        EXCLUDED,
        "'Atlanta, Georgia' matches no location tier",
        location="Atlanta, Georgia",
    ),
    Row(
        "location tier",
        "can't tell: ambiguous place",
        FLAGGED,
        "'Portland' is ambiguous",
        location="Portland",
    ),
    Row(
        "location tier",
        "can't tell: location not stated",
        FLAGGED,
        "location not stated",
        location="",
    ),
    Row(
        "location tier",
        "can't tell: remote, region not recognized",
        FLAGGED,
        "remote, but no tier recognizes 'Remote - European Union'",
        location="Remote - European Union",
    ),
    Row(
        "location tier",
        "can't tell: board says remote, city fits no tier",
        FLAGGED,
        "remote, but no tier recognizes 'Atlanta, Georgia'",
        location="Atlanta, Georgia",
        remote=Remote.YES,
    ),
    # --- Location: state/province code decides ---
    Row("state code", "match", MATCH, "'Salem, OR' matched OR", location="Salem, OR"),
    Row(
        "state code",
        "no match (place name alone does not override the code)",
        EXCLUDED,
        "'Salem, MA' is in MA, which is in no tier",
        location="Salem, MA",
    ),
    Row(
        "state code",
        "can't tell: 'CA' alone could be California or Canada",
        FLAGGED,
        "'CA' is ambiguous",
        location="CA",
    ),
    # --- Location: country terms only for bare or remote listings ---
    Row(
        "country",
        "match (bare country)",
        MATCH,
        "'United States' matched united states",
        location="United States",
    ),
    Row(
        "country",
        "match (remote in country)",
        MATCH,
        "'Remote, United States' matched united states",
        location="Remote, United States",
    ),
    Row(
        "country",
        "no match (on-site city in the country)",
        EXCLUDED,
        "'Lakeside, Example Country' matches no location tier",
        location="Lakeside, Example Country",
    ),
    Row(
        "country",
        "can't tell: board says remote, country no tier knows",
        FLAGGED,
        "remote, but no tier recognizes 'United Kingdom'",
        location="United Kingdom",
        remote=Remote.YES,
    ),
    # --- Location: remote tied to another country ---
    Row(
        "non-US remote",
        "match",
        EXCLUDED,
        "'Remote - India' is remote tied to india",
        location="Remote - India",
    ),
    Row("non-US remote", "no match", MATCH, "'Remote - US' matched us"),
    Row(
        "non-US remote",
        "can't tell: remote region not on the list",
        FLAGGED,
        "remote, but no tier recognizes 'Remote, Anywhere'",
        location="Remote, Anywhere",
    ),
    # --- Several locations: best tier wins ---
    Row(
        "multiple locations",
        "match (one segment placed)",
        MATCH,
        "'Remote - US' matched us",
        location="Houston, TX; Remote - US",
    ),
    Row(
        "multiple locations",
        "no match (every segment excluded, every reason kept)",
        EXCLUDED,
        "'Houston, TX' is in TX, which is in no tier; 'Remote - India' is remote tied to india",
        location="Houston, TX; Remote - India",
    ),
    Row(
        "multiple locations",
        "can't tell (nothing placed, one segment ambiguous)",
        FLAGGED,
        "'Portland' is ambiguous",
        location="Remote - India; Portland",
    ),
]


@pytest.mark.parametrize("row", ROWS, ids=[row.id for row in ROWS])
def test_rule_matrix(row: Row) -> None:
    posting = Posting(
        source=SourceName.GREENHOUSE,
        board="acme",
        source_job_id="1",
        company="Acme",
        title=row.title,
        location=row.location,
        remote=row.remote,
        url="https://example.test/1",
        description_text=row.description,
    )
    result = evaluate(posting, RULES, row.sector)

    assert result.outcome is row.outcome, result.reasons
    assert any(row.reason in reason for reason in result.reasons), result.reasons


def test_every_rule_has_match_and_no_match_rows() -> None:
    cases_by_rule: dict[str, list[str]] = {}
    for row in ROWS:
        cases_by_rule.setdefault(row.rule, []).append(row.case)
    for rule, cases in cases_by_rule.items():
        assert any(c.startswith("match") for c in cases), f"{rule}: no match row"
        assert any(c.startswith("no match") for c in cases), f"{rule}: no 'no match' row"
        assert any(c.startswith(("can't tell", "n/a")) for c in cases), (
            f"{rule}: no can't-tell row (or an n/a row saying why)"
        )


def test_row_ids_are_unique() -> None:
    ids = [row.id for row in ROWS]
    assert len(ids) == len(set(ids))
