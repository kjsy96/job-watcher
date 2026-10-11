"""Judge sponsorship wording for postings in tiers that require it.

Only applied when the posting's location tier has requires_sponsorship.
Terms come from [sponsorship] in filters.toml:

- restriction ("citizens and permanent
  residents only"):                           excluded, pathway titles too
- hard no ("unable to sponsor"):             excluded, except pathway titles,
                                              which are flagged instead
- offered ("visa sponsorship"):              ok
- eligibility only ("must be eligible to
  work in ..."):                              excluded, except pathway titles,
                                              which are flagged instead
- none of these:                             flagged, "sponsorship not stated"

Why pathway titles are flagged on a refusal: an employer that won't
"sponsor" often means it won't run the full labour-market process, which
an occupation-based permit doesn't need. Whether it would support that
permit is a question for a person to ask (docs/decisions.md, 2026-10-09).

An offer term in the same sentence as a hard-no term is not counted:
"Visa sponsorship is not available" contains the offer phrase "visa
sponsorship" but means no. A hard no and a real offer in different
sentences contradict each other, so the posting is flagged.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from jobwatcher.filter_config import Sponsorship, Tier

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n|;")


class SponsorshipStatus(StrEnum):
    OK = "ok"
    EXCLUDE = "exclude"
    FLAG = "flag"


@dataclass(frozen=True, slots=True)
class SponsorshipResult:
    status: SponsorshipStatus
    reason: str
    pathway_note: str = ""  # set when the title fits the tier's pathway terms


def assess(description: str, title: str, tier: Tier, terms: Sponsorship) -> SponsorshipResult:
    pathway_hits = tier.pathway_title_terms.hits(title)
    note = tier.pathway_note if pathway_hits else ""

    hard_no: list[str] = []
    offered: list[str] = []
    for sentence in _SENTENCE_BREAK.split(description):
        no_here = terms.hard_no_terms.hits(sentence)
        hard_no.extend(no_here)
        if not no_here:
            # Offer phrases inside a "no" sentence are part of the "no".
            offered.extend(terms.positive_terms.hits(sentence))
    eligibility = terms.eligibility_terms.hits(description)
    restricted = terms.restriction_terms.hits(description)

    def result(status: SponsorshipStatus, reason: str) -> SponsorshipResult:
        return SponsorshipResult(status, f"sponsorship: {reason}", note)

    if restricted:
        return result(SponsorshipStatus.EXCLUDE, f"restricted ({_quote(restricted)})")
    if hard_no and offered:
        return result(
            SponsorshipStatus.FLAG,
            f"conflicting statements ({_quote(hard_no)} vs {_quote(offered)})",
        )
    if hard_no:
        if pathway_hits:
            return result(
                SponsorshipStatus.FLAG,
                f"won't sponsor ({_quote(hard_no)}); pathway title ({_quote(pathway_hits)}) "
                "may still qualify: ask whether they'd support the pathway permit",
            )
        return result(SponsorshipStatus.EXCLUDE, f"won't sponsor ({_quote(hard_no)})")
    if offered:
        return result(SponsorshipStatus.OK, f"offered ({_quote(offered)})")
    if eligibility:
        if pathway_hits:
            return result(
                SponsorshipStatus.FLAG,
                f"eligibility required ({_quote(eligibility)}); pathway title "
                f"({_quote(pathway_hits)}) may qualify",
            )
        return result(SponsorshipStatus.EXCLUDE, f"eligibility required ({_quote(eligibility)})")
    return result(SponsorshipStatus.FLAG, "not stated")


def _quote(terms: list[str]) -> str:
    return ", ".join(f"'{t}'" for t in dict.fromkeys(terms))
