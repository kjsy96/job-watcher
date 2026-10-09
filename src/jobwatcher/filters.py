"""The rule engine: one posting in, one outcome with its reasons out.

Pure and deterministic: no database, no network, no LLM. The same posting
and rules always give the same result, which is what makes the result
testable and explainable.

Outcome order (PROJECT_PLAN.md, "Filter outcomes"):

1. Excluded:  a title exclude term, no role term in the title, a location
              that fits no tier, travel that can go above the limit, or
              sponsorship refused in a tier that needs it. All reasons
              are kept.
2. Possible:  the title fits, but no domain or work term was found.
3. Flagged:   would be a Match, but something needs a human look:
              unclear location or sponsorship, a description flag
              term, or a title flag term alongside a role term.
4. Match:     everything checks out. Ranked by overlap score.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from jobwatcher import sponsorship, travel
from jobwatcher.filter_config import FilterRules, Tier
from jobwatcher.locations import Placement, place
from jobwatcher.models import Posting, Remote
from jobwatcher.sponsorship import SponsorshipStatus
from jobwatcher.travel import TravelStatus


class Outcome(StrEnum):
    MATCH = "match"
    FLAGGED = "flagged"
    POSSIBLE = "possible"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class FilterResult:
    outcome: Outcome
    score: int  # distinct domain + work terms hit; 0 unless the title fits
    tier: Tier | None
    title_terms: list[str] = field(default_factory=list)
    domain_terms: list[str] = field(default_factory=list)
    work_terms: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)  # plain language, in order
    # Set when the title fits the tier's pathway terms. The report sorts
    # these to the top of Flagged (PROJECT_PLAN.md, issue 2.3).
    pathway_note: str = ""


def evaluate(posting: Posting, rules: FilterRules, sector: str = "") -> FilterResult:
    """Decide one posting's outcome. ``sector`` is the company's config sector,
    which counts toward domain terms (an industry is often only named there)."""
    text = posting.description_text
    title_terms = rules.title_include.hits(posting.title)
    excluded_title = rules.title_exclude.hits(posting.title)
    unlikely_title = rules.title_flag_terms.hits(posting.title)
    location = place(posting.location, rules, board_says_remote=posting.remote is Remote.YES)

    exclusions: list[str] = []  # any of these: Excluded
    flags: list[str] = []  # any of these: needs a human look
    passed: list[str] = []  # checks that passed, kept to explain a Match

    if excluded_title:
        exclusions.append(f"title contains excluded term(s): {', '.join(excluded_title)}")
    if not title_terms:
        exclusions.append(
            "title matches no role term"
            + (f" (and mentions {', '.join(unlikely_title)})" if unlikely_title else "")
        )
    elif unlikely_title:
        # A role term hit too, so the title is ambiguous: a person decides.
        flags.append(
            f"title mentions {', '.join(unlikely_title)}, which is usually not a fit; "
            "check what the role is"
        )

    if location.placement is Placement.EXCLUDE:
        exclusions.append(f"location: {location.reason}")
    elif location.placement is Placement.FLAG:
        flags.append(f"location: {location.reason}")
    else:
        passed.append(f"location: {location.reason}")

    if rules.travel_max_percent is not None:
        trip = travel.assess(text, rules.travel_max_percent)
        {
            TravelStatus.EXCEEDS: exclusions,
            TravelStatus.WITHIN: passed,
        }[trip.status].append(trip.reason)

    pathway_note = ""
    if location.tier is not None and location.tier.requires_sponsorship:
        visa = sponsorship.assess(text, posting.title, location.tier, rules.sponsorship)
        {
            SponsorshipStatus.EXCLUDE: exclusions,
            SponsorshipStatus.FLAG: flags,
            SponsorshipStatus.OK: passed,
        }[visa.status].append(visa.reason)
        pathway_note = visa.pathway_note

    if flag_terms := rules.description_flag_terms.hits(text):
        flags.append(f"description mentions: {', '.join(flag_terms)}")

    domain_terms = rules.domain_terms.hits(text, sector)
    work_terms = rules.work_terms.hits(text)
    # A term in both lists counts once: the score is distinct terms.
    score = len(set(domain_terms) | set(work_terms))
    notes = [f"note: {pathway_note}"] if pathway_note else []

    def result(outcome: Outcome, score: int, reasons: list[str]) -> FilterResult:
        return FilterResult(
            outcome,
            score,
            location.tier,
            title_terms,
            domain_terms,
            work_terms,
            reasons,
            pathway_note,
        )

    if exclusions:
        return result(Outcome.EXCLUDED, 0, exclusions)

    reasons = [f"title: {', '.join(title_terms)}", *passed]
    if score == 0:
        return result(
            Outcome.POSSIBLE,
            0,
            [*reasons, "no domain or work terms found in the description", *flags, *notes],
        )

    if domain_terms:
        reasons.append(f"domain: {', '.join(domain_terms)}")
    if work_terms:
        reasons.append(f"work: {', '.join(work_terms)}")
    if flags:
        # What needs a look comes first, then the pathway note, then the rest.
        return result(Outcome.FLAGGED, score, [*flags, *notes, *reasons])
    return result(Outcome.MATCH, score, [*reasons, *notes])
