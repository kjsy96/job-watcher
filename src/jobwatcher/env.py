"""Read secrets from the environment or the gitignored .env file.

No third-party package: the format is just NAME=value lines, with # comments.
Values are returned to the caller and never printed or logged here.
"""

import os
import re
from pathlib import Path

from jobwatcher.config import ConfigError

_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def read_env_file(path: Path) -> dict[str, str]:
    """NAME=value pairs from a .env file; an absent file gives no values."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _LINE.match(line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def require(names: tuple[str, ...], env_file: Path) -> dict[str, str]:
    """Each name's value, from the real environment first, then the .env file.

    A missing or empty value is a ConfigError that names the variable but
    never shows any value.
    """
    from_file = read_env_file(env_file)
    found: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        value = os.environ.get(name) or from_file.get(name, "")
        if value:
            found[name] = value
        else:
            missing.append(name)
    if missing:
        raise ConfigError(
            f"missing {', '.join(missing)}: set them in {env_file} "
            "(copy .env.example) or in the environment"
        )
    return found
