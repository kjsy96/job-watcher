"""Travel detection. Phrasings are taken from real stored postings unless noted."""

import pytest

from jobwatcher.travel import TravelStatus, assess, find_ranges

LIMIT = 40
WITHIN, EXCEEDS, UNCLEAR = TravelStatus.WITHIN, TravelStatus.EXCEEDS, TravelStatus.UNCLEAR


@pytest.mark.parametrize(
    ("text", "low", "high"),
    [
        ("Willingness to travel up to 75% to customer sites.", 0, 75),
        ("Must deploy for multi-week rotations, role is ~75% travel", 75, 75),
        ("This role requires periodic travel to customer sites (up to 15%)", 0, 15),
        ("10-20% travel required.", 10, 20),
        (
            "Willingness to travel, including to mine sites (approximately 15-30% of the time)",
            15,
            30,
        ),
        ("Expect roughly 10 to 15 percent travel to project sites globally", 10, 15),
        ("Able to travel up to 5-10%.", 0, 10),
        ("Travel: Must be able to travel domestically and internationally, 20-30% time.", 20, 30),
        ("Willing to travel domestically ~ 35%.", 35, 35),
        ("Approximately 50% travel on client site", 50, 50),
        ("Ability to travel (up to 50%).", 0, 50),
        ("Travel 15%-30%", 15, 30),  # made up: percent sign on both numbers
        ("Travel 15\N{EN DASH}30%", 15, 30),  # made up: en dash
        ("Travel 50%+ of the time", 50, 100),  # made up: plus sign
        ("Travel at least 30% of the time", 30, 100),  # made up
        ("Travel less than 10%", 0, 10),  # made up
        ("Travel will not exceed 20%", 0, 20),  # made up
        (
            "Travel internationally or within the region for customer, partner, and internal "
            "training events, expected to be approximately up to 25% of the time.",
            0,
            25,
        ),  # the percentage is ~130 characters from "Travel"
    ],
)
def test_real_phrasings_become_ranges(text: str, low: int, high: int) -> None:
    ranges = find_ranges(text)
    assert [(r.low, r.high) for r in ranges] == [(low, high)]


def test_range_quote_includes_its_qualifier() -> None:
    assert find_ranges("Travel up to 15%")[0].quote == "up to 15%"


@pytest.mark.parametrize(
    "text",
    [
        "Great benefits. 401k with 4% match.",  # no travel word at all
        "We travel a lot. Our revenue grew 300% last year.",  # different sentence
        "Travel stipend included, and we cover " + "x" * 200 + " 20% of costs",  # too far
        "Travel to 150% of quota",  # not a valid percentage
    ],
)
def test_percentages_that_are_not_about_travel_are_ignored(text: str) -> None:
    assert find_ranges(text) == []


# --- the decision against the limit ---


@pytest.mark.parametrize(
    ("text", "status", "reason"),
    [
        ("Travel up to 25%.", WITHIN, "travel 'up to 25%' is within 40%"),
        ("10-20% travel required.", WITHIN, "travel '10-20%' is within 40%"),
        ("Travel 40%.", WITHIN, "within 40%"),  # at the limit is within
        ("Role is ~75% travel.", EXCEEDS, "travel '75%' exceeds the 40% limit"),
        ("Travel 50%+.", EXCEEDS, "exceeds the 40% limit"),
        ("Ability to travel (up to 50%).", UNCLEAR, "travel 'up to 50%' may exceed the 40% limit"),
        ("Travel 30-50% of the time.", UNCLEAR, "may exceed"),  # straddles the limit
        ("Travel at least 30%.", UNCLEAR, "may exceed"),
        ("Travel 10% normally. Peaks of 60% travel in launches.", UNCLEAR, "may exceed"),
    ],
)
def test_percentages_against_the_limit(text: str, status: TravelStatus, reason: str) -> None:
    result = assess(text, LIMIT)
    assert result.status is status
    assert reason in result.reason


@pytest.mark.parametrize(
    "text",
    [
        "Travel is approximately twice per year depending on project needs.",
        "Employees are expected to travel to project sites, a minimum of one week per year.",
        "Willingness to travel internationally, with anticipated travel of 90+ days per year.",
        "This role is based in our office, with occasional travel to customer sites.",
        "Candidates should be comfortable with frequent travel.",
    ],
)
def test_travel_without_a_percentage_is_unclear_and_quoted(text: str) -> None:
    result = assess(text, LIMIT)
    assert result.status is UNCLEAR
    assert result.reason.startswith("travel mentioned without a percentage: '")


def test_long_sentence_is_shortened_in_the_reason() -> None:
    result = assess("Travel " + "to many places " * 20, LIMIT)
    assert result.reason.endswith("...'")
    assert len(result.reason) < 180


def test_no_travel_mentioned_is_not_stated() -> None:
    result = assess("Lead commissioning at mining sites.", LIMIT)
    assert (result.status, result.reason) == (UNCLEAR, "travel not stated")


@pytest.mark.parametrize("text", ["No travel required.", "Travel is not required for this role."])
def test_explicit_no_travel_is_within(text: str) -> None:
    result = assess(text, LIMIT)
    assert (result.status, result.reason) == (WITHIN, "travel: none required")


def test_stated_percentage_beats_vague_mentions() -> None:
    # Company boilerplate ("one week per year") plus a role-specific percentage.
    text = "Expected to travel one week per year.\nTravel up to 20% to client sites."
    assert assess(text, LIMIT).status is WITHIN


def test_repeated_wording_is_quoted_once() -> None:
    result = assess("Travel 50%+ to sites.\nRequirements: travel 50%+.", LIMIT)
    assert result.reason == "travel '50%+' exceeds the 40% limit"
