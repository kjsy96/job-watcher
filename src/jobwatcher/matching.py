"""Whole-word, case-insensitive term matching used by every filter rule."""

import re
from collections.abc import Iterable


def _pattern(term: str) -> re.Pattern[str]:
    # Whole words only: "mine" must not match "determine", and "us" must not
    # match "business". (?<!\w) and (?!\w) work even when the term itself
    # starts or ends with punctuation, like "u.s." or "p&id".
    # Spaces inside a phrase match any run of whitespace, so a phrase still
    # matches when a description wraps it across a line break.
    words = [re.escape(word) for word in term.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


class Terms:
    """A list of config terms, compiled once and matched many times.

    hits() returns the terms that occur in the text, in config order and
    spelled as in the config, so a reason can quote them exactly.
    """

    def __init__(self, terms: Iterable[str]) -> None:
        self.terms: tuple[str, ...] = tuple(terms)
        self._patterns = [(term, _pattern(term)) for term in self.terms]

    def hits(self, *texts: str) -> list[str]:
        return [term for term, pattern in self._patterns if any(pattern.search(t) for t in texts)]

    def any(self, *texts: str) -> bool:
        return any(pattern.search(t) for _, pattern in self._patterns for t in texts)

    def remove(self, text: str) -> str:
        """The text with every occurrence of every term replaced by a space."""
        for _, pattern in self._patterns:
            text = pattern.sub(" ", text)
        return text

    def __bool__(self) -> bool:
        return bool(self.terms)

    def __repr__(self) -> str:
        return f"Terms({list(self.terms)!r})"
