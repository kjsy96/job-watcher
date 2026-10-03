import pytest

import jobwatcher
from jobwatcher.__main__ import main


def test_package_exposes_version() -> None:
    assert jobwatcher.__version__ == "0.0.0"


def test_main_with_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: jobwatcher" in capsys.readouterr().out


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "jobwatcher 0.0.0" in capsys.readouterr().out
