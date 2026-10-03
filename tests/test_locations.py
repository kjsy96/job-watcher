from pathlib import Path

import pytest

from jobwatcher.filter_config import FilterRules, load_filter_rules
from jobwatcher.locations import Placement, place

RULES: FilterRules = load_filter_rules(Path(__file__).parent / "fixtures" / "filters_test.toml")

TIER, FLAG, EXCLUDE = Placement.TIER, Placement.FLAG, Placement.EXCLUDE


@pytest.mark.parametrize(
    ("location", "placement", "tier"),
    [
        # Remote and US-wide
        ("Remote", TIER, 1),
        ("Work from home", TIER, 1),
        ("United States", TIER, 1),  # bare country
        ("Remote - US", TIER, 1),
        ("Remote, United States", TIER, 1),
        # Codes decide
        ("Portland, OR", TIER, 1),
        ("Boulder, CO", TIER, 1),
        ("Remote, CO", TIER, 1),  # a code after a comma is a state code
        ("Montreal, QC", TIER, 2),
        ("Salem, OR", TIER, 1),
        ("Salem, MA", EXCLUDE, None),  # same city name, wrong state
        ("Portland, ME", EXCLUDE, None),
        ("Irvine, CA", EXCLUDE, None),  # CA is a real state code, in no tier here
        ("Austin, TX, United States", EXCLUDE, None),  # on-site, not "US-wide"
        # Place terms
        ("Denver", TIER, 1),
        ("Salem", TIER, 1),
        ("Quebec City", TIER, 2),
        # A tier with no codes matches its places in any state
        ("Texarkana, TX", TIER, 3),
        ("Texarkana, AR", TIER, 3),
        # Country terms only count bare or remote
        ("Example Country", TIER, 2),
        ("Remote - Example Country", TIER, 2),
        ("remote example country", TIER, 2),
        ("Lakeside, Example Country", EXCLUDE, None),
        # Remote tied to another country
        ("Remote - India", EXCLUDE, None),
        ("Remote, Germany", EXCLUDE, None),
        ("EMEA (Remote)", EXCLUDE, None),
        # No tier
        ("Bangalore, India", EXCLUDE, None),  # on-site abroad: no tier, not "remote tied"
        ("Atlanta, Georgia", EXCLUDE, None),
        # Ambiguous: flag, don't guess
        ("Portland", FLAG, None),
        ("Springfield", FLAG, None),
        ("CA", FLAG, None),  # California or Canada
        # Remote, but the rest of the text isn't recognized
        ("Remote - European Union", FLAG, None),
        ("Remote, Anywhere", FLAG, None),
        ("", FLAG, None),
    ],
)
def test_placement(location: str, placement: Placement, tier: int | None) -> None:
    result = place(location, RULES)
    assert result.placement is placement, result.reason
    assert (result.tier.number if result.tier else None) == tier


def test_best_tier_wins_across_segments() -> None:
    result = place("Houston, TX; Texarkana, AR; Remote - US", RULES)
    assert result.placement is TIER and result.tier is not None and result.tier.number == 1
    assert "'Remote - US' matched us" in result.reason


def test_any_tier_beats_a_flag_or_exclusion() -> None:
    result = place("Portland; Remote - India; Texarkana, TX", RULES)
    assert result.tier is not None and result.tier.number == 3


def test_flag_beats_exclusion_when_nothing_is_placed() -> None:
    result = place("Remote - India; Portland", RULES)
    assert result.placement is FLAG
    assert "'Portland' is ambiguous (portland)" in result.reason


def test_all_exclusion_reasons_are_kept() -> None:
    result = place("Salem, MA; Remote - India", RULES)
    assert result.placement is EXCLUDE
    assert result.reason == (
        "'Salem, MA' is in MA, which is in no tier; 'Remote - India' is remote tied to india"
    )


@pytest.mark.parametrize(
    ("location", "placement"),
    [
        ("Atlanta, Georgia", FLAG),  # remote per the board, city fits no tier: flag
        ("United Kingdom", FLAG),  # not in non_us_remote_terms here, so unknown: flag
        ("UK", EXCLUDE),  # remote per the board and tied to the UK: exclude
        ("United States", TIER),
    ],
)
def test_board_remote_flag_changes_unplaced_cities(location: str, placement: Placement) -> None:
    assert place(location, RULES, board_says_remote=True).placement is placement


def test_reason_names_tier_segment_and_term() -> None:
    result = place("Salem, OR", RULES)
    assert result.reason == "tier 1 (Remote US / Mountain): 'Salem, OR' matched OR"


def test_lowercase_after_comma_is_not_a_code() -> None:
    # "or" here is a word, not Oregon.
    assert place("Remote, or hybrid", RULES).placement is FLAG


def _rules_with_remote_only_in_tier2(tmp_path: Path, tier2_remote: str) -> FilterRules:
    path = tmp_path / "filters.toml"
    path.write_text(
        '[roles]\ntitle_include = ["engineer"]\n[domain]\nterms = []\n[work]\nterms = []\n'
        '[location.tier1]\nlabel = "On-site only"\nplace_terms = ["denver"]\n'
        f'[location.tier2]\nlabel = "Remote OK"\nremote_terms = [{tier2_remote}]\n',
        encoding="utf-8",
    )
    return load_filter_rules(path)


def test_plain_remote_goes_to_the_first_tier_that_lists_it(tmp_path: Path) -> None:
    # Tier order is preference order, but a tier that doesn't accept remote
    # is skipped rather than chosen.
    rules = _rules_with_remote_only_in_tier2(tmp_path, '"remote"')
    result = place("Remote", rules)
    assert result.placement is TIER and result.tier is not None and result.tier.number == 2


def test_plain_remote_with_no_tier_accepting_it_is_flagged(tmp_path: Path) -> None:
    # "remote" is always recognized as a remote marker, even when no tier
    # lists it; then nothing accepts it, so it needs a human look.
    rules = _rules_with_remote_only_in_tier2(tmp_path, '"work from home"')
    result = place("Remote", rules)
    assert result.placement is FLAG
    assert "no tier recognizes 'Remote'" in result.reason
