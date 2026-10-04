"""Discover employers the owner hasn't heard of, from Adzuna search results.

Adzuna is used only to learn that an unfamiliar employer is hiring for the
owner's kinds of roles. Its descriptions are 500-character snippets, so it
is never used to judge postings: an approved employer's full postings come
from its own job board and go through the normal filters.

Credentials: Adzuna takes app_id and app_key as URL query parameters, so a
request URL contains the key. This module never prints, logs, or stores a
URL, and never lets an httpx error message (which includes the URL) escape.
"""

import re
import time
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from jobwatcher.config import ConfigError
from jobwatcher.matching import Terms
from jobwatcher.sources.payload import ShapeError, require_dict, require_list, require_str

API_BASE = "https://api.adzuna.com/v1/api/jobs"
# Confirmed in issue 2b.1 (docs/sources.md): these answer; se, no, ie, dk,
# fi, pt return 404.
SUPPORTED_COUNTRIES = frozenset(["us", "ca", "at", "be", "de", "es", "fr", "it", "nl", "pl", "ch"])
MAX_RESULTS_PER_PAGE = 50  # larger values are silently capped by Adzuna
SECONDS_BETWEEN_CALLS = 2.6  # Adzuna allows 25 calls per minute
DEFAULT_MAX_CALLS = 200  # Adzuna allows 250 calls per day

# Legal-form words dropped when comparing employer names, so that
# "Acme Inc." and "ACME" are recognized as the same employer.
_LEGAL_SUFFIXES = frozenset(
    [
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "co",
        "company",
        "plc",
        "gmbh",
        "ag",
        "sa",
        "sas",
        "bv",
        "nv",
        "lp",
        "llp",
        "pty",
        "srl",
        "spa",
    ]
)
_NON_WORD = re.compile(r"[^a-z0-9]+")


# --- configuration -----------------------------------------------------------


@dataclass(frozen=True)
class SearchBlock:
    countries: tuple[str, ...]
    role_terms: tuple[str, ...]

    @property
    def calls(self) -> int:
        return len(self.countries) * len(self.role_terms)


@dataclass(frozen=True)
class DiscoveryConfig:
    searches: tuple[SearchBlock, ...]
    industry_terms: Terms
    max_days_old: int
    max_calls: int

    @property
    def calls(self) -> int:
        return sum(block.calls for block in self.searches)


def load_discovery_config(path: Path) -> DiscoveryConfig:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(
            f"{path} not found: copy config/discovery.example.toml to config/discovery.toml "
            "and edit it"
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    unknown = set(data) - {"search", "industry_terms", "max_days_old", "max_calls"}
    if unknown:
        raise ConfigError(f"{path}: unknown key(s) {sorted(unknown)}")

    raw_blocks = data.get("search")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise ConfigError(f"{path}: at least one [[search]] block is required")
    blocks = tuple(
        _search_block(raw, f"{path} [[search]] #{n}") for n, raw in enumerate(raw_blocks, 1)
    )

    industry = data.get("industry_terms", [])
    if not isinstance(industry, list) or not all(
        isinstance(t, str) and t.strip() for t in industry
    ):
        raise ConfigError(f"{path}: 'industry_terms' must be a list of non-empty text terms")

    max_days_old = _whole_number(data.get("max_days_old", 7), "max_days_old", path, 1, 365)
    max_calls = _whole_number(data.get("max_calls", DEFAULT_MAX_CALLS), "max_calls", path, 1, 250)

    config = DiscoveryConfig(blocks, Terms(t.strip() for t in industry), max_days_old, max_calls)
    if config.calls > config.max_calls:
        detail = " + ".join(
            f"{len(b.countries)} countries x {len(b.role_terms)} terms" for b in blocks
        )
        raise ConfigError(
            f"{path}: this search needs {config.calls} calls ({detail}) but max_calls is "
            f"{config.max_calls}; Adzuna allows 250 per day. Shorten role_terms or countries."
        )
    return config


def _search_block(raw: object, where: str) -> SearchBlock:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a table")
    unknown = set(raw) - {"countries", "role_terms"}
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
    countries = raw.get("countries")
    if (
        not isinstance(countries, list)
        or not countries
        or not all(isinstance(c, str) for c in countries)
    ):
        raise ConfigError(f"{where}: 'countries' must be a non-empty list of country codes")
    codes = tuple(c.strip().lower() for c in countries if isinstance(c, str))
    unsupported = sorted(set(codes) - SUPPORTED_COUNTRIES)
    if unsupported:
        raise ConfigError(
            f"{where}: Adzuna doesn't cover {unsupported}; supported: {sorted(SUPPORTED_COUNTRIES)}"
        )
    terms = raw.get("role_terms")
    if (
        not isinstance(terms, list)
        or not terms
        or not all(isinstance(t, str) and t.strip() for t in terms)
    ):
        raise ConfigError(f"{where}: 'role_terms' must be a non-empty list of text terms")
    return SearchBlock(codes, tuple(t.strip() for t in terms if isinstance(t, str)))


def _whole_number(value: object, key: str, path: Path, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ConfigError(f"{path}: '{key}' must be a whole number from {low} to {high}")
    return value


# --- searching ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdzunaJob:
    employer: str
    title: str
    location: str
    country: str
    snippet: str
    category: str
    created: str


class AdzunaError(Exception):
    """A search call failed. The message never contains the URL or the key."""


class AdzunaClient:
    """Searches one country for one role term per call, politely spaced."""

    def __init__(
        self,
        client: httpx.Client,
        app_id: str,
        app_key: str,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._auth = {"app_id": app_id, "app_key": app_key}
        self._pause = pause
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    def search(self, country: str, role_term: str, max_days_old: int) -> list[AdzunaJob]:
        if self._calls:
            self._pause(SECONDS_BETWEEN_CALLS)
        self._calls += 1
        params: dict[str, str | int] = {
            **self._auth,
            "title_only": role_term,
            "max_days_old": max_days_old,
            "results_per_page": MAX_RESULTS_PER_PAGE,
            "content-type": "application/json",
        }
        try:
            response = self._client.get(f"{API_BASE}/{country}/search/1", params=params)
        except httpx.HTTPError as exc:
            # str(exc) can include the request URL, which contains the key.
            raise AdzunaError(f"request failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise AdzunaError(f"HTTP {response.status_code}")
        try:
            payload: object = response.json()
        except ValueError:
            raise AdzunaError("response was not valid JSON") from None
        try:
            return parse_results(payload, country)
        except ShapeError as exc:
            raise AdzunaError(f"unexpected response shape: {exc}") from None


def parse_results(payload: object, country: str) -> list[AdzunaJob]:
    body = require_dict(payload, "response")
    jobs: list[AdzunaJob] = []
    for i, raw in enumerate(require_list(body.get("results"), "response.results")):
        where = f"results[{i}]"
        job = require_dict(raw, where)
        company = job.get("company")
        employer = ""
        if isinstance(company, dict) and isinstance(company.get("display_name"), str):
            employer = str(company["display_name"]).strip()
        if not employer:
            continue  # an ad without an employer can't lead to a company
        location = job.get("location")
        place = ""
        if isinstance(location, dict) and isinstance(location.get("display_name"), str):
            place = str(location["display_name"])
        category = job.get("category")
        label = ""
        if isinstance(category, dict) and isinstance(category.get("label"), str):
            label = str(category["label"])
        created = job.get("created")
        jobs.append(
            AdzunaJob(
                employer=employer,
                title=" ".join(require_str(job, "title", where).split()),
                location=place,
                country=country,
                snippet=str(job.get("description") or ""),
                category=label,
                created=created if isinstance(created, str) else "",
            )
        )
    return jobs


# --- grouping and ranking ------------------------------------------------------


def employer_key(name: str) -> str:
    """Normalized name for comparing employers: 'Acme Inc.' == 'ACME'."""
    words = _NON_WORD.sub(" ", name.lower()).split()
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


@dataclass
class Candidate:
    name: str
    key: str
    jobs: list[AdzunaJob] = field(default_factory=list)
    industry_hits: list[str] = field(default_factory=list)

    @property
    def countries(self) -> list[str]:
        return sorted({j.country for j in self.jobs})

    @property
    def titles(self) -> list[str]:
        return list(dict.fromkeys(j.title for j in self.jobs))


@dataclass
class DiscoveryResult:
    candidates: list[Candidate]
    skipped_known: int  # employers found but already tracked or rejected
    calls: int
    errors: list[str]  # "us / 'field engineer': HTTP 429"


def group_candidates(
    jobs: Iterable[AdzunaJob], known: set[str], industry_terms: Terms
) -> tuple[list[Candidate], int]:
    """Group jobs by employer, drop known employers, and rank the rest.

    Ranking: most distinct industry terms found first, then most matching
    jobs, then name. Employers with no industry terms are kept (a 500-
    character snippet can't prove an industry is absent), just ranked lower.
    """
    groups: dict[str, list[AdzunaJob]] = {}
    for job in jobs:
        groups.setdefault(employer_key(job.employer), []).append(job)

    candidates: list[Candidate] = []
    skipped = 0
    for key, group in groups.items():
        if key in known:
            skipped += 1
            continue
        # The most common spelling of the name stands for the employer.
        name = Counter(j.employer for j in group).most_common(1)[0][0]
        texts = [t for j in group for t in (j.title, j.snippet, j.category)]
        hits = industry_terms.hits(*texts)
        unique_jobs = list({(j.title, j.location, j.country): j for j in group}.values())
        candidates.append(Candidate(name, key, unique_jobs, hits))

    candidates.sort(key=lambda c: (-len(c.industry_hits), -len(c.jobs), c.name.lower()))
    return candidates, skipped


def discover(
    config: DiscoveryConfig, client: AdzunaClient, known_names: Iterable[str]
) -> DiscoveryResult:
    """Run every search in the config and return ranked new employers."""
    known = {employer_key(n) for n in known_names}
    jobs: list[AdzunaJob] = []
    errors: list[str] = []
    for block in config.searches:
        for role_term in block.role_terms:
            for country in block.countries:
                try:
                    jobs.extend(client.search(country, role_term, config.max_days_old))
                except AdzunaError as exc:
                    errors.append(f"{country} / '{role_term}': {exc}")
    candidates, skipped = group_candidates(jobs, known, config.industry_terms)
    return DiscoveryResult(candidates, skipped, client.calls, errors)


def known_employer_names(companies_path: Path, rejected_path: Path) -> list[str]:
    """Names from companies.toml and (if it exists) rejected_companies.toml."""
    names: list[str] = []
    for path in (companies_path, rejected_path):
        if not path.exists():
            continue
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
        for entry in data.get("company", []):
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                names.append(str(entry["name"]))
    return names
