"""Approve or reject discovered employers (issue 2b.4).

approve adds an employer to config/companies.toml using its remembered job
board, so the daily run starts watching it. reject adds it to
config/rejected_companies.toml with a reason, so discovery never proposes
it again. Nothing is ever added automatically: these only run when the
owner types them.

Both files are the owner's hand-edited, commented config, so entries are
appended as text (a TOML library would drop the comments), then the file
is validated; if validation fails, the original text is put back.
"""

import json
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jobwatcher.config import ConfigError, load_companies
from jobwatcher.discovery import employer_key
from jobwatcher.models import SourceName
from jobwatcher.store import Store


@dataclass(frozen=True, slots=True)
class Approval:
    name: str
    source: SourceName
    board: str
    sector: str
    warning: str | None  # e.g. the board was only "possible"


def _toml_string(value: str) -> str:
    # A JSON string is also a valid TOML basic string, escapes included.
    return json.dumps(value, ensure_ascii=False)


def _append(path: Path, block: str, validate: Callable[[Path], object]) -> None:
    """Append a block of TOML text, validate the file, and undo on failure."""
    original = path.read_text(encoding="utf-8") if path.exists() else None
    text = original or ""
    if text and not text.endswith("\n"):
        text += "\n"
    if text:
        text += "\n"  # a blank line between entries, but not at the top of a new file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + block, encoding="utf-8")
    try:
        validate(path)
    except (ConfigError, tomllib.TOMLDecodeError) as exc:
        if original is None:
            path.unlink()
        else:
            path.write_text(original, encoding="utf-8")
        raise ConfigError(
            f"{path} would have become invalid, so it was left unchanged: {exc}"
        ) from exc


def _rejected_names(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as f:
        data = tomllib.load(f)
    return [
        str(e["name"])
        for e in data.get("company", [])
        if isinstance(e, dict) and isinstance(e.get("name"), str)
    ]


def approve(
    name: str,
    companies_path: Path,
    store: Store,
    today: date,
    sector: str | None = None,
    source: SourceName | None = None,
    board: str | None = None,
) -> Approval:
    """Add a discovered employer to companies.toml.

    The board comes from discovery's remembered lookup, or from source and
    board given explicitly (for an employer whose board the owner found).
    """
    key = employer_key(name)
    existing = load_companies(companies_path) if companies_path.exists() else []
    if any(employer_key(c.name) == key for c in existing):
        raise ConfigError(f"{name!r} is already in {companies_path}")

    check = store.board_check(key)
    if (source is None) != (board is None):
        raise ConfigError("give both --source and --board, or neither")
    warning: str | None = None
    if source is not None and board is not None:
        chosen_source, chosen_board, note = source, board.strip(), "board given by the owner"
        display = check.employer_name if check else name.strip()
    elif check is None:
        raise ConfigError(
            f"no job board lookup is remembered for {name!r}. Check the spelling against the "
            "discovery report, or give --source and --board if you found its board yourself."
        )
    elif check.status == "not_found" or check.source is None or check.board is None:
        raise ConfigError(
            f"no readable board was found for {check.employer_name!r} ({check.detail}). "
            "If you found its board yourself, approve it with --source and --board."
        )
    else:
        chosen_source, chosen_board = check.source, check.board
        display = check.employer_name
        note = f"board {check.status}: {check.detail}"
        if check.status == "possible":
            warning = (
                f"the {chosen_source}:{chosen_board} board was only a possible match "
                "(no job titles matched), so check its next report entries are this employer"
            )

    if any((c.source, c.board) == (chosen_source, chosen_board) for c in existing):
        taken = next(
            c.name for c in existing if (c.source, c.board) == (chosen_source, chosen_board)
        )
        raise ConfigError(f"{chosen_source}:{chosen_board} is already used by {taken!r}")

    chosen_sector = sector if sector is not None else ""
    block = (
        f"# Added by 'jobwatcher approve' on {today:%Y-%m-%d} ({note})\n"
        "[[company]]\n"
        f"name = {_toml_string(display)}\n"
        f"source = {_toml_string(chosen_source)}\n"
        f"board = {_toml_string(chosen_board)}\n"
        f"sector = {_toml_string(chosen_sector)}\n"
    )
    _append(companies_path, block, load_companies)
    return Approval(display, chosen_source, chosen_board, chosen_sector, warning)


def _validate_rejected(path: Path) -> None:
    with path.open("rb") as f:
        tomllib.load(f)


def reject(name: str, reason: str, rejected_path: Path, companies_path: Path, today: date) -> None:
    """Add an employer to rejected_companies.toml so discovery skips it for good."""
    if not reason.strip():
        raise ConfigError("give a reason, so the decision can be reviewed later")
    key = employer_key(name)
    if companies_path.exists() and any(
        employer_key(c.name) == key for c in load_companies(companies_path)
    ):
        raise ConfigError(
            f"{name!r} is in {companies_path}; remove it there first if it should be rejected"
        )
    if any(employer_key(n) == key for n in _rejected_names(rejected_path)):
        raise ConfigError(f"{name!r} is already rejected in {rejected_path}")

    header = (
        ""
        if rejected_path.exists()
        else "# PRIVATE: gitignored. Employers discovery should never propose again.\n"
        "# Added by 'jobwatcher reject'; edit by hand if a decision changes.\n"
    )
    block = (
        header + "[[company]]\n"
        f"name = {_toml_string(name.strip())}\n"
        f"reason = {_toml_string(reason.strip())}\n"
        f"rejected_on = {_toml_string(f'{today:%Y-%m-%d}')}\n"
    )
    _append(rejected_path, block, _validate_rejected)
