"""End-to-end tests for `python -m jobwatcher fetch`.

The real fetch path runs (config, fetchers, store, summary), but the HTTP
client is swapped for one whose MockTransport answers from the saved
fixtures, so no request leaves the machine.
"""

import io
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from jobwatcher import fetch
from jobwatcher.__main__ import EXIT_COMPANY_FAILED, EXIT_OK, EXIT_UNUSABLE, main, run_fetch
from jobwatcher.models import PostingStatus
from jobwatcher.store import Store

FIXTURES = Path(__file__).parent / "fixtures"

# The public demo boards the fixtures were captured from.
ROUTES = {
    "https://boards-api.greenhouse.io/v1/boards/gitlab/jobs?content=true": "greenhouse_jobs.json",
    "https://api.lever.co/v0/postings/leverdemo?mode=json": "lever_postings.json",
    "https://api.ashbyhq.com/posting-api/job-board/Ashby": "ashby_jobs.json",
}

CONFIG = """
[[company]]
name = "GitLab"
source = "greenhouse"
board = "gitlab"

[[company]]
name = "Lever Demo"
source = "lever"
board = "leverdemo"

[[company]]
name = "Ashby"
source = "ashby"
board = "Ashby"
"""

WRONG_BOARD = """
[[company]]
name = "Typo Co"
source = "greenhouse"
board = "no-such-board"
"""


class FakeBoards:
    """Serves fixture JSON by URL, 404 otherwise, and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.overrides: dict[str, object] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url in self.overrides:
            return httpx.Response(200, json=self.overrides[url])
        if url in ROUTES:
            data = json.loads((FIXTURES / ROUTES[url]).read_text(encoding="utf-8"))
            return httpx.Response(200, json=data)
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def boards(monkeypatch: pytest.MonkeyPatch) -> FakeBoards:
    fake = FakeBoards()
    real_make_client: Callable[[], httpx.Client] = fetch.make_client

    def make_mock_client() -> httpx.Client:
        # Same headers and timeout as the real client, fake transport.
        real = real_make_client()
        return httpx.Client(
            transport=httpx.MockTransport(fake.handle),
            headers=real.headers,
            timeout=real.timeout,
        )

    monkeypatch.setattr(fetch, "make_client", make_mock_client)
    return fake


def run(config_text: str, tmp_path: Path) -> tuple[int, str]:
    config = tmp_path / "companies.toml"
    config.write_text(config_text, encoding="utf-8")
    out = io.StringIO()
    code = run_fetch(config, tmp_path / "data" / "jobwatcher.db", out)
    return code, out.getvalue()


def summary_row(output: str, name: str) -> list[str]:
    line = next(line for line in output.splitlines() if line.startswith(name + " "))
    return line.split()


def test_first_run_records_everything_as_new(boards: FakeBoards, tmp_path: Path) -> None:
    code, out = run(CONFIG, tmp_path)

    assert code == EXIT_OK
    assert "3 companies: 3 ok, 0 failed. 13 fetched, 13 new, 0 reopened, 0 closed." in out
    assert summary_row(out, "GitLab")[2:] == ["4", "4", "0", "0", "ok"]
    assert summary_row(out, "Lever Demo")[3:] == ["5", "5", "0", "0", "ok"]


def test_second_run_finds_nothing_new(boards: FakeBoards, tmp_path: Path) -> None:
    run(CONFIG, tmp_path)
    code, out = run(CONFIG, tmp_path)

    assert code == EXIT_OK
    assert "13 fetched, 0 new, 0 reopened, 0 closed." in out


def test_one_request_per_company_with_user_agent(boards: FakeBoards, tmp_path: Path) -> None:
    run(CONFIG, tmp_path)

    assert sorted(str(r.url) for r in boards.requests) == sorted(ROUTES)
    for request in boards.requests:
        assert request.headers["User-Agent"].startswith("job-watcher/")
        assert "github.com/kjsy96/job-watcher" in request.headers["User-Agent"]


def test_wrong_board_is_reported_and_others_still_run(boards: FakeBoards, tmp_path: Path) -> None:
    code, out = run(CONFIG + WRONG_BOARD, tmp_path)

    assert code == EXIT_COMPANY_FAILED
    assert "Typo Co" in out and "ERROR: HTTP 404" in out
    assert "4 companies: 3 ok, 1 failed. 13 fetched, 13 new" in out
    assert "none of their postings were marked closed" in out


def test_failed_fetch_never_closes_existing_postings(boards: FakeBoards, tmp_path: Path) -> None:
    run(CONFIG, tmp_path)
    # Next run: GitLab's board returns the wrong shape (a list, not {"jobs": ...}).
    gitlab_url = next(u for u in ROUTES if "greenhouse" in u)
    boards.overrides[gitlab_url] = []

    code, out = run(CONFIG, tmp_path)

    assert code == EXIT_COMPANY_FAILED
    assert "unexpected response shape" in out
    with Store.open(tmp_path / "data" / "jobwatcher.db") as store:
        stored = store.get("greenhouse:gitlab:8638232002")
        assert stored is not None
        assert stored.status is PostingStatus.OPEN


def test_posting_gone_from_board_is_closed(boards: FakeBoards, tmp_path: Path) -> None:
    run(CONFIG, tmp_path)
    lever_url = next(u for u in ROUTES if "lever" in u)
    postings = json.loads((FIXTURES / ROUTES[lever_url]).read_text(encoding="utf-8"))
    boards.overrides[lever_url] = postings[1:]  # first posting taken down

    code, out = run(CONFIG, tmp_path)

    assert code == EXIT_OK
    assert summary_row(out, "Lever Demo")[3:] == ["4", "0", "0", "1", "ok"]


def test_config_error_stops_before_any_request(boards: FakeBoards, tmp_path: Path) -> None:
    code, out = run('[[company]]\nname = "X"\nsource = "workday"\nboard = "x"\n', tmp_path)

    assert code == EXIT_UNUSABLE
    assert out.startswith("Config error:")
    assert boards.requests == []


def test_missing_config_explains_the_fix(boards: FakeBoards, tmp_path: Path) -> None:
    out = io.StringIO()
    code = run_fetch(tmp_path / "missing.toml", tmp_path / "db.sqlite", out)
    assert code == EXIT_UNUSABLE
    assert "copy config/companies.example.toml" in out.getvalue()


def test_unusable_database_is_reported(boards: FakeBoards, tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "data" / "jobwatcher.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE notes (body TEXT)")
    conn.commit()
    conn.close()

    config = tmp_path / "companies.toml"
    config.write_text(CONFIG, encoding="utf-8")
    out = io.StringIO()
    assert run_fetch(config, db, out) == EXIT_UNUSABLE
    assert out.getvalue().startswith("Database error:")
    assert boards.requests == []


def test_main_parses_fetch_arguments(
    boards: FakeBoards, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "companies.toml"
    config.write_text(CONFIG, encoding="utf-8")
    code = main(["fetch", "--config", str(config), "--db", str(tmp_path / "j.db")])
    assert code == EXIT_OK
    assert "3 companies: 3 ok" in capsys.readouterr().out
