from pathlib import Path

import pytest

from jobwatcher.filter_config import FilterRules, load_filter_rules
from jobwatcher.filters import Outcome, evaluate
from jobwatcher.models import Posting, Remote, SourceName

RULES: FilterRules = load_filter_rules(Path(__file__).parent / "fixtures" / "filters_test.toml")

GOOD_DESCRIPTION = "Lead commissioning and data validation at mining sites."


def posting(
    title: str = "Implementation Engineer",
    location: str = "Remote - US",
    description: str = GOOD_DESCRIPTION,
    remote: Remote = Remote.UNKNOWN,
) -> Posting:
    return Posting(
        source=SourceName.GREENHOUSE,
        board="acme",
        source_job_id="1",
        company="Acme",
        title=title,
        location=location,
        remote=remote,
        url="https://example.test/1",
        description_text=description,
    )


# --- Match ---


def test_match_with_score_tier_and_reasons() -> None:
    result = evaluate(posting(), RULES)

    assert result.outcome is Outcome.MATCH
    assert result.score == 3  # mining, commissioning, data validation
    assert result.tier is not None and result.tier.number == 1
    assert result.title_terms == ["implementation"]
    assert result.domain_terms == ["mining"]
    assert result.work_terms == ["commissioning", "data validation"]
    assert result.reasons == [
        "title: implementation",
        "location: tier 1 (Remote US / Mountain): 'Remote - US' matched us",
        "domain: mining",
        "work: commissioning, data validation",
    ]


def test_score_counts_distinct_terms_not_occurrences() -> None:
    once = evaluate(posting(description="mining"), RULES)
    many = evaluate(posting(description="mining mining MINING"), RULES)
    assert once.score == many.score == 1


def test_overlapping_terms_each_count() -> None:
    # "mine" and "mining" are separate config terms; both hit, both count.
    result = evaluate(posting(description="Mining at the mine."), RULES)
    assert result.domain_terms == ["mining", "mine"]
    assert result.score == 2


def test_company_sector_counts_toward_domain() -> None:
    plain = posting(description="Lead commissioning for customers.")
    assert evaluate(plain, RULES).domain_terms == []
    with_sector = evaluate(plain, RULES, sector="cooling tower manufacturing")
    assert with_sector.domain_terms == ["manufacturing", "cooling tower"]
    assert with_sector.score == 3


# --- Possible ---


def test_title_fits_but_no_domain_or_work_terms_is_possible() -> None:
    result = evaluate(posting(description="Join our sales team."), RULES)
    assert result.outcome is Outcome.POSSIBLE
    assert result.score == 0
    assert "no domain or work terms found in the description" in result.reasons


def test_possible_still_lists_flags() -> None:
    result = evaluate(posting(location="Portland", description="Host a webinar."), RULES)
    assert result.outcome is Outcome.POSSIBLE
    assert any("ambiguous" in r for r in result.reasons)
    assert "description mentions: webinar" in result.reasons


# --- Flagged: would match, but needs a look ---


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Portland", "location: 'Portland' is ambiguous (portland)"),
        ("", "location: location not stated"),
        ("Remote - European Union", "location: remote, but no tier recognizes"),
    ],
)
def test_unclear_location_is_flagged(location: str, expected: str) -> None:
    result = evaluate(posting(location=location), RULES)
    assert result.outcome is Outcome.FLAGGED
    assert result.reasons[0].startswith(expected)  # the reason to look comes first
    assert result.score == 3


def test_board_remote_with_unplaced_city_is_flagged() -> None:
    result = evaluate(posting(location="Atlanta, Georgia", remote=Remote.YES), RULES)
    assert result.outcome is Outcome.FLAGGED


def test_description_flag_term_is_flagged() -> None:
    result = evaluate(
        posting(description=GOOD_DESCRIPTION + " Run a train the trainer webinar."), RULES
    )
    assert result.outcome is Outcome.FLAGGED
    assert result.reasons[0] == "description mentions: webinar, train the trainer"


# --- Excluded, with every reason kept ---


def test_title_exclude_term() -> None:
    result = evaluate(posting(title="Implementation Intern"), RULES)
    assert result.outcome is Outcome.EXCLUDED
    assert result.reasons == ["title contains excluded term(s): intern"]
    assert result.score == 0


def test_title_without_role_term() -> None:
    result = evaluate(posting(title="Office Manager"), RULES)
    assert result.reasons == ["title matches no role term"]


def test_padded_exclude_term_still_matches_whole_word() -> None:
    # Config has "vp " (with a space); it is trimmed and matched as a word.
    assert evaluate(posting(title="VP, Implementation"), RULES).outcome is Outcome.EXCLUDED
    assert evaluate(posting(title="Implementation Lead, VPN"), RULES).outcome is Outcome.MATCH


def test_location_in_no_tier() -> None:
    result = evaluate(posting(location="Salem, MA"), RULES)
    assert result.outcome is Outcome.EXCLUDED
    assert result.reasons == ["location: 'Salem, MA' is in MA, which is in no tier"]


def test_all_exclusion_reasons_are_recorded() -> None:
    result = evaluate(posting(title="Account Executive", location="Remote - India"), RULES)
    assert result.reasons == [
        "title contains excluded term(s): account executive",
        "title matches no role term",
        "location: 'Remote - India' is remote tied to india",
    ]


def test_excluded_beats_flags() -> None:
    result = evaluate(posting(title="Intern", location="Portland"), RULES)
    assert result.outcome is Outcome.EXCLUDED


def test_exclusion_keeps_matched_terms_for_review() -> None:
    # Excluded postings can be reviewed later, so what matched is kept.
    result = evaluate(posting(location="Salem, MA"), RULES)
    assert result.domain_terms == ["mining"]
    assert result.tier is None


def test_evaluate_is_deterministic() -> None:
    p = posting(location="Houston, TX; Remote - US")
    assert evaluate(p, RULES) == evaluate(p, RULES)
