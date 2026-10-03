import json
from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture() -> Callable[[str], object]:
    """Return a function that loads a saved JSON response by file name."""

    def load(name: str) -> object:
        with (FIXTURES / name).open(encoding="utf-8") as f:
            return json.load(f)

    return load
