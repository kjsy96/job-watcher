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
    ],
)
def test_whole_word_case_insensitive(term: str, text: str, expected: bool) -> None:
    assert Terms([term]).any(text) is expected


@pytest.mark.parametrize(
    ("term", "text"),
    [
        ("field service", "Field Services Engineer"),  # plural of a phrase's last word
        ("solutions", "Solution Architect"),  # singular of a plural term
        ("solution", "Solutions Consultant"),
        ("deployment", "Safety Manager - Mining Deployments"),
        ("process", "Chemical processes"),
        ("utility", "Electric utilities"),
        ("utilities", "A water utility"),
        ("mine", "Underground mines"),
        ("switch", "Network switches"),
    ],
)
def test_singular_and_plural_both_match(term: str, text: str) -> None:
    assert Terms([term]).any(text)


@pytest.mark.parametrize(
    ("term", "text"),
    [
        ("us", "Remote - USS Enterprise"),  # short terms stay exact
        ("us", "Remote - U"),
        ("ca", "Cas Remote"),
        ("vp", "VPs of sales"),
        ("field service", "Fields service"),  # only the last word is inflected
        ("deployed", "deploys"),  # not a plural, a different word form
        ("mine", "Mineral exploration"),  # still whole words only
        ("solution", "solutioning"),
    ],
)
def test_inflection_does_not_overreach(term: str, text: str) -> None:
    assert not Terms([term]).any(text)


def test_reasons_quote_the_configured_spelling() -> None:
    assert Terms(["solutions"]).hits("Solution Architect") == ["solutions"]


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


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_term_is_rejected(blank: str) -> None:
    # A blank term would compile to a pattern that matches every text.
    with pytest.raises(ValueError, match="must not be blank"):
        Terms(["mining", blank])


def test_vowel_y_plural_adds_s() -> None:
    # "survey" -> "surveys", not "surveies" (only consonant + y becomes "ies").
    terms = Terms(["site survey"])
    assert terms.any("Site surveys at remote mines")
    assert not terms.any("Site surveies")


def test_repr_lists_the_terms() -> None:
    assert repr(Terms(["mining", "cement"])) == "Terms(['mining', 'cement'])"
