"""Find how much travel a posting states, and judge it against the limit.

Built from real wording in stored postings: "up to 75%", "~75% travel",
"(up to 15%)", "10-20% travel required", "approximately 15-30% of the
time", "10 to 15 percent", "up to 5-10%", "~ 35%", "50%+".

Each percentage near a travel word becomes a range (low, high), and the
posting is judged on all of its ranges together:

- every range tops out at or below the limit:  within
- every range starts above the limit:          exceeds  -> excluded
- anything in between ("up to 50%" vs 40%):    unclear  -> flagged

Travel described without a percentage ("twice per year", "occasional
travel") is flagged with the sentence quoted, not converted: whether "90
days per year" means calendar or working days would be a guess.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

_TRAVEL_WORD = re.compile(r"\btravel(?:s|ed|ing|ling|led|er|ers)?\b", re.IGNORECASE)
# Sentences: ends of sentences, line breaks (block tags become line breaks
# in stored text), and semicolons.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n|;")
_MAX_DISTANCE = 160  # characters between a travel word and its percentage (one long real
# sentence put "up to 25%" about 130 characters after "Travel")

_NUM = r"(\d{1,3})"
_PERCENT = r"\s*(?:%|percent\b)"
_DASH = r"\s*(?:-|\u2013|\u2014|to)\s*"
# "15-30%", "15 - 30 %", "10 to 15 percent", "15%-30%"
_RANGE = re.compile(_NUM + r"\s*%?" + _DASH + _NUM + _PERCENT, re.IGNORECASE)
# "25%", "25 percent", "50%+"
_SINGLE = re.compile(_NUM + _PERCENT + r"(\+)?", re.IGNORECASE)

_UP_TO = re.compile(
    r"(?:up\s+to|less\s+than|under|no\s+more\s+than|not\s+(?:to\s+)?exceed|"
    r"maximum(?:\s+of)?|max\.?)\s*[~\u2248]?\s*(?:approximately\s+|approx\.?\s+|about\s+)?$",
    re.IGNORECASE,
)
_AT_LEAST = re.compile(
    r"(?:at\s+least|more\s+than|over|minimum(?:\s+of)?|min\.?)\s*[~\u2248]?\s*$",
    re.IGNORECASE,
)
_NO_TRAVEL = re.compile(
    r"\bno\s+travel\b|\btravel\s+(?:is\s+)?not\s+required\b|\bdoes\s+not\s+require\s+travel\b",
    re.IGNORECASE,
)
_QUALIFIER_WINDOW = 40  # characters before a percentage checked for "up to" etc.


class TravelStatus(StrEnum):
    WITHIN = "within"
    EXCEEDS = "exceeds"
    UNCLEAR = "unclear"


@dataclass(frozen=True, slots=True)
class TravelRange:
    low: int
    high: int
    quote: str  # the words it came from, e.g. "up to 15%"


@dataclass(frozen=True, slots=True)
class TravelResult:
    status: TravelStatus
    reason: str


def find_ranges(text: str) -> list[TravelRange]:
    """Every travel percentage stated in the text, as a (low, high) range."""
    ranges: list[TravelRange] = []
    for sentence in _SENTENCE_BREAK.split(text):
        travel_spots = [m.start() for m in _TRAVEL_WORD.finditer(sentence)]
        if not travel_spots:
            continue
        taken: list[tuple[int, int]] = []
        for match in [*_RANGE.finditer(sentence), *_SINGLE.finditer(sentence)]:
            start, end = match.span()
            if any(start < t_end and t_start < end for t_start, t_end in taken):
                continue  # "15-30%" already read as a range; skip its "30%"
            if min(abs(start - spot) for spot in travel_spots) > _MAX_DISTANCE:
                continue  # a percentage about something else in a long sentence
            parsed = _to_range(sentence, match)
            if parsed is not None:
                taken.append((start, end))
                ranges.append(parsed)
    return ranges


def _to_range(sentence: str, match: re.Match[str]) -> TravelRange | None:
    numbers = [int(g) for g in match.groups() if g is not None and g.isdigit()]
    if any(n > 100 for n in numbers):
        return None
    before = sentence[max(0, match.start() - _QUALIFIER_WINDOW) : match.start()]
    qualifier = _UP_TO.search(before) or _AT_LEAST.search(before)
    quote = (qualifier.group(0) if qualifier else "") + match.group(0)
    quote = " ".join(quote.split())

    if len(numbers) == 2:
        low, high = sorted(numbers)
    else:
        low = high = numbers[0]
    if _UP_TO.search(before):
        low = 0
    elif _AT_LEAST.search(before) or match.group(0).endswith("+"):
        high = 100
    return TravelRange(low, high, quote)


def assess(text: str, max_percent: int) -> TravelResult:
    ranges = find_ranges(text)
    if ranges:
        # The same wording stated twice in a posting is quoted once.
        quoted = ", ".join(f"'{q}'" for q in dict.fromkeys(r.quote for r in ranges))
        if all(r.high <= max_percent for r in ranges):
            return TravelResult(TravelStatus.WITHIN, f"travel {quoted} is within {max_percent}%")
        if all(r.low > max_percent for r in ranges):
            return TravelResult(
                TravelStatus.EXCEEDS, f"travel {quoted} exceeds the {max_percent}% limit"
            )
        return TravelResult(
            TravelStatus.UNCLEAR, f"travel {quoted} may exceed the {max_percent}% limit"
        )

    if _NO_TRAVEL.search(text):
        return TravelResult(TravelStatus.WITHIN, "travel: none required")

    for sentence in _SENTENCE_BREAK.split(text):
        if _TRAVEL_WORD.search(sentence):
            snippet = " ".join(sentence.split())
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
            return TravelResult(
                TravelStatus.UNCLEAR, f"travel mentioned without a percentage: '{snippet}'"
            )
    return TravelResult(TravelStatus.UNCLEAR, "travel not stated")
