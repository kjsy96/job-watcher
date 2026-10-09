"""Load and validate config/filters.toml into FilterRules.

Like the company config, every problem stops the run before anything is
fetched or filtered, and names the section and key at fault. Unknown keys
are errors, so a typo like "titel_include" can't silently disable a rule.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from jobwatcher.config import ConfigError
from jobwatcher.matching import Terms

# Two-letter codes that count as a state or province after a comma, as in
# "Springfield, VT". Kept here, not in config, because they are facts, not
# preferences; config only says which of them belong to which tier.
US_STATE_CODES = frozenset(
    [
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
        "PR",
    ]
)
CA_PROVINCE_CODES = frozenset(
    ["AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"]
)
REGION_CODES = US_STATE_CODES | CA_PROVINCE_CODES

_TIER_KEY = re.compile(r"tier(\d+)")

_SECTION_KEYS: dict[str, frozenset[str]] = {
    "roles": frozenset({"title_include", "title_exclude", "title_flag_terms"}),
    "domain": frozenset({"terms"}),
    "work": frozenset({"terms"}),
    "description": frozenset({"flag_terms"}),
    "sponsorship": frozenset(
        {"hard_no_terms", "eligibility_terms", "positive_terms", "restriction_terms"}
    ),
    "travel": frozenset({"max_percent"}),
}
_LOCATION_KEYS = frozenset({"non_us_remote_terms", "ambiguous_terms"})
_TIER_KEYS = frozenset(
    {
        "label",
        "requires_sponsorship",
        "remote_terms",
        "us_wide_terms",
        "country_terms",
        "state_codes",
        "province_codes",
        "place_terms",
        "pathway_title_terms",
        "pathway_note",
    }
)


@dataclass(frozen=True)
class Tier:
    """One location preference tier. Lower number = more preferred."""

    number: int
    label: str
    requires_sponsorship: bool
    remote_terms: Terms
    us_wide_terms: Terms
    country_terms: Terms
    region_codes: frozenset[str]  # state_codes and province_codes together
    place_terms: Terms
    pathway_title_terms: Terms
    pathway_note: str


@dataclass(frozen=True)
class Sponsorship:
    hard_no_terms: Terms
    eligibility_terms: Terms
    positive_terms: Terms
    # Always excluded, even for pathway titles: "citizens and permanent
    # residents only" is a restriction no work permit gets around.
    restriction_terms: Terms


@dataclass(frozen=True)
class FilterRules:
    title_include: Terms
    title_exclude: Terms
    # Titles that usually mean a role that isn't a fit, but not always
    # ("Firmware Support Engineer"). Flagged when a role term also hits.
    title_flag_terms: Terms
    domain_terms: Terms
    work_terms: Terms
    non_us_remote_terms: Terms
    ambiguous_terms: Terms
    tiers: tuple[Tier, ...]
    description_flag_terms: Terms
    sponsorship: Sponsorship  # used from issue 2.3
    travel_max_percent: int | None  # used from issue 2.3


def load_filter_rules(path: Path) -> FilterRules:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(
            f"{path} not found: copy config/filters.example.toml to config/filters.toml and edit it"
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    known = set(_SECTION_KEYS) | {"location"}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{path}: unknown section(s) {sorted(unknown)}")

    roles = _section(data, "roles", path, required=True)
    domain = _section(data, "domain", path, required=True)
    work = _section(data, "work", path, required=True)
    description = _section(data, "description", path)
    sponsorship = _section(data, "sponsorship", path)
    travel = _section(data, "travel", path)

    title_include = _terms(roles, "title_include", f"{path} [roles]", required=True)

    location = data.get("location")
    if not isinstance(location, dict):
        raise ConfigError(f"{path}: [location] with at least one [location.tierN] is required")
    tiers = _tiers(location, f"{path} [location]")

    return FilterRules(
        title_include=title_include,
        title_exclude=_terms(roles, "title_exclude", f"{path} [roles]"),
        title_flag_terms=_terms(roles, "title_flag_terms", f"{path} [roles]"),
        domain_terms=_terms(domain, "terms", f"{path} [domain]"),
        work_terms=_terms(work, "terms", f"{path} [work]"),
        non_us_remote_terms=_terms(location, "non_us_remote_terms", f"{path} [location]"),
        ambiguous_terms=_terms(location, "ambiguous_terms", f"{path} [location]"),
        tiers=tiers,
        description_flag_terms=_terms(description, "flag_terms", f"{path} [description]"),
        sponsorship=Sponsorship(
            hard_no_terms=_terms(sponsorship, "hard_no_terms", f"{path} [sponsorship]"),
            eligibility_terms=_terms(sponsorship, "eligibility_terms", f"{path} [sponsorship]"),
            positive_terms=_terms(sponsorship, "positive_terms", f"{path} [sponsorship]"),
            restriction_terms=_terms(sponsorship, "restriction_terms", f"{path} [sponsorship]"),
        ),
        travel_max_percent=_max_percent(travel, f"{path} [travel]"),
    )


def _section(
    data: dict[str, object], name: str, path: Path, *, required: bool = False
) -> dict[str, object]:
    section = data.get(name)
    if section is None:
        if required:
            raise ConfigError(f"{path}: [{name}] section is required")
        return {}
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: [{name}] must be a table")
    unknown = set(section) - _SECTION_KEYS[name]
    if unknown:
        raise ConfigError(f"{path} [{name}]: unknown key(s) {sorted(unknown)}")
    return section


def _terms(section: dict[str, object], key: str, where: str, *, required: bool = False) -> Terms:
    value = section.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: '{key}' must be a list of text terms")
    # Whole-word matching makes padding like "vp " unnecessary, so surrounding
    # spaces are trimmed rather than treated as part of the term.
    terms = [v.strip() for v in value if isinstance(v, str)]
    if any(not t for t in terms):
        raise ConfigError(f"{where}: '{key}' contains an empty term")
    if required and not terms:
        raise ConfigError(f"{where}: '{key}' must list at least one term")
    return Terms(terms)


def _codes(section: dict[str, object], key: str, where: str, valid: frozenset[str]) -> set[str]:
    value = section.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: '{key}' must be a list of two-letter codes")
    codes = {v.strip().upper() for v in value if isinstance(v, str)}
    unknown = codes - valid
    if unknown:
        raise ConfigError(f"{where}: '{key}' has unknown code(s) {sorted(unknown)}")
    return codes


def _tiers(location: dict[str, object], where: str) -> tuple[Tier, ...]:
    tiers: list[Tier] = []
    for key, value in location.items():
        if key in _LOCATION_KEYS:
            continue
        found = _TIER_KEY.fullmatch(key)
        if not found:
            raise ConfigError(f"{where}: unknown key '{key}' (tiers are named tier1, tier2, ...)")
        if not isinstance(value, dict):
            raise ConfigError(f"{where}.{key} must be a table")
        tier_where = f"{where}.{key}"
        unknown = set(value) - _TIER_KEYS
        if unknown:
            raise ConfigError(f"{tier_where}: unknown key(s) {sorted(unknown)}")

        label = value.get("label", key)
        requires = value.get("requires_sponsorship", False)
        note = value.get("pathway_note", "")
        if not isinstance(label, str) or not isinstance(note, str):
            raise ConfigError(f"{tier_where}: 'label' and 'pathway_note' must be text")
        if not isinstance(requires, bool):
            raise ConfigError(f"{tier_where}: 'requires_sponsorship' must be true or false")

        codes = _codes(value, "state_codes", tier_where, US_STATE_CODES) | _codes(
            value, "province_codes", tier_where, CA_PROVINCE_CODES
        )
        tiers.append(
            Tier(
                number=int(found.group(1)),
                label=label,
                requires_sponsorship=requires,
                remote_terms=_terms(value, "remote_terms", tier_where),
                us_wide_terms=_terms(value, "us_wide_terms", tier_where),
                country_terms=_terms(value, "country_terms", tier_where),
                region_codes=frozenset(codes),
                place_terms=_terms(value, "place_terms", tier_where),
                pathway_title_terms=_terms(value, "pathway_title_terms", tier_where),
                pathway_note=note,
            )
        )

    if not tiers:
        raise ConfigError(f"{where}: at least one [location.tierN] table is required")
    numbers = [t.number for t in tiers]
    if len(set(numbers)) != len(numbers):
        raise ConfigError(f"{where}: tier numbers must be unique")
    return tuple(sorted(tiers, key=lambda t: t.number))


def _max_percent(travel: dict[str, object], where: str) -> int | None:
    value = travel.get("max_percent")
    if value is None:
        return None
    # bool is an int in Python; "max_percent = true" is a mistake, not 1%.
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
        raise ConfigError(f"{where}: 'max_percent' must be a whole number from 0 to 100")
    return value
