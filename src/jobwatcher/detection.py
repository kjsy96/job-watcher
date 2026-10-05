"""Find a discovered employer's Greenhouse, Lever, or Ashby job board (issue 2b.3).

One guess per platform: the board name built from the employer's name. A
board that exists isn't proof it's the same employer (another organization
can own the same name), so a board only counts as confirmed when one of its
job titles matches a title from the employer's Adzuna ads.

Never uses Adzuna's job links to find a board: those are Adzuna's paid
click-throughs.
"""

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

import httpx

from jobwatcher.discovery import Candidate, employer_key
from jobwatcher.models import SourceName
from jobwatcher.store import BoardCheck, Store

# Lightweight list endpoints (no descriptions needed to compare titles).
_ENDPOINTS: dict[SourceName, str] = {
    SourceName.GREENHOUSE: "https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
    SourceName.LEVER: "https://api.lever.co/v0/postings/{board}?mode=json",
    SourceName.ASHBY: "https://api.ashbyhq.com/posting-api/job-board/{board}",
}
SECONDS_BETWEEN_REQUESTS = 1.0
_WORDS = re.compile(r"[a-z0-9]+")
_MIN_CONTAINED_TITLE = 12  # shorter titles must match exactly


@dataclass(frozen=True, slots=True)
class Detection:
    check: BoardCheck | None  # None when a request failed: retry on a later run
    error: str | None
    requests: int


def guess_board_name(employer: str) -> str:
    """'Heidelberg Materials' -> 'heidelbergmaterials'; 'The BIG Jobsite Inc.' -> 'bigjobsite'.

    Most boards use the company name lowercased with spaces and punctuation
    removed. It's one guess; employers whose board uses another name come
    out as not found, for the owner to check by hand.
    """
    words = employer_key(employer).split()
    if words and words[0] == "the":
        # "The BIG Jobsite" -> "bigjobsite"; a name that is only "The ..." plus
        # legal words ("The Company") leaves nothing usable to guess.
        words = words[1:]
    return "".join(words)


def _normalize_title(title: str) -> str:
    return " ".join(_WORDS.findall(title.lower()))


def titles_match(board_titles: Iterable[str], ad_titles: Iterable[str]) -> str | None:
    """The first board title that matches an ad title, or None.

    Equal after normalizing case and punctuation, or, for titles of at
    least 12 characters, one contained in the other ("Field Engineer II"
    vs "Senior Field Engineer II - Remote").
    """
    ads = [a for a in (_normalize_title(t) for t in ad_titles) if a]
    for original in board_titles:
        title = _normalize_title(original)
        if not title:
            continue
        for ad in ads:
            if title == ad:
                return original
            short, long = sorted((title, ad), key=len)
            if len(short) >= _MIN_CONTAINED_TITLE and short in long:
                return original
    return None


def _titles(source: SourceName, payload: object) -> list[str] | None:
    """Job titles from a board response, or None if the shape is unexpected."""
    items: object
    key = "text" if source is SourceName.LEVER else "title"
    if source is SourceName.LEVER:
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("jobs")
    else:
        return None
    if not isinstance(items, list):
        return None
    return [str(i[key]) for i in items if isinstance(i, dict) and isinstance(i.get(key), str)]


def detect_board(
    employer: str,
    ad_titles: list[str],
    client: httpx.Client,
    now: datetime,
    pause: Callable[[float], None] = time.sleep,
) -> Detection:
    """Try the guessed board name on each platform; stop at the first confirmed."""
    key = employer_key(employer)
    board = guess_board_name(employer)
    possible: BoardCheck | None = None
    requests = 0
    if not board:
        return Detection(
            BoardCheck(key, employer, "not_found", None, None, "no usable board name", now),
            None,
            0,
        )

    for source, template in _ENDPOINTS.items():
        if requests:
            pause(SECONDS_BETWEEN_REQUESTS)
        requests += 1
        try:
            response = client.get(template.format(board=board))
        except httpx.HTTPError as exc:
            return Detection(None, f"{source}: request failed: {type(exc).__name__}", requests)
        if response.status_code == 404:
            continue  # no board under this name on this platform
        if response.status_code != 200:
            return Detection(None, f"{source}: HTTP {response.status_code}", requests)
        try:
            titles = _titles(source, response.json())
        except ValueError:
            titles = None
        if titles is None:
            return Detection(None, f"{source}: unexpected response", requests)

        matched = titles_match(titles, ad_titles)
        if matched is not None:
            return Detection(
                BoardCheck(
                    key, employer, "confirmed", source, board, f"board has '{matched}'", now
                ),
                None,
                requests,
            )
        if possible is None:
            detail = (
                f"board exists with {len(titles)} jobs, none matching the Adzuna ads"
                if titles
                else "board exists but has no open jobs"
            )
            possible = BoardCheck(key, employer, "possible", source, board, detail, now)

    if possible is not None:
        return Detection(possible, None, requests)
    return Detection(
        BoardCheck(
            key,
            employer,
            "not_found",
            None,
            None,
            f"no Greenhouse, Lever, or Ashby board named '{board}'",
            now,
        ),
        None,
        requests,
    )


@dataclass(frozen=True, slots=True)
class DetectionSummary:
    checked: int  # looked up this run
    from_earlier: int  # already checked on a previous run
    not_checked: int  # beyond this run's limit; later runs continue
    requests: int
    errors: list[str]


def detect_boards(
    candidates: list[Candidate],
    store: Store,
    client: httpx.Client,
    now: datetime,
    limit: int,
    pause: Callable[[float], None] = time.sleep,
) -> DetectionSummary:
    """Attach a board to each candidate: remembered results first, then new
    lookups for the top-ranked unchecked candidates, up to the limit.

    Candidates must already be in rank order, so the limit spends requests
    on the most promising employers first.
    """
    checked = from_earlier = not_checked = requests = 0
    errors: list[str] = []
    for candidate in candidates:
        earlier = store.board_check(candidate.key)
        if earlier is not None:
            candidate.board = earlier
            from_earlier += 1
            continue
        if checked >= limit:
            not_checked += 1
            continue
        if requests:
            pause(SECONDS_BETWEEN_REQUESTS)
        detection = detect_board(candidate.name, candidate.titles, client, now, pause)
        checked += 1
        requests += detection.requests
        if detection.check is not None:
            store.record_board_check(detection.check)
            candidate.board = detection.check
        else:
            candidate.board_error = detection.error
            errors.append(f"{candidate.name}: {detection.error}")
    return DetectionSummary(checked, from_earlier, not_checked, requests, errors)
