import pytest

from jobwatcher.matching import Terms


@pytest.mark.parametrize(
    ("term", "text", "expected"),
    [
        ("mine", "Work at an open-pit mine.", True),
        ("mine", "Help determine requirements.", False),  # not inside a longer word
        ("mine", "MINE SITE", True),  # case-insensitive
        ("us", "Remote - US", True),
        ("us", "Grow the business", False),
        ("u.s.", "Based in the U.S. only", True),
        ("p&id", "Read P&ID drawings", True),
        ("data validation", "data\nvalidation of sensors", True),  # phrase across a line break
        ("data validation", "data quality validation", False),  # words must be adjacent
        ("field service", "Field Services Engineer", False),  # whole words only, no plurals
    ],
)
def test_whole_word_case_insensitive(term: str, text: str, expected: bool) -> None:
    assert Terms([term]).any(text) is expected


def test_hits_are_in_config_order_and_spelled_as_configured() -> None:
    terms = Terms(["Commissioning", "mining", "troubleshooting"])
    assert terms.hits("Troubleshooting and MINING work, then commissioning") == [
        "Commissioning",
        "mining",
        "troubleshooting",
    ]


def test_hits_across_several_texts() -> None:
    terms = Terms(["mining", "cooling tower"])
    assert terms.hits("description about mining", "cooling tower maker") == [
        "mining",
        "cooling tower",
    ]


def test_remove_replaces_every_occurrence() -> None:
    assert Terms(["remote", "us"]).remove("Remote - US (remote)").split() == ["-", "(", ")"]


def test_empty_terms_match_nothing() -> None:
    empty = Terms([])
    assert not empty
    assert empty.hits("anything") == []
    assert not empty.any("anything")
