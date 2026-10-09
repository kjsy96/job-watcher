from pathlib import Path

import pytest

from jobwatcher.filter_config import FilterRules, load_filter_rules
from jobwatcher.filters import Outcome, evaluate
from jobwatcher.models import Posting, Remote, SourceName

RULES: FilterRules = load_filter_rules(Path(__file__).parent / "fixtures" / "filters_test.toml")

# Travel is stated (and within the 40% limit) so the baseline is a clean Match.
GOOD_DESCRIPTION = "Lead commissioning and data validation at mining sites. Travel up to 10%."


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
        "location: tier 1 (Remote US / Example states): 'Remote - US' matched us",
        "travel 'up to 10%' is within 40%",
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
    result = evaluate(posting(location="Springfield", description="Host a webinar."), RULES)
    assert result.outcome is Outcome.POSSIBLE
    assert any("ambiguous" in r for r in result.reasons)
    assert "description mentions: webinar" in result.reasons


# --- Flagged: would match, but needs a look ---


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Springfield", "location: 'Springfield' is ambiguous (springfield)"),
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
    result = evaluate(posting(location="Burlington, MA"), RULES)
    assert result.outcome is Outcome.EXCLUDED
    assert result.reasons == ["location: 'Burlington, MA' is in MA, which is in no tier"]


def test_all_exclusion_reasons_are_recorded() -> None:
    result = evaluate(posting(title="Account Executive", location="Remote - India"), RULES)
    assert result.reasons == [
        "title contains excluded term(s): account executive",
        "title matches no role term",
        "location: 'Remote - India' is remote tied to india",
    ]


def test_excluded_beats_flags() -> None:
    result = evaluate(posting(title="Intern", location="Springfield"), RULES)
    assert result.outcome is Outcome.EXCLUDED


def test_exclusion_keeps_matched_terms_for_review() -> None:
    # Excluded postings can be reviewed later, so what matched is kept.
    result = evaluate(posting(location="Burlington, MA"), RULES)
    assert result.domain_terms == ["mining"]
    assert result.tier is None


def test_evaluate_is_deterministic() -> None:
    p = posting(location="Houston, TX; Remote - US")
    assert evaluate(p, RULES) == evaluate(p, RULES)


def test_travel_rule_is_off_without_max_percent(tmp_path: Path) -> None:
    text = (Path(__file__).parent / "fixtures" / "filters_test.toml").read_text(encoding="utf-8")
    no_travel = tmp_path / "filters.toml"
    no_travel.write_text(text.replace("[travel]\nmax_percent = 40\n", ""), encoding="utf-8")
    rules = load_filter_rules(no_travel)
    assert rules.travel_max_percent is None

    # No travel statement at all, yet a clean Match: the rule is switched off.
    result = evaluate(posting(description="Commissioning at mining sites."), rules)
    assert result.outcome is Outcome.MATCH
    assert not any("travel" in r for r in result.reasons)


# --- title flag terms: usually not a fit, but the title is ambiguous ---


def test_title_flag_term_with_a_role_term_is_flagged() -> None:
    # "test engineer" is a role term, so this might still be a fit.
    result = evaluate(posting(title="Firmware Test Engineer"), RULES)
    assert result.outcome is Outcome.FLAGGED
    assert result.reasons[0] == (
        "title mentions firmware, which is usually not a fit; check what the role is"
    )
    assert result.title_terms == ["test engineer"]


def test_title_flag_term_alone_is_excluded_and_named() -> None:
    result = evaluate(posting(title="Senior Controls Engineer"), RULES)
    assert result.outcome is Outcome.EXCLUDED
    assert result.reasons == ["title matches no role term (and mentions controls engineer)"]


def test_title_flag_term_without_overlap_stays_possible_with_the_reason() -> None:
    # No domain or work terms: Possible, as for any title-only hit, and the
    # reason to be careful is still shown.
    result = evaluate(
        posting(title="Firmware Test Engineer", description="Travel up to 10%."), RULES
    )
    assert result.outcome is Outcome.POSSIBLE
    assert "title mentions firmware, which is usually not a fit; check what the role is" in (
        result.reasons
    )


def test_title_flag_terms_match_whole_words_only() -> None:
    # "firmwares" is a plural form; "Firmwareless" is a different word.
    assert evaluate(posting(title="Firmwareless Test Engineer"), RULES).outcome is Outcome.MATCH
    flagged = evaluate(posting(title="Test Engineer, Firmwares"), RULES)
    assert flagged.outcome is Outcome.FLAGGED


def test_title_exclude_still_beats_a_title_flag() -> None:
    result = evaluate(posting(title="Firmware Test Engineer Intern"), RULES)
    assert result.outcome is Outcome.EXCLUDED
    assert result.reasons[0] == "title contains excluded term(s): intern"
