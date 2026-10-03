"""Helpers for checking the shape of JSON returned by a job board.

JSON from the network is untyped. These helpers check the one value a
fetcher needs and either return it with a precise type or raise
ShapeError saying exactly where the response differed. Fetchers turn
ShapeError into SourceError, so a changed response format is reported
as a source failure instead of quietly producing wrong postings.
"""

from datetime import datetime


class ShapeError(ValueError):
    """The response does not have the shape a fetcher relies on."""


def require_dict(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ShapeError(f"{where}: expected an object, got {type(value).__name__}")
    return value


def require_list(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise ShapeError(f"{where}: expected a list, got {type(value).__name__}")
    return value


def require_str(obj: dict[str, object], key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise ShapeError(f"{where}.{key}: expected a string, got {type(value).__name__}")
    return value


def require_id(obj: dict[str, object], key: str, where: str) -> str:
    """A job ID, which boards send as either an integer or a string."""
    value = obj.get(key)
    # bool is a subclass of int in Python, so exclude it explicitly.
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ShapeError(f"{where}.{key}: expected a job ID, got {value!r}")


def optional_datetime(obj: dict[str, object], key: str, where: str) -> datetime | None:
    """An ISO 8601 timestamp with a timezone, or None if absent.

    A value that is present but unreadable is an error, not None: it
    means the format changed, and that should be reported.
    """
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if parsed.tzinfo is not None:
                return parsed
    raise ShapeError(f"{where}.{key}: expected an ISO 8601 timestamp with timezone, got {value!r}")
