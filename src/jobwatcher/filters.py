"""The rule engine: one posting in, one outcome with its reasons out.

Pure and deterministic: no database, no network, no LLM. The same posting
and rules always give the same result, which is what makes the result
testable and explainable.

Outcome order (PROJECT_PLAN.md, "Filter outcomes"):

1. Excluded:  a title exclude term, no role term in the title, or a
              location that fits no tier. All applicable reasons are kept.
2. Possible:  the title fits, but no domain or work term was found.
3. Flagged:   would be a Match, but something needs a human look.
4. Match:     everything checks out. Ranked by overlap score.

Travel and sponsorship rules (issue 2.3) plug into the same exclude/flag
steps.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from jobwatcher.filter_config import FilterRules, Tier
from jobwatcher.locations import Placement, place
from jobwatcher.models import Posting, Remote


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


def evaluate(posting: Posting, rules: FilterRules, sector: str = "") -> FilterResult:
    """Decide one posting's outcome. ``sector`` is the company's config sector,
    which counts toward domain terms (an industry is often only named there)."""
    title_terms = rules.title_include.hits(posting.title)
    excluded_title = rules.title_exclude.hits(posting.title)
    location = place(posting.location, rules, board_says_remote=posting.remote is Remote.YES)

    exclusions: list[str] = []
    if excluded_title:
        exclusions.append(f"title contains excluded term(s): {', '.join(excluded_title)}")
    if not title_terms:
        exclusions.append("title matches no role term")
    if location.placement is Placement.EXCLUDE:
        exclusions.append(f"location: {location.reason}")

    domain_terms = rules.domain_terms.hits(posting.description_text, sector)
    work_terms = rules.work_terms.hits(posting.description_text)
    # A term in both lists counts once: the score is distinct terms.
    score = len(set(domain_terms) | set(work_terms))

    if exclusions:
        return FilterResult(
            Outcome.EXCLUDED,
            0,
            location.tier,
            title_terms,
            domain_terms,
            work_terms,
            exclusions,
        )

    reasons = [f"title: {', '.join(title_terms)}"]
    flags: list[str] = []
    if location.placement is Placement.FLAG:
        flags.append(f"location: {location.reason}")
    else:
        reasons.append(f"location: {location.reason}")
    if flag_terms := rules.description_flag_terms.hits(posting.description_text):
        flags.append(f"description mentions: {', '.join(flag_terms)}")

    if score == 0:
        return FilterResult(
            Outcome.POSSIBLE,
            0,
            location.tier,
            title_terms,
            domain_terms,
            work_terms,
            [*reasons, "no domain or work terms found in the description", *flags],
        )

    if domain_terms:
        reasons.append(f"domain: {', '.join(domain_terms)}")
    if work_terms:
        reasons.append(f"work: {', '.join(work_terms)}")
    if flags:
        return FilterResult(
            Outcome.FLAGGED,
            score,
            location.tier,
            title_terms,
            domain_terms,
            work_terms,
            [*flags, *reasons],  # what needs a look comes first
        )
    return FilterResult(
        Outcome.MATCH, score, location.tier, title_terms, domain_terms, work_terms, reasons
    )
