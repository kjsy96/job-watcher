from pathlib import Path

import pytest

from jobwatcher.config import ConfigError
from jobwatcher.filter_config import CA_PROVINCE_CODES, US_STATE_CODES, load_filter_rules

REPO_ROOT = Path(__file__).parent.parent
TEST_RULES = Path(__file__).parent / "fixtures" / "filters_test.toml"

MINIMAL = """
[roles]
title_include = ["engineer"]
[domain]
terms = []
[work]
terms = []
[location.tier1]
label = "Anywhere"
remote_terms = ["remote"]
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "filters.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_committed_example_is_valid() -> None:
    rules = load_filter_rules(REPO_ROOT / "config" / "filters.example.toml")
    assert [t.number for t in rules.tiers] == [1, 2, 3]
    assert rules.travel_max_percent == 40


def test_test_rules_load() -> None:
    rules = load_filter_rules(TEST_RULES)
    assert [t.number for t in rules.tiers] == [1, 2, 3]
    assert rules.tiers[0].region_codes == {"CO", "OR"}
    assert rules.tiers[1].region_codes == {"QC"}
    assert rules.tiers[1].requires_sponsorship is True
    assert rules.tiers[2].region_codes == frozenset()


def test_minimal_config_is_enough(tmp_path: Path) -> None:
    rules = load_filter_rules(write(tmp_path, MINIMAL))
    assert rules.travel_max_percent is None
    assert not rules.description_flag_terms


def test_terms_are_trimmed(tmp_path: Path) -> None:
    rules = load_filter_rules(write(tmp_path, MINIMAL.replace('["engineer"]', '["vp ", " lead"]')))
    assert rules.title_include.terms == ("vp", "lead")


def test_tiers_are_sorted_by_number(tmp_path: Path) -> None:
    text = MINIMAL + '\n[location.tier3]\nlabel = "C"\n\n[location.tier2]\nlabel = "B"\n'
    assert [t.label for t in load_filter_rules(write(tmp_path, text)).tiers] == [
        "Anywhere",
        "B",
        "C",
    ]


def test_region_code_tables_are_disjoint() -> None:
    # A code must mean one place: "CA" is California, never a province code.
    assert not US_STATE_CODES & CA_PROVINCE_CODES
    assert len(US_STATE_CODES) == 52 and len(CA_PROVINCE_CODES) == 13


def test_missing_file_explains_the_fix(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"copy config/filters\.example\.toml"):
        load_filter_rules(tmp_path / "filters.toml")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (('[roles]\ntitle_include = ["engineer"]', ""), r"\[roles\] section is required"),
        (('title_include = ["engineer"]', "title_include = []"), "at least one term"),
        (('title_include = ["engineer"]', 'title_include = "engineer"'), "list of text terms"),
        (('title_include = ["engineer"]', 'title_include = ["engineer", ""]'), "empty term"),
        (('title_include = ["engineer"]', 'titel_include = ["engineer"]'), r"unknown key\(s\)"),
        (("[work]", "[wrok]"), r"unknown section\(s\) \['wrok'\]"),
        (('[location.tier1]\nlabel = "Anywhere"\nremote_terms = ["remote"]', ""), "location"),
        (('label = "Anywhere"', 'label = "Anywhere"\nstate_codes = ["XX"]'), "unknown code"),
        (('label = "Anywhere"', 'label = "Anywhere"\nprovince_codes = ["CA"]'), "unknown code"),
        (
            ('label = "Anywhere"', 'label = "Anywhere"\nrequires_sponsorship = "yes"'),
            "true or false",
        ),
        (('label = "Anywhere"', 'label = "Anywhere"\nplace = ["x"]'), r"unknown key\(s\)"),
        (("[location.tier1]", "[location.first]"), "tiers are named tier1"),
    ],
)
def test_invalid_config_names_the_problem(
    tmp_path: Path, change: tuple[str, str], message: str
) -> None:
    old, new = change
    assert old in MINIMAL
    with pytest.raises(ConfigError, match=message):
        load_filter_rules(write(tmp_path, MINIMAL.replace(old, new)))


@pytest.mark.parametrize("value", ["true", "-1", "101", "40.5", '"40"'])
def test_travel_max_percent_must_be_0_to_100(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="max_percent"):
        load_filter_rules(write(tmp_path, MINIMAL + f"\n[travel]\nmax_percent = {value}\n"))
