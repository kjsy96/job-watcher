"""Sponsorship rules, for tiers that require sponsorship (test tier 2)."""

from pathlib import Path

import pytest

from jobwatcher.filter_config import FilterRules, load_filter_rules
from jobwatcher.filters import Outcome, evaluate
from jobwatcher.models import Posting, Remote, SourceName
from jobwatcher.sponsorship import SponsorshipStatus, assess

RULES: FilterRules = load_filter_rules(Path(__file__).parent / "fixtures" / "filters_test.toml")
SPONSOR_TIER = RULES.tiers[1]  # "Quebec / Remote Example Country", requires_sponsorship
NO_SPONSOR_TIER = RULES.tiers[0]
PATHWAY_NOTE = "Possible engineer permit route: confirm fit"

OK, EXCLUDE, FLAG = SponsorshipStatus.OK, SponsorshipStatus.EXCLUDE, SponsorshipStatus.FLAG

NON_PATHWAY_TITLE = "Implementation Lead"
PATHWAY_TITLE = "Field Engineer"


def check(description: str, title: str = NON_PATHWAY_TITLE) -> tuple[SponsorshipStatus, str]:
    result = assess(description, title, SPONSOR_TIER, RULES.sponsorship)
    return result.status, result.reason


# --- the four cases from the plan, for non-pathway and pathway titles ---


def test_clear_no_is_excluded() -> None:
    status, reason = check("We are unable to sponsor work visas.")
    assert status is EXCLUDE
    assert reason == "sponsorship: won't sponsor ('unable to sponsor')"


def test_clear_no_is_flagged_for_a_pathway_title() -> None:
    # "Won't sponsor" often means "won't run the full labour-market process",
    # which the pathway permit doesn't need, so a person asks (2026-10-09).
    status, reason = check("We are unable to sponsor work visas.", PATHWAY_TITLE)
    assert status is FLAG
    assert reason == (
        "sponsorship: won't sponsor ('unable to sponsor'); pathway title ('engineer') "
        "may still qualify: ask whether they'd support the pathway permit"
    )


@pytest.mark.parametrize("title", [NON_PATHWAY_TITLE, PATHWAY_TITLE])
def test_restriction_is_excluded_even_for_a_pathway_title(title: str) -> None:
    status, reason = check(
        "Open to Canadian citizens and permanent residents only. Visa sponsorship is offered "
        "for other roles.",
        title,
    )
    assert status is EXCLUDE
    assert reason == "sponsorship: restricted ('citizens and permanent residents only')"


@pytest.mark.parametrize("title", [NON_PATHWAY_TITLE, PATHWAY_TITLE])
def test_clear_yes_passes(title: str) -> None:
    status, reason = check("Visa sponsorship available for the right candidate.", title)
    assert status is OK
    assert reason == "sponsorship: offered ('visa sponsorship')"


@pytest.mark.parametrize("title", [NON_PATHWAY_TITLE, PATHWAY_TITLE])
def test_silence_is_flagged(title: str) -> None:
    status, reason = check("Lead commissioning at mining sites.", title)
    assert status is FLAG
    assert reason == "sponsorship: not stated"


def test_eligibility_only_excludes_a_non_pathway_title() -> None:
    status, reason = check("Candidates must be eligible to work in Example Country.")
    assert status is EXCLUDE
    assert reason == (
        "sponsorship: eligibility required ('must be eligible to work in example country')"
    )


def test_eligibility_only_flags_a_pathway_title() -> None:
    # The occupation-based permit may satisfy the employer's requirement.
    status, reason = check("Candidates must be eligible to work in Example Country.", PATHWAY_TITLE)
    assert status is FLAG
    assert "eligibility required" in reason
    assert "pathway title ('engineer') may qualify" in reason


# --- pathway note ---


def test_pathway_title_carries_the_note() -> None:
    result = assess("Lead commissioning.", PATHWAY_TITLE, SPONSOR_TIER, RULES.sponsorship)
    assert result.pathway_note == PATHWAY_NOTE


def test_non_pathway_title_has_no_note() -> None:
    result = assess("Lead commissioning.", NON_PATHWAY_TITLE, SPONSOR_TIER, RULES.sponsorship)
    assert result.pathway_note == ""


# --- wording traps ---


def test_offer_phrase_inside_a_no_sentence_is_not_an_offer() -> None:
    # Contains "visa sponsorship" (offer) but the sentence says no.
    status, reason = check("Visa sponsorship is not available for this role.")
    assert status is EXCLUDE
    assert reason == "sponsorship: won't sponsor ('sponsorship is not available')"


def test_no_and_offer_in_different_sentences_conflict() -> None:
    text = "We are unable to sponsor H-1B visas.\nWe will sponsor permits for other countries."
    status, reason = check(text)
    assert status is FLAG
    assert reason == "sponsorship: conflicting statements ('unable to sponsor' vs 'will sponsor')"


def test_offer_beats_eligibility_wording() -> None:
    text = "Must be eligible to work in Example Country. We will sponsor the right candidate."
    assert check(text)[0] is OK


def test_unrelated_use_of_sponsor_is_silence() -> None:
    # Real postings use "executive sponsors"; no configured phrase matches.
    assert check("Build trust with executive sponsors.")[0] is FLAG


def test_repeated_terms_are_quoted_once() -> None:
    _, reason = check("Unable to sponsor. Again: unable to sponsor.")
    assert reason == "sponsorship: won't sponsor ('unable to sponsor')"


# --- wired into evaluate(): only tiers that require sponsorship are checked ---


def posting(location: str, description: str, title: str = "Implementation Engineer") -> Posting:
    return Posting(
        source=SourceName.GREENHOUSE,
        board="acme",
        source_job_id="1",
        company="Acme",
        title=title,
        location=location,
        remote=Remote.UNKNOWN,
        url="https://example.test/1",
        description_text=description,
    )


BASE = "Commissioning at mining sites. Travel up to 10%."


def test_tier_without_sponsorship_requirement_ignores_the_wording() -> None:
    result = evaluate(posting("Remote - US", BASE + " Unable to sponsor."), RULES)
    assert result.outcome is Outcome.MATCH
    assert not any("sponsorship" in r for r in result.reasons)


def test_sponsorship_tier_refusal_excludes() -> None:
    result = evaluate(
        posting("Montreal, QC", BASE + " Unable to sponsor.", title="Implementation Lead"), RULES
    )
    assert result.outcome is Outcome.EXCLUDED
    assert result.reasons == ["sponsorship: won't sponsor ('unable to sponsor')"]


def test_sponsorship_tier_refusal_is_flagged_for_a_pathway_title_with_the_note() -> None:
    result = evaluate(posting("Montreal, QC", BASE + " Unable to sponsor."), RULES)
    assert result.outcome is Outcome.FLAGGED
    assert result.reasons[0].startswith("sponsorship: won't sponsor ('unable to sponsor')")
    assert result.reasons[1] == f"note: {PATHWAY_NOTE}"


def test_sponsorship_tier_offer_is_a_match_with_reason() -> None:
    result = evaluate(posting("Montreal, QC", BASE + " Visa sponsorship available."), RULES)
    assert result.outcome is Outcome.MATCH
    assert "sponsorship: offered ('visa sponsorship')" in result.reasons


def test_sponsorship_tier_silence_is_flagged_with_pathway_note_first() -> None:
    result = evaluate(posting("Montreal, QC", BASE), RULES)
    assert result.outcome is Outcome.FLAGGED
    assert result.reasons[0] == "sponsorship: not stated"
    assert result.reasons[1] == f"note: {PATHWAY_NOTE}"  # "Engineer" is a pathway title
    assert result.pathway_note == PATHWAY_NOTE


def test_pathway_note_also_shows_on_a_match() -> None:
    result = evaluate(posting("Montreal, QC", BASE + " We will sponsor."), RULES)
    assert result.outcome is Outcome.MATCH
    assert result.reasons[-1] == f"note: {PATHWAY_NOTE}"


def test_no_note_outside_sponsorship_tiers() -> None:
    assert evaluate(posting("Remote - US", BASE), RULES).pathway_note == ""


def test_best_tier_decides_whether_sponsorship_applies() -> None:
    # Remote US (tier 1, no sponsorship needed) beats Quebec (tier 2).
    result = evaluate(posting("Montreal, QC; Remote - US", BASE + " Unable to sponsor."), RULES)
    assert result.outcome is Outcome.MATCH
