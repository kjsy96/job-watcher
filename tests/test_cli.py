from importlib.metadata import version

import pytest

import jobwatcher
from jobwatcher.__main__ import main

# Read from installed metadata (pyproject.toml), so a version bump never
# needs a test edit. The tests still prove the version flows through.
INSTALLED_VERSION = version("jobwatcher")


def test_package_exposes_installed_version() -> None:
    assert jobwatcher.__version__ == INSTALLED_VERSION


def test_main_with_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: jobwatcher" in capsys.readouterr().out


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert f"jobwatcher {INSTALLED_VERSION}" in capsys.readouterr().out
