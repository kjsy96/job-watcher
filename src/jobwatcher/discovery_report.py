"""Render a discovery run as a Markdown report (issue 2b.4).

Grouped so the next action is obvious: employers with a confirmed board
are ready to approve (the exact command is shown), possible boards need a
look first, employers with no readable board are for tracking by hand or
rejecting, and employers beyond this run's lookup limit are listed briefly.

Adzuna's terms require crediting "The Adzuna API" wherever its data is
published; the report is private, but it carries the credit anyway.
"""

from datetime import datetime

from jobwatcher.detection import DetectionSummary
from jobwatcher.discovery import Candidate, DiscoveryConfig, DiscoveryResult
from jobwatcher.report import SEP, clock, escape

ADZUNA_CREDIT = "Source: [The Adzuna API](https://www.adzuna.co.uk/)"


def render_discovery_report(
    run_at: datetime,
    config: DiscoveryConfig,
    result: DiscoveryResult,
    boards: DetectionSummary,
) -> str:
    ready = [c for c in result.candidates if c.board and c.board.status == "confirmed"]
    check = [c for c in result.candidates if c.board and c.board.status == "possible"]
    none_found = [c for c in result.candidates if c.board and c.board.status == "not_found"]
    unchecked = [c for c in result.candidates if c.board is None]

    lines = [
        f"# Job Watcher discovery: {run_at:%Y-%m-%d}",
        "",
        f"Run at {clock(run_at)}",
        "",
        f"- **New employers:** {len(result.candidates)} "
        f"(ads from the last {config.max_days_old} days)",
        f"- **Filtered out:** {result.skipped_known} already known (on the company list or "
        f"rejected), {result.ignored} on the ignore list, {result.dropped_no_industry} with "
        "no industry term in any ad",
        f"- **Job boards:** {len(ready)} ready to approve, {len(check)} to check first, "
        f"{len(none_found)} with no readable board found, {len(unchecked)} not checked yet "
        f"(limit {config.detect_top} per run; later runs continue)",
        f"- **Requests:** {result.calls} Adzuna calls, {boards.requests} job board lookups",
        "",
    ]

    errors = [*result.errors, *boards.errors]
    lines += ["## Problems", ""]
    if errors:
        lines.append(
            "These searches or lookups failed, so some employers may be missing below. "
            "Failed board lookups are retried on the next run."
        )
        lines.append("")
        lines += [f"- {escape(e)}" for e in errors]
    else:
        lines.append("None. Every search and board lookup succeeded.")
    lines.append("")

    lines += _section(
        "Ready to approve",
        "A board was found and one of its job titles matches this employer's ads.",
        ready,
        approve_hint=True,
    )
    lines += _section(
        "Check first",
        "A board exists under the guessed name, but none of its titles match the ads, so "
        "it may belong to a different organization with the same name. Look at it before "
        "approving.",
        check,
        approve_hint=True,
    )
    lines += _section(
        "No readable board found",
        "No Greenhouse, Lever, or Ashby board under the guessed name. The employer may use "
        "Workday or similar (track by hand), or a board with a different name (approve "
        "with `--source` and `--board`). Or reject it.",
        none_found,
        approve_hint=False,
    )

    lines += ["## Not checked yet", ""]
    if unchecked:
        lines.append("Beyond this run's lookup limit; the next runs continue down this list.")
        lines.append("")
        lines += [f"- {_head(c)}" for c in unchecked]
    else:
        lines.append("None.")
    lines += ["", "---", "", ADZUNA_CREDIT, ""]
    return "\n".join(lines)


def _head(c: Candidate) -> str:
    industry = ", ".join(c.industry_hits) if c.industry_hits else "none"
    return (
        f"**{escape(c.name)}**{SEP}{'/'.join(c.countries)}{SEP}{len(c.jobs)} ad(s)"
        f"{SEP}industry: {escape(industry)}"
    )


def _section(title: str, intro: str, items: list[Candidate], approve_hint: bool) -> list[str]:
    lines = [f"## {title}", ""]
    if not items:
        return [*lines, "None.", ""]
    lines += [intro, ""]
    for c in items:
        lines.append(f"- {_head(c)}")
        if c.board is not None:
            where = f"`{c.board.source}:{c.board.board}`, " if c.board.source else ""
            lines.append(f"  - board: {where}{escape(c.board.detail)}")
        for job_title in c.titles[:3]:
            lines.append(f"  - {escape(job_title)}")
        if approve_hint:
            name = c.name.replace('"', "'")
            lines.append(f'  - approve: `python -m jobwatcher approve "{name}"`')
    return [*lines, ""]
