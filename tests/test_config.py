from pathlib import Path

import pytest

from jobwatcher.config import ConfigError, load_companies
from jobwatcher.models import Company, SourceName

REPO_ROOT = Path(__file__).parent.parent


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "companies.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_committed_example_is_valid() -> None:
    # Keeps config/companies.example.toml from drifting out of date.
    companies = load_companies(REPO_ROOT / "config" / "companies.example.toml")
    assert companies and all(isinstance(c, Company) for c in companies)


def test_loads_companies_in_order(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
[[company]]
name = "Acme"
source = "greenhouse"
board = "acme"
sector = "mining"

[[company]]
name = "  Beta  "
source = "ashby"
board = "Beta"
""",
    )
    assert load_companies(path) == [
        Company("Acme", SourceName.GREENHOUSE, "acme", "mining"),
        Company("Beta", SourceName.ASHBY, "Beta", ""),  # sector optional, name trimmed
    ]


def test_missing_file_explains_how_to_create_it(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"not found: copy config/companies\.example\.toml"):
        load_companies(tmp_path / "companies.toml")


def test_invalid_toml_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_companies(write(tmp_path, "[[company]\nname = "))


@pytest.mark.parametrize("text", ["", "[settings]\nx = 1\n", "company = 'acme'\n"])
def test_no_company_entries_is_an_error(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError, match=r"no \[\[company\]\] entries"):
        load_companies(write(tmp_path, text))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('source = "greenhouse"\nboard = "acme"', "'name' is required"),
        ('name = " "\nsource = "greenhouse"\nboard = "acme"', "'name' is required"),
        ('name = "Acme"\nboard = "acme"', "'source' must be one of greenhouse, lever, ashby"),
        ('name = "Acme"\nsource = "workday"\nboard = "acme"', "got 'workday'"),
        ('name = "Acme"\nsource = "lever"', "'board' is required"),
        ('name = "Acme"\nsource = "lever"\nboard = ""', "'board' is required"),
        ('name = "Acme"\nsource = "lever"\nboard = " acme"', "just the identifier"),
        (
            'name = "Acme"\nsource = "lever"\nboard = "https://jobs.lever.co/acme"',
            "just the identifier",
        ),
        ('name = "Acme"\nsource = "lever"\nboard = "acme"\nsector = 3', "'sector' must be text"),
        ('name = "Acme"\nsource = "lever"\nbaord = "acme"', r"unknown key\(s\) \['baord'\]"),
    ],
)
def test_invalid_company_names_the_problem(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_companies(write(tmp_path, f"[[company]]\n{body}\n"))


def test_error_names_the_company(tmp_path: Path) -> None:
    text = (
        '[[company]]\nname = "Good"\nsource = "lever"\nboard = "good"\n\n'
        '[[company]]\nname = "Typo Co"\nsource = "lever"\nboard = "typo/co"\n'
    )
    with pytest.raises(ConfigError, match=r"company #2 \(Typo Co\)"):
        load_companies(write(tmp_path, text))


def test_duplicate_board_is_rejected(tmp_path: Path) -> None:
    text = (
        '[[company]]\nname = "Acme"\nsource = "lever"\nboard = "acme"\n\n'
        '[[company]]\nname = "Acme Again"\nsource = "lever"\nboard = "acme"\n'
    )
    with pytest.raises(ConfigError, match="'Acme Again' and 'Acme' both use lever:acme"):
        load_companies(write(tmp_path, text))


def test_same_board_name_on_different_platforms_is_fine(tmp_path: Path) -> None:
    text = (
        '[[company]]\nname = "A"\nsource = "lever"\nboard = "acme"\n\n'
        '[[company]]\nname = "B"\nsource = "greenhouse"\nboard = "acme"\n'
    )
    assert len(load_companies(write(tmp_path, text))) == 2
