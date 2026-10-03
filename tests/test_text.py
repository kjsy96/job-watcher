from jobwatcher.sources.text import html_to_text


def test_tags_are_stripped() -> None:
    assert html_to_text("<p>Hello <strong>world</strong></p>") == "Hello world"


def test_block_tags_keep_words_apart() -> None:
    # Without line breaks at block tags this would read "Requirements10% travel".
    text = html_to_text("<h4>Requirements</h4><ul><li>10% travel</li><li>CO</li></ul>")
    assert text == "Requirements\n10% travel\nCO"


def test_entities_are_decoded() -> None:
    assert html_to_text("<p>R&amp;D &mdash; Ops&nbsp;team</p>") == "R&D \N{EM DASH} Ops team"


def test_whitespace_and_blank_lines_collapse() -> None:
    assert html_to_text("<p>a   b</p>\n\n<p></p>\n<p>  c </p>") == "a b\nc"


def test_script_and_style_content_is_dropped() -> None:
    assert html_to_text("<style>p{}</style><p>Keep</p><script>x()</script>") == "Keep"


def test_unclosed_tags_do_not_lose_text() -> None:
    assert html_to_text("<div><p>Cut off mid") == "Cut off mid"


def test_empty_input() -> None:
    assert html_to_text("") == ""
