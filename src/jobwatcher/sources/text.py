"""Turn job description HTML into plain text for storage and filtering."""

import re
from html.parser import HTMLParser
from typing import override

# Tags that end a visual line or block. Text on either side must not run
# together ("Requirements</h4><ul><li>10% travel" must not become
# "Requirements10% travel"), or whole-word filter matching would miss terms.
_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "tr", "table"}
)
_SKIP_TAGS = frozenset({"script", "style"})
_SPACES = re.compile("[ \t\r\f\v\u00a0]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        # convert_charrefs=True makes the parser decode entities such as
        # &amp; and &nbsp; inside text, so they never reach the stored text.
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    @override
    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """Strip tags, decode entities, and normalize whitespace.

    Block-level tags become line breaks, runs of spaces (including
    non-breaking spaces) collapse to one, and blank lines collapse to one
    break. The result is readable in a report and safe for whole-word
    matching.
    """
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    text = _SPACES.sub(" ", "".join(parser.parts))
    lines = (line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n", "\n".join(lines)).strip()
