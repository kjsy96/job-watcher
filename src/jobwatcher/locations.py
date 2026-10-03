"""Place a posting's location text into a preference tier, or flag/exclude it.

Location strings from the three boards are free text, sometimes several
locations joined with "; ". Each segment is judged on its own and the
posting gets its best tier. The rules, in order of strength:

1. A state or province code after a comma ("Portland, OR") decides the
   tier. "Salem, MA" fits no tier even though "salem" is a tier 1 place.
2. Remote tied to another country ("Remote - India") is excluded.
3. Place terms ("denver", "texarkana") anywhere in the segment.
4. Country terms ("united states") only for a bare country or a remote
   listing. "Austin, TX, United States" is not "remote US".
5. Plain "Remote" with nothing else goes to the first tier listing it.

What's left is flagged when it might fit (an ambiguous term like "CA"
alone, or a remote job whose text doesn't say where) and excluded
otherwise. Flag, don't guess.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from jobwatcher.filter_config import REGION_CODES, FilterRules, Tier
from jobwatcher.matching import Terms

# A known two-letter code right after a comma: "Portland, OR",
# "Denver, CO, United States". Uppercase only, so "Portland, or Salem"
# can't count as Oregon.
_CODE_AFTER_COMMA = re.compile(r",\s*([A-Z]{2})\b")
# Separators and punctuation, removed when checking whether anything is
# left of a segment besides the words already accounted for.
_FILLER = re.compile(r"[\s,;:()\-/|]+")
_REMOTE_WORD = "remote"


class Placement(StrEnum):
    TIER = "tier"
    FLAG = "flag"
    EXCLUDE = "exclude"


@dataclass(frozen=True, slots=True)
class LocationResult:
    placement: Placement
    tier: Tier | None
    reason: str


def place(location: str, rules: FilterRules, board_says_remote: bool = False) -> LocationResult:
    """Best result across all segments: any tier beats any flag beats exclusion."""
    segments = [s.strip() for s in location.split(";") if s.strip()]
    if not segments:
        return LocationResult(Placement.FLAG, None, "location not stated")

    remote_terms = Terms(
        [_REMOTE_WORD, *(term for tier in rules.tiers for term in tier.remote_terms.terms)]
    )
    results = [_place_segment(s, rules, remote_terms, board_says_remote) for s in segments]
    placed = [r for r in results if r.tier is not None]
    if placed:
        return min(placed, key=lambda r: r.tier.number if r.tier else 0)
    flagged = [r.reason for r in results if r.placement is Placement.FLAG]
    if flagged:
        return LocationResult(Placement.FLAG, None, "; ".join(flagged))
    return LocationResult(Placement.EXCLUDE, None, "; ".join(r.reason for r in results))


def _place_segment(
    segment: str, rules: FilterRules, remote_terms: Terms, board_says_remote: bool
) -> LocationResult:
    is_remote = board_says_remote or remote_terms.any(segment)

    # 1. A state or province code listed by a tier places it in that tier.
    codes = [c for c in _CODE_AFTER_COMMA.findall(segment) if c in REGION_CODES]
    for tier in rules.tiers:
        if hit := [c for c in codes if c in tier.region_codes]:
            return _tiered(tier, segment, hit[0])

    # 2. Remote tied to another country or region.
    if is_remote and (foreign := rules.non_us_remote_terms.hits(segment)):
        return LocationResult(
            Placement.EXCLUDE, None, f"'{segment}' is remote tied to {foreign[0]}"
        )

    # 3. Place terms. A tier that lists codes only accepts its place names
    # without a code or with one of its own codes, so "Salem, MA" can't match
    # tier "salem" + OR. A tier with no codes (e.g. "texarkana", which
    # spans TX and AR) matches on the place name alone.
    for tier in rules.tiers:
        if codes and tier.region_codes:
            continue
        if hit := tier.place_terms.hits(segment):
            return _tiered(tier, segment, hit[0])

    # A code that no tier accepts, and no place term rescued it.
    if codes:
        return LocationResult(
            Placement.EXCLUDE, None, f"'{segment}' is in {codes[0]}, which is in no tier"
        )

    # 4. Country terms, only for a bare country or a remote listing.
    for tier in rules.tiers:
        for countries in (tier.us_wide_terms, tier.country_terms):
            hit = countries.hits(segment)
            if hit and (is_remote or not _leftover(segment, Terms(hit))):
                return _tiered(tier, segment, hit[0])

    # 5. Plain "Remote" with nothing else in the text.
    if is_remote:
        if not _leftover(segment, remote_terms):
            for tier in rules.tiers:
                if hit := tier.remote_terms.hits(segment):
                    return _tiered(tier, segment, hit[0])
        return LocationResult(
            Placement.FLAG,
            None,
            f"remote, but no tier recognizes '{segment}' "
            "(add it to a tier, or to non_us_remote_terms if it's outside the US)",
        )

    if ambiguous := rules.ambiguous_terms.hits(segment):
        return LocationResult(
            Placement.FLAG, None, f"'{segment}' is ambiguous ({', '.join(ambiguous)})"
        )
    return LocationResult(Placement.EXCLUDE, None, f"'{segment}' matches no location tier")


def _tiered(tier: Tier, segment: str, matched: str) -> LocationResult:
    return LocationResult(
        Placement.TIER, tier, f"tier {tier.number} ({tier.label}): '{segment}' matched {matched}"
    )


def _leftover(segment: str, known: Terms) -> str:
    """The segment with the known terms and punctuation removed."""
    return _FILLER.sub("", known.remove(segment))
