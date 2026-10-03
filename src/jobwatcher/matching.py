"""Whole-word, case-insensitive term matching used by every filter rule."""

import re
from collections.abc import Iterable

# Plural and singular forms are only added for words this long. Short terms
# are usually codes or abbreviations ("us", "ca", "uk", "vp") where an extra
# or missing "s" changes the meaning ("us" must never become "u").
_MIN_INFLECT_LENGTH = 4


def _singular_or_plural(word: str) -> str:
    """A regex for a word in either its singular or plural form.

    Covers regular English plurals only: solution/solutions,
    deployment/deployments, process/processes, utility/utilities.
    """
    if len(word) < _MIN_INFLECT_LENGTH or not word.isalpha():
        return re.escape(word)
    lower = word.lower()
    if lower.endswith("ies"):  # utilities -> utility
        return re.escape(word[:-3]) + "(?:y|ies)"
    if lower.endswith("ss"):  # process -> processes
        return re.escape(word) + "(?:es)?"
    if lower.endswith("s"):  # solutions -> solution
        return re.escape(word[:-1]) + "(?:s|es)?"
    if lower.endswith("y") and lower[-2] not in "aeiou":  # utility -> utilities
        return re.escape(word[:-1]) + "(?:y|ies)"
    return re.escape(word) + "(?:s|es)?"  # deployment -> deployments, switch -> switches


def _pattern(term: str) -> re.Pattern[str]:
    # Whole words only: "mine" must not match "determine", and "us" must not
    # match "business". (?<!\w) and (?!\w) work even when the term itself
    # starts or ends with punctuation, like "u.s." or "p&id".
    # Spaces inside a phrase match any run of whitespace, so a phrase still
    # matches when a description wraps it across a line break.
    # Only the last word of a phrase is inflected: "field service" matches
    # "field services", the way English pluralizes a phrase.
    words = [re.escape(word) for word in term.split()]
    words[-1] = _singular_or_plural(term.split()[-1])
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


class Terms:
    """A list of config terms, compiled once and matched many times.

    hits() returns the terms that occur in the text, in config order and
    spelled as in the config, so a reason can quote them exactly.
    """

    def __init__(self, terms: Iterable[str]) -> None:
        self.terms: tuple[str, ...] = tuple(terms)
        if any(not term.strip() for term in self.terms):
            # A blank term compiles to a pattern that matches every text,
            # which would silently turn a rule on for every posting.
            raise ValueError("a term must not be blank")
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
