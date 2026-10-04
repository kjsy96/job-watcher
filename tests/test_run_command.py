"""End-to-end tests for `python -m jobwatcher run` (issue 2.5).

The real pipeline runs (config, fetch, store, filter, report file), but
the HTTP client answers from the saved fixtures, so no request leaves the
machine. Filter rules are the neutral test fixture.
"""

import io
import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pytest

from jobwatcher import fetch
from jobwatcher.__main__ import (
    EXIT_COMPANY_FAILED,
    EXIT_OK,
    EXIT_UNUSABLE,
    main,
    run_daily,
    run_fetch,
    run_refilter,
)
from jobwatcher.run import next_report_path
from jobwatcher.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
TEST_RULES = FIXTURES / "filters_test.toml"

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
sector = "software"

[[company]]
name = "Lever Demo"
source = "lever"
board = "leverdemo"

[[company]]
name = "Ashby"
source = "ashby"
board = "Ashby"
"""

BROKEN = """
[[company]]
name = "Down Co"
source = "greenhouse"
board = "no-such-board"
"""


class FakeBoards:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        name = ROUTES.get(str(request.url))
        if name is None:
            return httpx.Response(404)
        return httpx.Response(200, json=json.loads((FIXTURES / name).read_text(encoding="utf-8")))


@pytest.fixture
def boards(monkeypatch: pytest.MonkeyPatch) -> FakeBoards:
    fake = FakeBoards()
    real_make_client: Callable[[], httpx.Client] = fetch.make_client

    def make_mock_client() -> httpx.Client:
        real = real_make_client()
        return httpx.Client(transport=httpx.MockTransport(fake.handle), headers=real.headers)

    monkeypatch.setattr(fetch, "make_client", make_mock_client)
    return fake


class Paths:
    def __init__(self, tmp_path: Path, config: str = CONFIG) -> None:
        self.config = tmp_path / "companies.toml"
        self.config.write_text(config, encoding="utf-8")
        self.filters = TEST_RULES
        self.db = tmp_path / "data" / "jobwatcher.db"
        self.reports = tmp_path / "reports"

    def run(self) -> tuple[int, str]:
        out = io.StringIO()
        code = run_daily(self.config, self.filters, self.db, self.reports, out, io.StringIO())
        return code, out.getvalue()

    def report_files(self) -> list[str]:
        return sorted(p.name for p in self.reports.glob("*.md"))


def test_first_run_filters_stores_and_reports_everything_new(
    boards: FakeBoards, tmp_path: Path
) -> None:
    paths = Paths(tmp_path)
    code, out = paths.run()

    assert code == EXIT_OK
    assert re.search(r"New postings: \d+ match, \d+ flagged, \d+ possible, \d+ excluded\.", out)
    [name] = paths.report_files()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", name)
    assert f"Report: {paths.reports / name}" in out

    report = (paths.reports / name).read_text(encoding="utf-8")
    assert "- **Postings:** 13 fetched, 13 new, 0 reopened, 0 closed" in report
    assert "None. Every company's board was read and recorded." in report

    # Every new posting has a stored filter result with reasons.
    with Store.open(paths.db) as store:
        outcome, reasons = store.filter_result("greenhouse:gitlab:8638232002")
    assert outcome in {"match", "flagged", "possible", "excluded"}
    assert reasons


def test_report_outcome_counts_match_stored_results(boards: FakeBoards, tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    paths.run()
    report = (paths.reports / paths.report_files()[0]).read_text(encoding="utf-8")

    with Store.open(paths.db) as store:
        rows = store._conn.execute(
            "SELECT filter_result, count(*) FROM postings GROUP BY filter_result"
        ).fetchall()
    stored = {row[0]: row[1] for row in rows}
    expected = (
        f"- **New postings:** {stored.get('match', 0)} match, {stored.get('flagged', 0)} flagged, "
        f"{stored.get('possible', 0)} possible, {stored.get('excluded', 0)} excluded"
    )
    assert expected in report
    assert sum(stored.values()) == 13


def test_second_run_same_day_keeps_the_first_report(boards: FakeBoards, tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    paths.run()
    [first] = paths.report_files()
    first_text = (paths.reports / first).read_text(encoding="utf-8")

    code, out = paths.run()

    assert code == EXIT_OK
    assert "New postings: 0 match, 0 flagged, 0 possible, 0 excluded." in out
    assert set(paths.report_files()) == {first, first.replace(".md", "-2.md")}
    assert (paths.reports / first).read_text(encoding="utf-8") == first_text  # untouched
    second = (paths.reports / first.replace(".md", "-2.md")).read_text(encoding="utf-8")
    assert "13 fetched, 0 new" in second
    assert "No new matches." in second


def test_failed_company_is_reported_first_and_exits_1(boards: FakeBoards, tmp_path: Path) -> None:
    paths = Paths(tmp_path, CONFIG + BROKEN)
    code, _ = paths.run()

    assert code == EXIT_COMPANY_FAILED
    report = (paths.reports / paths.report_files()[0]).read_text(encoding="utf-8")
    assert "- **ERROR: Down Co** (`greenhouse:no-such-board`): HTTP 404" in report
    assert report.index("ERROR: Down Co") < report.index("## Matches")
    assert "13 new" in report  # the healthy companies still ran


@pytest.mark.parametrize("broken", ["companies", "filters"])
def test_config_error_exits_2_before_any_request(
    boards: FakeBoards, tmp_path: Path, broken: str
) -> None:
    paths = Paths(tmp_path)
    if broken == "companies":
        paths.config.write_text('[[company]]\nname = "X"\nsource = "workday"\nboard = "x"\n')
    else:
        paths.filters = tmp_path / "filters.toml"
        paths.filters.write_text("[roles]\ntitel_include = []\n", encoding="utf-8")

    code, out = paths.run()

    assert code == EXIT_UNUSABLE
    assert out.startswith("Config error:")
    assert boards.requests == []
    assert not paths.reports.exists()


def test_main_parses_run_arguments(
    boards: FakeBoards, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = Paths(tmp_path)
    code = main(
        [
            "run",
            "--config",
            str(paths.config),
            "--filters",
            str(paths.filters),
            "--db",
            str(paths.db),
            "--reports",
            str(paths.reports),
        ]
    )
    assert code == EXIT_OK
    assert "Report: " in capsys.readouterr().out
    assert len(paths.report_files()) == 1


def test_next_report_path_never_reuses_a_name(tmp_path: Path) -> None:
    day = date(2026, 10, 4)
    assert next_report_path(tmp_path, day).name == "2026-10-04.md"
    (tmp_path / "2026-10-04.md").touch()
    (tmp_path / "2026-10-04-2.md").touch()
    assert next_report_path(tmp_path, day).name == "2026-10-04-3.md"


# --- refilter (issue 48) ---


def refilter(paths: Paths) -> tuple[int, str]:
    out = io.StringIO()
    code = run_refilter(paths.config, paths.filters, paths.db, paths.reports, out)
    return code, out.getvalue()


def test_refilter_after_run_changes_nothing_and_sends_no_requests(
    boards: FakeBoards, tmp_path: Path
) -> None:
    paths = Paths(tmp_path)
    paths.run()
    requests_before = len(boards.requests)

    code, out = refilter(paths)

    assert code == EXIT_OK
    assert len(boards.requests) == requests_before  # no network at all
    assert "Re-filtered 13 open postings (no boards fetched)." in out
    assert "Changes: 0 changed outcome, 0 filtered for the first time." in out
    names = paths.report_files()
    assert any(re.fullmatch(r"refilter-\d{4}-\d{2}-\d{2}\.md", n) for n in names)


def test_refilter_filters_postings_recorded_before_filtering_existed(
    boards: FakeBoards, tmp_path: Path
) -> None:
    paths = Paths(tmp_path)
    run_fetch(paths.config, paths.db, io.StringIO())  # fetch only: no results stored

    code, out = refilter(paths)

    assert code == EXIT_OK
    assert "Changes: 0 changed outcome, 13 filtered for the first time." in out
    with Store.open(paths.db) as store:
        assert store.filter_result("greenhouse:gitlab:8638232002")[0] is not None


def test_refilter_lists_outcomes_that_changed(boards: FakeBoards, tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    paths.run()
    posting_id = "greenhouse:gitlab:8638232002"
    with Store.open(paths.db) as store:
        current, reasons = store.filter_result(posting_id)
        assert current == "excluded"  # under the neutral test rules
        # Pretend an earlier rule set had matched it, so this refilter differs.
        store.set_filter_results([(posting_id, "match", reasons)])

    _, out = refilter(paths)

    assert "Changes: 1 changed outcome, 0 filtered for the first time." in out
    name = next(n for n in paths.report_files() if n.startswith("refilter-"))
    changes = (paths.reports / name).read_text(encoding="utf-8").split("## Changes")[1]
    assert "AI Transformation Owner, CRO" in changes.split("## ")[0]
    assert "match -> **excluded**" in changes.split("## ")[0]
    with Store.open(paths.db) as store:
        assert store.filter_result(posting_id)[0] == "excluded"  # stored again


def test_refilter_skips_closed_postings(boards: FakeBoards, tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    paths.run()
    with Store.open(paths.db) as store:
        store._conn.execute(
            "UPDATE postings SET status = 'closed' WHERE id = 'greenhouse:gitlab:8638232002'"
        )
        store._conn.commit()

    _, out = refilter(paths)
    assert "Re-filtered 12 open postings" in out


def test_refilter_never_overwrites_its_report(boards: FakeBoards, tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    paths.run()
    refilter(paths)
    refilter(paths)
    refilters = [n for n in paths.report_files() if n.startswith("refilter-")]
    assert len(refilters) == 2
    assert any(n.endswith("-2.md") for n in refilters)


def test_refilter_config_error_exits_2(boards: FakeBoards, tmp_path: Path) -> None:
    paths = Paths(tmp_path)
    paths.filters = tmp_path / "missing.toml"
    code, out = refilter(paths)
    assert code == EXIT_UNUSABLE
    assert out.startswith("Config error:")
