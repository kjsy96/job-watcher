"""Load and validate config/companies.toml.

Every problem stops the run before any network request and names the
company and field at fault. A typo in the company list should be a clear
error at startup, not a confusing fetch failure later.
"""

import tomllib
from pathlib import Path

from jobwatcher.models import Company, SourceName

_EXAMPLE_HINT = (
    "copy config/companies.example.toml to config/companies.toml and edit it "
    "(PowerShell: Copy-Item config\\companies.example.toml config\\companies.toml)"
)
_KNOWN_KEYS = frozenset({"name", "source", "board", "sector"})


class ConfigError(Exception):
    """The config file is missing or invalid. The message says how to fix it."""


def load_companies(path: Path) -> list[Company]:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found: {_EXAMPLE_HINT}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    entries = data.get("company")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path} has no [[company]] entries")

    companies: list[Company] = []
    seen: dict[tuple[SourceName, str], str] = {}
    for i, raw in enumerate(entries, start=1):
        company = _company(raw, f"{path}, company #{i}")
        key = (company.source, company.board)
        if key in seen:
            # Two entries for one board would fetch it twice and record it
            # twice under two names.
            raise ConfigError(
                f"{path}: {company.name!r} and {seen[key]!r} both use "
                f"{company.source}:{company.board}"
            )
        seen[key] = company.name
        companies.append(company)
    return companies


def _company(raw: object, where: str) -> Company:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a [[company]] table")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{where}: 'name' is required")
    where = f"{where} ({name.strip()})"

    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        # Catches typos like 'baord' that would otherwise be silently ignored.
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")

    source = raw.get("source")
    valid = ", ".join(s.value for s in SourceName)
    if not isinstance(source, str) or source not in {s.value for s in SourceName}:
        raise ConfigError(f"{where}: 'source' must be one of {valid}, got {source!r}")

    board = raw.get("board")
    if not isinstance(board, str) or not board.strip():
        raise ConfigError(f"{where}: 'board' is required")
    if board.strip() != board or "/" in board:
        raise ConfigError(
            f"{where}: 'board' should be just the identifier from the job board URL, got {board!r}"
        )

    sector = raw.get("sector", "")
    if not isinstance(sector, str):
        raise ConfigError(f"{where}: 'sector' must be text")

    return Company(name=name.strip(), source=SourceName(source), board=board, sector=sector)
