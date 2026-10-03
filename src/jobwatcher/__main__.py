"""Command-line entry point: ``python -m jobwatcher``.

Exit codes:
  0  every company was fetched and recorded
  1  the run finished, but at least one company failed (see the summary)
  2  nothing ran: bad arguments, config, or database
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from jobwatcher import __version__, fetch
from jobwatcher.config import ConfigError, load_companies
from jobwatcher.fetch import CompanyResult
from jobwatcher.store import Store, StoreError

EXIT_OK = 0
EXIT_COMPANY_FAILED = 1
EXIT_UNUSABLE = 2


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
    fetch_cmd.add_argument(
        "--config",
        type=Path,
        default=Path("config/companies.toml"),
        help="company list (default: config/companies.toml)",
    )
    fetch_cmd.add_argument(
        "--db",
        type=Path,
        default=Path("data/jobwatcher.db"),
        help="SQLite database, created if missing (default: data/jobwatcher.db)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "fetch":
        return run_fetch(args.config, args.db, sys.stdout)
    parser.print_help()
    return EXIT_OK


def run_fetch(config_path: Path, db_path: Path, out: TextIO) -> int:
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
            results = fetch.fetch_all(companies, store, client, seen_at)
    except StoreError as exc:
        print(f"Database error: {exc}", file=out)
        return EXIT_UNUSABLE

    print_summary(results, seen_at, out)
    return EXIT_OK if all(r.ok for r in results) else EXIT_COMPANY_FAILED


def print_summary(results: list[CompanyResult], seen_at: datetime, out: TextIO) -> None:
    rows = [("Company", "Board", "Fetched", "New", "Reopened", "Closed", "Status")]
    for r in results:
        board_id = f"{r.company.source}:{r.company.board}"
        if r.board is None:
            rows.append((r.company.name, board_id, "-", "-", "-", "-", f"ERROR: {r.error}"))
        else:
            rows.append(
                (
                    r.company.name,
                    board_id,
                    str(r.fetched),
                    str(r.board.new),
                    str(r.board.reopened),
                    str(r.board.closed),
                    "ok",
                )
            )

    widths = [max(len(row[col]) for row in rows) for col in range(6)]
    print(f"Fetch run {seen_at:%Y-%m-%d %H:%M} UTC", file=out)
    for row in rows:
        # Text columns left-aligned, counts right-aligned; status unpadded.
        cells = [row[0].ljust(widths[0]), row[1].ljust(widths[1])]
        cells += [row[c].rjust(widths[c]) for c in range(2, 6)]
        print("  ".join([*cells, row[6]]), file=out)

    done = [r for r in results if r.board is not None]
    failed = len(results) - len(done)
    print(
        f"{len(results)} companies: {len(done)} ok, {failed} failed. "
        f"{sum(r.fetched or 0 for r in done)} fetched, "
        f"{sum(r.board.new for r in done if r.board)} new, "
        f"{sum(r.board.reopened for r in done if r.board)} reopened, "
        f"{sum(r.board.closed for r in done if r.board)} closed.",
        file=out,
    )
    if failed:
        print(
            "Failed companies were not recorded, so none of their postings were marked closed.",
            file=out,
        )


if __name__ == "__main__":
    raise SystemExit(main())
