"""Command-line entry point: ``python -m jobwatcher``.

Commands:
  fetch     fetch every company's board once and record new and closed postings
  run       fetch, filter the new postings, and write the day's report
  refilter  re-run the current rules over every open stored posting (no network)
  discover  search Adzuna for employers not already on the list (discovery only)
  approve   add a discovered employer to the company list
  reject    never propose a discovered employer again

Exit codes:
  0  every company was fetched and recorded (refilter: it finished)
  1  the run finished, but at least one company failed (see the summary;
     for run, the report is still written and lists the failure first)
  2  nothing ran: bad arguments, config, or database
  3  run only: offline (every company failed with no HTTP response); nothing
     was recorded and today is not counted, so the next attempt retries

run counts at most one run per local day. Later attempts the same day exit
0 without fetching, unless --force is given.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from jobwatcher import __version__, approvals, detection, discovery, env, fetch
from jobwatcher import run as daily
from jobwatcher.config import ConfigError, load_companies
from jobwatcher.discovery_report import render_discovery_report
from jobwatcher.fetch import CompanyResult, Outcome
from jobwatcher.filter_config import load_filter_rules
from jobwatcher.models import SourceName
from jobwatcher.store import Store, StoreError

EXIT_OK = 0
EXIT_COMPANY_FAILED = 1
EXIT_UNUSABLE = 2
EXIT_OFFLINE = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobwatcher",
        description="Daily shortlist of new job postings from target companies.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    fetch_cmd = commands.add_parser(
        "fetch", help="fetch every company's board once and record new and closed postings"
    )
    run_cmd = commands.add_parser(
        "run", help="fetch, filter the new postings, and write the day's report"
    )
    refilter_cmd = commands.add_parser(
        "refilter",
        help="re-run the current rules over every open stored posting and write a review "
        "report (no network)",
    )
    for cmd in (fetch_cmd, run_cmd, refilter_cmd):
        cmd.add_argument(
            "--config",
            type=Path,
            default=Path("config/companies.toml"),
            help="company list (default: config/companies.toml)",
        )
        cmd.add_argument(
            "--db",
            type=Path,
            default=Path("data/jobwatcher.db"),
            help="SQLite database, created if missing (default: data/jobwatcher.db)",
        )
    run_cmd.add_argument(
        "--force",
        action="store_true",
        help="run even if today already has a counted run",
    )
    for cmd in (run_cmd, refilter_cmd):
        cmd.add_argument(
            "--filters",
            type=Path,
            default=Path("config/filters.toml"),
            help="filter rules (default: config/filters.toml)",
        )
        cmd.add_argument(
            "--reports",
            type=Path,
            default=Path("reports"),
            help="folder for reports (default: reports)",
        )

    discover_cmd = commands.add_parser(
        "discover",
        help="search Adzuna for employers hiring for your kinds of roles that aren't on the "
        "company list yet",
    )
    discover_cmd.add_argument(
        "--discovery",
        type=Path,
        default=Path("config/discovery.toml"),
        help="discovery search terms (default: config/discovery.toml)",
    )
    discover_cmd.add_argument(
        "--config",
        type=Path,
        default=Path("config/companies.toml"),
        help="company list; these employers are skipped (default: config/companies.toml)",
    )
    discover_cmd.add_argument(
        "--rejected",
        type=Path,
        default=Path("config/rejected_companies.toml"),
        help="rejected employers, also skipped (default: config/rejected_companies.toml)",
    )
    discover_cmd.add_argument(
        "--env",
        type=Path,
        default=Path(".env"),
        help="file holding ADZUNA_APP_ID and ADZUNA_APP_KEY (default: .env)",
    )
    discover_cmd.add_argument(
        "--db",
        type=Path,
        default=Path("data/jobwatcher.db"),
        help="SQLite database, which remembers job board lookups (default: data/jobwatcher.db)",
    )
    discover_cmd.add_argument(
        "--reports",
        type=Path,
        default=Path("reports"),
        help="folder for the discovery report (default: reports)",
    )

    approve_cmd = commands.add_parser(
        "approve",
        help="add a discovered employer to the company list, using its found job board",
    )
    approve_cmd.add_argument("name", help="employer name, as shown in the discovery report")
    approve_cmd.add_argument(
        "--sector", default=None, help="optional sector label for reports (default: none)"
    )
    approve_cmd.add_argument(
        "--source",
        choices=[s.value for s in SourceName],
        default=None,
        help="the employer's platform, if you found its board yourself (use with --board)",
    )
    approve_cmd.add_argument(
        "--board", default=None, help="the board name from its job board URL (with --source)"
    )
    reject_cmd = commands.add_parser(
        "reject", help="never propose a discovered employer again, with a reason"
    )
    reject_cmd.add_argument("name", help="employer name, as shown in the discovery report")
    reject_cmd.add_argument("--reason", required=True, help="why it isn't a fit")
    for cmd in (approve_cmd, reject_cmd):
        cmd.add_argument(
            "--config",
            type=Path,
            default=Path("config/companies.toml"),
            help="company list (default: config/companies.toml)",
        )
    approve_cmd.add_argument(
        "--db",
        type=Path,
        default=Path("data/jobwatcher.db"),
        help="SQLite database holding the job board lookups (default: data/jobwatcher.db)",
    )
    reject_cmd.add_argument(
        "--rejected",
        type=Path,
        default=Path("config/rejected_companies.toml"),
        help="rejected employers (default: config/rejected_companies.toml)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "fetch":
        return run_fetch(args.config, args.db, sys.stdout, sys.stderr)
    if args.command == "run":
        return run_daily(
            args.config,
            args.filters,
            args.db,
            args.reports,
            sys.stdout,
            sys.stderr,
            force=args.force,
        )
    if args.command == "refilter":
        return run_refilter(args.config, args.filters, args.db, args.reports, sys.stdout)
    if args.command == "discover":
        return run_discover(
            args.discovery,
            args.config,
            args.rejected,
            args.env,
            sys.stdout,
            db_path=args.db,
            reports_dir=args.reports,
        )
    if args.command == "approve":
        return run_approve(
            args.name,
            args.config,
            args.db,
            sys.stdout,
            sector=args.sector,
            source=args.source,
            board=args.board,
        )
    if args.command == "reject":
        return run_reject(args.name, args.reason, args.rejected, args.config, sys.stdout)
    parser.print_help()
    return EXIT_OK


def run_discover(
    discovery_path: Path,
    companies_path: Path,
    rejected_path: Path,
    env_path: Path,
    out: TextIO,
    pause: Callable[[float], None] | None = None,
    db_path: Path = Path("data/jobwatcher.db"),
    reports_dir: Path = Path("reports"),
) -> int:
    # Config, credentials, and the call budget are all checked before any request.
    try:
        config = discovery.load_discovery_config(discovery_path)
        known = discovery.known_employer_names(companies_path, rejected_path)
        creds = env.require(("ADZUNA_APP_ID", "ADZUNA_APP_KEY"), env_path)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=out)
        return EXIT_UNUSABLE

    minutes = max(1, round(config.calls * discovery.SECONDS_BETWEEN_CALLS / 60))
    print(
        f"Searching Adzuna: {config.calls} calls (budget {config.max_calls}), "
        f"ads from the last {config.max_days_old} days. Calls are spaced out, so this "
        f"takes about {minutes} minute{'s' if minutes != 1 else ''}.",
        file=out,
    )
    sleep = {"pause": pause} if pause is not None else {}
    try:
        with Store.open(db_path) as store, fetch.make_client() as http:
            client = discovery.AdzunaClient(
                http, creds["ADZUNA_APP_ID"], creds["ADZUNA_APP_KEY"], **sleep
            )
            result = discovery.discover(config, client, known)
            boards = detection.detect_boards(
                result.candidates,
                store,
                http,
                datetime.now(UTC),
                config.detect_top,
                **sleep,
            )
    except StoreError as exc:
        print(f"Database error: {exc}", file=out)
        return EXIT_UNUSABLE

    print(
        f"{len(result.candidates)} new employers; {result.skipped_known} already known "
        f"(on the company list or rejected); {result.ignored} on the ignore list; "
        f"{result.dropped_no_industry} dropped with no industry term in any ad. "
        f"{result.calls} calls, {len(result.errors)} failed.",
        file=out,
    )
    print(
        f"Job boards: {boards.checked} looked up this run ({boards.requests} requests), "
        f"{boards.from_earlier} remembered from earlier runs, {boards.not_checked} not "
        f"checked yet (limit {config.detect_top} per run), {len(boards.errors)} failed.",
        file=out,
    )
    for error in [*result.errors, *boards.errors]:
        print(f"  ERROR {error}", file=out)

    run_at = datetime.now(UTC).astimezone()
    path = daily.next_report_path(reports_dir, run_at.date(), prefix="discovery-")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_discovery_report(run_at, config, result, boards), encoding="utf-8")
    ready = sum(1 for c in result.candidates if c.board and c.board.status == "confirmed")
    print(f"{ready} ready to approve. Report: {path}", file=out)
    print("Source: The Adzuna API (https://www.adzuna.co.uk/)", file=out)
    # Any failed call is reported and makes the exit non-zero, like a failed board.
    failed = result.errors or boards.errors
    return EXIT_OK if not failed else EXIT_COMPANY_FAILED


def run_approve(
    name: str,
    companies_path: Path,
    db_path: Path,
    out: TextIO,
    sector: str | None = None,
    source: str | None = None,
    board: str | None = None,
) -> int:
    try:
        with Store.open(db_path) as store:
            approval = approvals.approve(
                name,
                companies_path,
                store,
                datetime.now(UTC).astimezone().date(),
                sector=sector,
                source=SourceName(source) if source else None,
                board=board,
            )
    except (ConfigError, StoreError) as exc:
        print(f"Not approved: {exc}", file=out)
        return EXIT_UNUSABLE
    print(
        f"Approved {approval.name}: added {approval.source}:{approval.board} to "
        f"{companies_path}. The next daily run starts watching it.",
        file=out,
    )
    if approval.warning:
        print(f"Note: {approval.warning}.", file=out)
    return EXIT_OK


def run_reject(
    name: str, reason: str, rejected_path: Path, companies_path: Path, out: TextIO
) -> int:
    try:
        approvals.reject(
            name, reason, rejected_path, companies_path, datetime.now(UTC).astimezone().date()
        )
    except ConfigError as exc:
        print(f"Not rejected: {exc}", file=out)
        return EXIT_UNUSABLE
    print(f"Rejected {name}: added to {rejected_path}; discovery won't propose it again.", file=out)
    return EXIT_OK


def run_refilter(
    config_path: Path, filters_path: Path, db_path: Path, reports_dir: Path, out: TextIO
) -> int:
    try:
        companies = load_companies(config_path)
        rules = load_filter_rules(filters_path)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=out)
        return EXIT_UNUSABLE

    run_at = datetime.now(UTC).astimezone()
    try:
        with Store.open(db_path) as store:
            result = daily.refilter(companies, rules, store, run_at, reports_dir)
    except StoreError as exc:
        print(f"Database error: {exc}", file=out)
        return EXIT_UNUSABLE

    counts = {o: 0 for o in ("match", "flagged", "possible", "excluded")}
    for item in result.items:
        counts[item.result.outcome.value] += 1
    print(f"Re-filtered {len(result.items)} open postings (no boards fetched).", file=out)
    print(
        f"Outcomes: {counts['match']} match, {counts['flagged']} flagged, "
        f"{counts['possible']} possible, {counts['excluded']} excluded.",
        file=out,
    )
    print(
        f"Changes: {len(result.changes)} changed outcome, "
        f"{result.first_filtered} filtered for the first time.",
        file=out,
    )
    print(f"Report: {result.report_path}", file=out)
    return EXIT_OK


def run_fetch(config_path: Path, db_path: Path, out: TextIO, err: TextIO | None = None) -> int:
    try:
        companies = load_companies(config_path)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=out)
        return EXIT_UNUSABLE

    # One timestamp for the whole run, so "first seen" means the same run
    # for every company.
    seen_at = datetime.now(UTC)
    try:
        with Store.open(db_path) as store, fetch.make_client() as client:
            results = fetch.fetch_all(companies, store, client, seen_at, errors=err)
    except StoreError as exc:
        print(f"Database error: {exc}", file=out)
        return EXIT_UNUSABLE

    print_summary(results, seen_at, out)
    # Anything short of every company OK exits non-zero, so a scheduled run
    # can never look healthy while a source is broken or suspicious.
    return EXIT_OK if all(r.outcome is Outcome.OK for r in results) else EXIT_COMPANY_FAILED


def run_daily(
    config_path: Path,
    filters_path: Path,
    db_path: Path,
    reports_dir: Path,
    out: TextIO,
    err: TextIO | None = None,
    force: bool = False,
) -> int:
    # Both configs are checked before any request, so a typo in either file
    # can't cost a run's worth of fetching.
    try:
        companies = load_companies(config_path)
        rules = load_filter_rules(filters_path)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=out)
        return EXIT_UNUSABLE

    # Local, timezone-aware time: the report's date and file name follow the
    # owner's calendar day. The store converts it to UTC for storage.
    seen_at = datetime.now(UTC).astimezone()
    try:
        with Store.open(db_path) as store, fetch.make_client() as client:
            result = daily.run(
                companies, rules, store, client, seen_at, reports_dir, errors=err, force=force
            )
    except StoreError as exc:
        print(f"Database error: {exc}", file=out)
        return EXIT_UNUSABLE

    if result.status is daily.RunStatus.ALREADY_RAN:
        print(
            f"Already ran today ({seen_at:%Y-%m-%d}); report: {result.report_path}. "
            "Use --force to run again.",
            file=out,
        )
        return EXIT_OK
    if result.status is daily.RunStatus.OFFLINE:
        print(
            "Offline, will retry: every company failed without a network response. "
            "Nothing was recorded and today is not counted.",
            file=out,
        )
        return EXIT_OFFLINE

    print_summary(result.companies, seen_at.astimezone(UTC), out)
    counts = {o: 0 for o in ("match", "flagged", "possible", "excluded")}
    for item in result.items:
        counts[item.result.outcome.value] += 1
    print(
        f"New postings: {counts['match']} match, {counts['flagged']} flagged, "
        f"{counts['possible']} possible, {counts['excluded']} excluded.",
        file=out,
    )
    print(f"Report: {result.report_path}", file=out)
    return (
        EXIT_OK if all(r.outcome is Outcome.OK for r in result.companies) else EXIT_COMPANY_FAILED
    )


_STATUS_LABEL = {Outcome.OK: "ok", Outcome.WARNING: "WARNING", Outcome.FAILED: "ERROR"}


def print_summary(results: list[CompanyResult], seen_at: datetime, out: TextIO) -> None:
    rows = [("Company", "Board", "Fetched", "New", "Reopened", "Closed", "Status")]
    for r in results:
        board_id = f"{r.company.source}:{r.company.board}"
        fetched = "-" if r.fetched is None else str(r.fetched)
        if r.board is None:
            counts = ("-", "-", "-")
        else:
            counts = (str(r.board.new), str(r.board.reopened), str(r.board.closed))
        status = _STATUS_LABEL[r.outcome]
        if r.message:
            status = f"{status}: {r.message}"
        rows.append((r.company.name, board_id, fetched, *counts, status))

    widths = [max(len(row[col]) for row in rows) for col in range(6)]
    print(f"Fetch run {seen_at:%Y-%m-%d %H:%M} UTC", file=out)
    for row in rows:
        # Text columns left-aligned, counts right-aligned; status unpadded.
        cells = [row[0].ljust(widths[0]), row[1].ljust(widths[1])]
        cells += [row[c].rjust(widths[c]) for c in range(2, 6)]
        print("  ".join([*cells, row[6]]), file=out)

    recorded = [r for r in results if r.board is not None]
    counts_by = {o: sum(r.outcome is o for r in results) for o in Outcome}
    print(
        f"{len(results)} companies: {counts_by[Outcome.OK]} ok, "
        f"{counts_by[Outcome.WARNING]} warning, {counts_by[Outcome.FAILED]} failed. "
        f"{sum(r.fetched or 0 for r in recorded)} fetched, "
        f"{sum(r.board.new for r in recorded if r.board)} new, "
        f"{sum(r.board.reopened for r in recorded if r.board)} reopened, "
        f"{sum(r.board.closed for r in recorded if r.board)} closed.",
        file=out,
    )
    if len(recorded) < len(results):
        print(
            "Companies with a warning or error were not recorded, "
            "so none of their postings were marked closed.",
            file=out,
        )


if __name__ == "__main__":
    raise SystemExit(main())
