import json
from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolate_from_real_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test from an empty temporary folder.

    The commands default to paths relative to the working directory
    (data/jobwatcher.db, config/companies.toml, reports/, .env). From the
    repo folder those are the owner's real, private files, so a test that
    forgets to pass a path must never reach them. Tests find their own
    fixtures through absolute paths built from __file__.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def load_fixture() -> Callable[[str], object]:
    """Return a function that loads a saved JSON response by file name."""

    def load(name: str) -> object:
        with (FIXTURES / name).open(encoding="utf-8") as f:
            return json.load(f)

    return load
