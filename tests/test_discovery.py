"""Discovery (issue 2b.2). No network: Adzuna is answered by a MockTransport.

The fixture is a real Adzuna response (US, "commissioning engineer",
2026-10-04) with the owner's app ID scrubbed from every redirect_url.
"""

import io
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from jobwatcher import fetch
from jobwatcher.__main__ import EXIT_COMPANY_FAILED, EXIT_OK, EXIT_UNUSABLE, run_discover
from jobwatcher.config import ConfigError
from jobwatcher.discovery import (
    SECONDS_BETWEEN_CALLS,
    AdzunaClient,
    AdzunaError,
    AdzunaJob,
    Candidate,
    DiscoveryConfig,
    Grouped,
    SearchBlock,
    discover,
    employer_key,
    group_candidates,
    known_employer_names,
    load_discovery_config,
    parse_results,
)
from jobwatcher.env import read_env_file, require
from jobwatcher.matching import Terms
from jobwatcher.sources.payload import ShapeError

FIXTURE = Path(__file__).parent / "fixtures" / "adzuna_search.json"
REPO_ROOT = Path(__file__).parent.parent
FAKE_ID = "fakeid42"
FAKE_KEY = "fakekey0123456789abcdef0123456789"  # never a real credential


def payload() -> dict[str, object]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def job(employer: str, title: str = "Field Engineer", **kw: str) -> AdzunaJob:
    fields = {"location": "Denver", "country": "us", "snippet": "", "category": "", "created": ""}
    fields.update(kw)
    return AdzunaJob(employer=employer, title=title, **fields)


def unpack(grouped: Grouped) -> tuple[list[Candidate], int]:
    return grouped.candidates, grouped.skipped_known


# --- the fixture is safe to commit ---


def test_fixture_contains_no_app_id() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    assert "utm_source=APP_ID_REDACTED" in text
    assert "app_key" not in text


# --- config ---


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "discovery.toml"
    path.write_text(text, encoding="utf-8")
    return path


GOOD = """
industry_terms = ["engineering"]
[[search]]
countries = ["us", "ca"]
role_terms = ["field engineer", "commissioning"]
"""


def test_committed_example_is_valid() -> None:
    config = load_discovery_config(REPO_ROOT / "config" / "discovery.example.toml")
    assert config.calls <= config.max_calls


def test_calls_are_countries_times_terms(tmp_path: Path) -> None:
    config = load_discovery_config(write(tmp_path, GOOD))
    assert config.calls == 4
    assert config.max_days_old == 7 and config.max_calls == 200


def test_over_budget_config_is_refused_with_the_arithmetic(tmp_path: Path) -> None:
    text = GOOD.replace('["field engineer", "commissioning"]', str([f"t{i}" for i in range(6)]))
    text = text.replace("[[search]]", "max_calls = 10\n[[search]]")
    with pytest.raises(
        ConfigError, match=r"needs 12 calls \(2 countries x 6 terms\) but max_calls is 10"
    ):
        load_discovery_config(write(tmp_path, text))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (('["us", "ca"]', '["us", "se"]'), r"doesn't cover \['se'\]"),
        (('["us", "ca"]', "[]"), "non-empty list of country codes"),
        (('["field engineer", "commissioning"]', "[]"), "non-empty list of text terms"),
        (('["engineering"]', '["engineering", ""]'), "non-empty text terms"),
        (("industry_terms", "industy_terms"), r"unknown key\(s\) \['industy_terms'\]"),
        (('role_terms = ["field', 'roles = ["field'), r"unknown key\(s\) \['roles'\]"),
    ],
)
def test_invalid_config_names_the_problem(
    tmp_path: Path, change: tuple[str, str], message: str
) -> None:
    old, new = change
    assert old in GOOD
    with pytest.raises(ConfigError, match=message):
        load_discovery_config(write(tmp_path, GOOD.replace(old, new)))


def test_require_industry_term_defaults_on_and_can_be_turned_off(tmp_path: Path) -> None:
    assert load_discovery_config(write(tmp_path, GOOD)).require_industry_term is True
    off = "require_industry_term = false\n" + GOOD
    assert load_discovery_config(write(tmp_path, off)).require_industry_term is False


def test_requiring_a_term_from_an_empty_list_is_refused(tmp_path: Path) -> None:
    text = GOOD.replace('industry_terms = ["engineering"]', "industry_terms = []")
    with pytest.raises(ConfigError, match="every employer would be dropped"):
        load_discovery_config(write(tmp_path, text))
    # Fine when the requirement is off.
    load_discovery_config(write(tmp_path, "require_industry_term = false\n" + text))


def test_require_industry_term_must_be_true_or_false(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="true or false"):
        load_discovery_config(write(tmp_path, 'require_industry_term = "yes"\n' + GOOD))


@pytest.mark.parametrize("line", ["max_days_old = 0", "max_days_old = 400", "max_calls = 251"])
def test_numbers_must_be_in_range(tmp_path: Path, line: str) -> None:
    with pytest.raises(ConfigError, match="whole number"):
        load_discovery_config(write(tmp_path, line + "\n" + GOOD))


def test_missing_config_explains_the_fix(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"copy config/discovery\.example\.toml"):
        load_discovery_config(tmp_path / "discovery.toml")


def test_no_search_blocks_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"at least one \[\[search\]\]"):
        load_discovery_config(write(tmp_path, 'industry_terms = ["x"]\n'))


# --- parsing the real response ---


def test_parses_the_real_response() -> None:
    jobs = parse_results(payload(), "us")
    assert len(jobs) == 5
    assert jobs[0].employer == "Amazon Data Services, Inc."
    assert jobs[0].title == "Commissioning Engineer, AMER-Central ACx"
    assert jobs[0].category == "Engineering Jobs"
    assert jobs[0].country == "us"
    assert len(jobs[0].snippet) == 500


def test_ads_without_an_employer_are_skipped() -> None:
    data = payload()
    results = data["results"]
    assert isinstance(results, list)
    results[0]["company"] = {}
    results[1].pop("company")
    assert len(parse_results(data, "us")) == 3


@pytest.mark.parametrize(
    "bad", [[], {"count": 0}, {"results": None}, {"results": [{"company": {"display_name": "X"}}]}]
)
def test_wrong_shape_raises(bad: object) -> None:
    with pytest.raises(ShapeError):
        parse_results(bad, "us")


# --- employer names ---


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Amazon Data Services, Inc.", "amazon data services"),
        ("ACME Corp.", "Acme"),
        ("Acme, LLC", "acme"),
        ("Siemens AG", "SIEMENS"),
        ("Bosch GmbH", "bosch"),
        ("Rio Tinto plc", "Rio Tinto"),
    ],
)
def test_employer_names_normalize_to_the_same_key(a: str, b: str) -> None:
    assert employer_key(a) == employer_key(b)


def test_a_name_made_only_of_a_suffix_word_is_kept() -> None:
    assert employer_key("Company") == "company"


def test_different_employers_stay_different() -> None:
    assert employer_key("Acme Mining") != employer_key("Acme Robotics")


# --- grouping and ranking ---


def test_groups_the_real_response_by_employer() -> None:
    candidates, skipped = unpack(group_candidates(parse_results(payload(), "us"), set(), Terms([])))
    assert skipped == 0
    by_name = {c.name: c for c in candidates}
    assert set(by_name) == {"Amazon Data Services, Inc.", "Syska Hennessy Group"}
    assert len(by_name["Amazon Data Services, Inc."].jobs) == 3
    # Same title in two places counts as two ads.
    assert len(by_name["Syska Hennessy Group"].jobs) == 2
    assert by_name["Syska Hennessy Group"].titles == [
        "Commissioning Engineer III - Senior Commissioning Engineer"
    ]


def test_known_employers_are_skipped_whatever_the_spelling() -> None:
    known = {employer_key("Amazon Data Services")}  # no ", Inc." in the config
    candidates, skipped = unpack(group_candidates(parse_results(payload(), "us"), known, Terms([])))
    assert [c.name for c in candidates] == ["Syska Hennessy Group"]
    assert skipped == 1


def test_ranked_by_industry_terms_then_ad_count() -> None:
    jobs = [
        job("Busy Co", "Field Engineer A"),
        job("Busy Co", "Field Engineer B"),
        job("Busy Co", "Field Engineer C"),
        job("Mine Tech", snippet="Commissioning at open-pit mining sites."),
        job("Plant Ops", category="Manufacturing Jobs"),
    ]
    candidates, _ = unpack(group_candidates(jobs, set(), Terms(["mining", "manufacturing"])))
    assert [(c.name, c.industry_hits) for c in candidates] == [
        ("Mine Tech", ["mining"]),
        ("Plant Ops", ["manufacturing"]),
        ("Busy Co", []),  # kept: a snippet can't prove the industry is absent
    ]


def test_duplicate_ads_from_several_searches_count_once() -> None:
    same = job("Acme", "Field Engineer")
    candidates, _ = unpack(group_candidates([same, same, same], set(), Terms([])))
    assert len(candidates[0].jobs) == 1


def test_countries_are_listed() -> None:
    candidates, _ = unpack(
        group_candidates([job("Acme", country="us"), job("Acme", country="de")], set(), Terms([]))
    )
    assert candidates[0].countries == ["de", "us"]


def test_required_industry_term_drops_and_counts_the_rest() -> None:
    jobs = [
        job("Mine Tech", snippet="Commissioning at open-pit mining sites."),
        job("Payroll Co", "Implementation Consultant", snippet="Payroll software rollouts."),
        job("Bank Co", "Implementation Lead", category="Accounting & Finance Jobs"),
    ]
    grouped = group_candidates(jobs, set(), Terms(["mining"]), require_industry_term=True)
    assert [c.name for c in grouped.candidates] == ["Mine Tech"]
    assert grouped.dropped_no_industry == 2

    kept = group_candidates(jobs, set(), Terms(["mining"]), require_industry_term=False)
    assert [c.name for c in kept.candidates] == ["Mine Tech", "Bank Co", "Payroll Co"]
    assert kept.dropped_no_industry == 0


def test_industry_term_in_any_ad_keeps_the_employer() -> None:
    # One relevant ad is enough, even if the employer's other ads aren't.
    jobs = [
        job("Acme", "Implementation Lead", snippet="Payroll rollouts."),
        job("Acme", "Field Engineer", snippet="Commissioning at mining sites."),
    ]
    grouped = group_candidates(jobs, set(), Terms(["mining"]), require_industry_term=True)
    assert [c.name for c in grouped.candidates] == ["Acme"]


def test_known_employers_are_counted_as_known_not_dropped() -> None:
    grouped = group_candidates(
        [job("Acme", snippet="no industry here")],
        {employer_key("Acme")},
        Terms(["mining"]),
        require_industry_term=True,
    )
    assert (grouped.skipped_known, grouped.dropped_no_industry) == (1, 0)


def test_ignored_employers_are_skipped_and_counted_separately() -> None:
    jobs = [
        job("Umanist Staffing LLC", snippet="Field engineer for a mining client."),
        job("Robert Half", snippet="Mining commissioning contract."),
        job("Mine Tech", snippet="Commissioning at mining sites."),
        job("Acme", snippet="Mining site work."),
    ]
    grouped = group_candidates(
        jobs,
        {employer_key("Acme")},
        Terms(["mining"]),
        require_industry_term=True,
        # Configured without the legal suffix and in a different case.
        ignored=frozenset({employer_key("umanist staffing"), employer_key("ROBERT HALF")}),
    )
    assert [c.name for c in grouped.candidates] == ["Mine Tech"]
    assert (grouped.ignored, grouped.skipped_known, grouped.dropped_no_industry) == (2, 1, 0)


def test_ignore_list_is_loaded_as_normalized_names(tmp_path: Path) -> None:
    text = 'ignore_employers = ["Robert Half Inc.", "Job-Room"]\n' + GOOD
    config = load_discovery_config(write(tmp_path, text))
    assert config.ignore_employers == {employer_key("robert half"), employer_key("JOB ROOM")}
    assert load_discovery_config(write(tmp_path, GOOD)).ignore_employers == frozenset()


@pytest.mark.parametrize("value", ['"Robert Half"', '["Robert Half", ""]', "[3]"])
def test_ignore_list_must_be_a_list_of_names(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="list of non-empty names"):
        load_discovery_config(write(tmp_path, f"ignore_employers = {value}\n" + GOOD))


# --- the Adzuna client ---


Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, pauses: list[float] | None = None) -> AdzunaClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return AdzunaClient(
        http, FAKE_ID, FAKE_KEY, pause=(pauses.append if pauses is not None else lambda _: None)
    )


def test_search_sends_the_confirmed_parameters() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload())

    jobs = make_client(handler).search("ca", "field engineer", 7)

    assert len(jobs) == 5 and jobs[0].country == "ca"
    url = seen[0].url
    assert url.path == "/v1/api/jobs/ca/search/1"
    assert url.params["title_only"] == "field engineer"
    assert url.params["max_days_old"] == "7"
    assert url.params["results_per_page"] == "50"
    assert url.params["app_id"] == FAKE_ID and url.params["app_key"] == FAKE_KEY


def test_calls_are_spaced_but_the_first_isnt_delayed() -> None:
    pauses: list[float] = []
    client = make_client(lambda _: httpx.Response(200, json={"results": []}), pauses)
    for _ in range(3):
        client.search("us", "x", 7)
    assert pauses == [SECONDS_BETWEEN_CALLS, SECONDS_BETWEEN_CALLS]
    assert client.calls == 3


def raise_connect(request: httpx.Request) -> httpx.Response:
    # httpx's own message would include the full URL (and so the key).
    raise httpx.ConnectError(f"failed to reach {request.url}", request=request)


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (lambda _: httpx.Response(429), "HTTP 429"),
        (lambda _: httpx.Response(401, text=f"bad key {FAKE_KEY}"), "HTTP 401"),
        (lambda _: httpx.Response(200, text="<html>down</html>"), "not valid JSON"),
        (lambda _: httpx.Response(200, json={"oops": 1}), "unexpected response shape"),
        (raise_connect, "request failed: ConnectError"),
    ],
)
def test_errors_never_contain_the_key_or_url(handler: Handler, message: str) -> None:
    with pytest.raises(AdzunaError) as exc:
        make_client(handler).search("us", "field engineer", 7)
    text = str(exc.value)
    assert message in text
    assert FAKE_KEY not in text and FAKE_ID not in text and "api.adzuna.com" not in text
    assert exc.value.__cause__ is None  # the httpx error (with its URL) isn't chained


def test_discover_collects_failures_and_keeps_going() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/ca/" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, json=payload())

    config = DiscoveryConfig(
        (SearchBlock(("us", "ca"), ("a", "b")),), Terms([]), 7, 200, require_industry_term=False
    )
    result = discover(config, make_client(handler), known_names=["Syska Hennessy Group"])

    assert result.calls == 4
    assert result.errors == ["ca / 'a': HTTP 503", "ca / 'b': HTTP 503"]
    assert [c.name for c in result.candidates] == ["Amazon Data Services, Inc."]
    assert result.skipped_known == 1


# --- known employers from config files ---


def test_known_names_come_from_companies_and_rejected(tmp_path: Path) -> None:
    companies = tmp_path / "companies.toml"
    companies.write_text('[[company]]\nname = "Acme"\nsource = "lever"\nboard = "acme"\n')
    rejected = tmp_path / "rejected.toml"
    rejected.write_text('[[company]]\nname = "Nope Inc"\nreason = "not a fit"\n')
    assert known_employer_names(companies, rejected) == ["Acme", "Nope Inc"]
    assert known_employer_names(companies, tmp_path / "missing.toml") == ["Acme"]


# --- .env reading ---


def test_env_file_parsing(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# comment\nA=1\n  B = two words \nnot a line\nC=\n", encoding="utf-8")
    assert read_env_file(path) == {"A": "1", "B": "two words", "C": ""}
    assert read_env_file(tmp_path / "absent") == {}


def test_require_prefers_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / ".env"
    path.write_text("ADZUNA_APP_ID=from-file\nADZUNA_APP_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("ADZUNA_APP_ID", "from-env")
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    assert require(("ADZUNA_APP_ID", "ADZUNA_APP_KEY"), path) == {
        "ADZUNA_APP_ID": "from-env",
        "ADZUNA_APP_KEY": "file-key",
    }


def test_missing_values_are_named_but_never_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text(f"ADZUNA_APP_ID={FAKE_ID}\nADZUNA_APP_KEY=\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        require(("ADZUNA_APP_ID", "ADZUNA_APP_KEY"), path)
    assert "missing ADZUNA_APP_KEY" in str(exc.value)
    assert FAKE_ID not in str(exc.value)


# --- the discover command ---


class Setup:
    def __init__(self, tmp_path: Path, discovery_text: str = GOOD) -> None:
        self.discovery = write(tmp_path, discovery_text)
        self.companies = tmp_path / "companies.toml"
        self.companies.write_text(
            '[[company]]\nname = "Syska Hennessy Group"\nsource = "lever"\nboard = "syska"\n'
        )
        self.rejected = tmp_path / "rejected.toml"
        self.env = tmp_path / ".env"
        self.env.write_text(f"ADZUNA_APP_ID={FAKE_ID}\nADZUNA_APP_KEY={FAKE_KEY}\n")
        self.db = tmp_path / "data" / "jobwatcher.db"

    def run(self) -> tuple[int, str]:
        out = io.StringIO()
        code = run_discover(
            self.discovery,
            self.companies,
            self.rejected,
            self.env,
            out,
            pause=lambda _: None,
            db_path=self.db,
        )
        return code, out.getvalue()


class FakeAdzuna:
    """Answers Adzuna searches from the fixture (or a chosen error status).

    Job board lookups (Greenhouse, Lever, Ashby) are answered 404 unless a
    board is registered in self.boards, so detection runs without network.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []  # Adzuna searches only
        self.board_requests: list[httpx.Request] = []
        self.boards: dict[str, httpx.Response] = {}  # URL -> response
        self.status = 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.adzuna.com":
            self.board_requests.append(request)
            return self.boards.get(str(request.url), httpx.Response(404))
        self.requests.append(request)
        if self.status != 200:
            # A hostile error body that echoes the full URL, key included.
            return httpx.Response(self.status, text=f"error for {request.url}")
        return httpx.Response(200, json=payload())


@pytest.fixture
def adzuna(monkeypatch: pytest.MonkeyPatch) -> FakeAdzuna:
    fake = FakeAdzuna()
    monkeypatch.setattr(
        fetch, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(fake.handle))
    )
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    return fake


def test_discover_lists_new_employers(adzuna: FakeAdzuna, tmp_path: Path) -> None:
    code, out = Setup(tmp_path).run()

    assert code == EXIT_OK
    assert len(adzuna.requests) == 4  # 2 countries x 2 role terms
    assert (
        "1 new employers; 1 already known (on the company list or rejected); "
        "0 on the ignore list; 0 dropped" in out
    )
    # The fake answers both countries with the same 3 ads; per country they count separately.
    assert "- Amazon Data Services, Inc. [ca/us] 6 ad(s); industry: engineering" in out
    assert "Source: The Adzuna API" in out


def test_failed_calls_exit_1_and_never_show_credentials(adzuna: FakeAdzuna, tmp_path: Path) -> None:
    adzuna.status = 401
    code, out = Setup(tmp_path).run()

    assert code == EXIT_COMPANY_FAILED
    assert "ERROR us / 'field engineer': HTTP 401" in out
    assert FAKE_KEY not in out and FAKE_ID not in out and "api.adzuna.com" not in out


def test_config_error_exits_2_before_any_request(adzuna: FakeAdzuna, tmp_path: Path) -> None:
    over = "max_calls = 3\n" + GOOD  # needs 4
    code, out = Setup(tmp_path, over).run()
    assert code == EXIT_UNUSABLE
    assert out.startswith("Config error:") and "needs 4 calls" in out
    assert adzuna.requests == []


def test_missing_credentials_exit_2_before_any_request(adzuna: FakeAdzuna, tmp_path: Path) -> None:
    setup = Setup(tmp_path)
    setup.env.write_text("ADZUNA_APP_ID=\nADZUNA_APP_KEY=\n")
    code, out = setup.run()
    assert code == EXIT_UNUSABLE
    assert "missing ADZUNA_APP_ID, ADZUNA_APP_KEY" in out
    assert adzuna.requests == []


def test_discover_shows_each_employers_board(adzuna: FakeAdzuna, tmp_path: Path) -> None:
    adzuna.boards["https://boards-api.greenhouse.io/v1/boards/amazondataservices/jobs"] = (
        httpx.Response(
            200, json={"jobs": [{"title": "Commissioning Engineer, AMER-West ACx"}], "meta": {}}
        )
    )
    setup = Setup(tmp_path)

    code, out = setup.run()

    assert code == EXIT_OK
    assert "Job boards: 1 looked up this run (1 requests), 0 remembered" in out
    assert (
        "    board: confirmed: greenhouse:amazondataservices "
        "(board has 'Commissioning Engineer, AMER-West ACx')" in out
    )

    # Second run: remembered, so no board requests at all.
    adzuna.board_requests.clear()
    _, out = setup.run()
    assert "Job boards: 0 looked up this run (0 requests), 1 remembered" in out
    assert adzuna.board_requests == []
