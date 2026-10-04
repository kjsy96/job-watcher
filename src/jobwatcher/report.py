"""Render one run's results as a Markdown report.

A pure function: fetch results and filtered new postings in, Markdown out.
Choosing which postings are new and writing the file belong to the run
command (issue 2.5).

Order (PROJECT_PLAN.md, issue 2.4): run summary, then source problems
before any results, then Matches by tier and score, Flagged (pathway
first), Possible, the Excluded count with reasons, and a table of every
company. Empty sections say so instead of disappearing, so a quiet day
can't be mistaken for a broken report.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from jobwatcher.fetch import CompanyResult
from jobwatcher.fetch import Outcome as FetchOutcome
from jobwatcher.filters import FilterResult, Outcome
from jobwatcher.models import Posting

SEP = " \N{MIDDLE DOT} "  # between fields in one entry
# Characters that can change meaning mid-line in Markdown text and table
# cells. Kept to the minimum so the raw file stays readable: parentheses
# are harmless once "[" and "]" are escaped (no link can start), and an
# underscore inside a word ("non_us_remote_terms") can't start emphasis,
# so only an underscore at the edge of a word is escaped.
_MARKDOWN_SPECIAL = re.compile(r"[\\`*\[\]<>|]|(?<!\w)_|_(?!\w)")


@dataclass(frozen=True, slots=True)
class ReportItem:
    """One new posting and its filter result."""

    posting: Posting
    result: FilterResult


def render_report(
    run_at: datetime, companies: Sequence[CompanyResult], items: Sequence[ReportItem]
) -> str:
    by_outcome: dict[Outcome, list[ReportItem]] = {o: [] for o in Outcome}
    for item in items:
        by_outcome[item.result.outcome].append(item)

    lines: list[str] = [f"# Job Watcher report: {run_at:%Y-%m-%d}", ""]
    lines += _summary(run_at, companies, by_outcome)
    lines += _source_problems(companies)
    lines += _matches(by_outcome[Outcome.MATCH])
    lines += _flagged(by_outcome[Outcome.FLAGGED])
    lines += _possible(by_outcome[Outcome.POSSIBLE])
    lines += _excluded(by_outcome[Outcome.EXCLUDED])
    lines += _company_table(companies)
    return "\n".join(lines).rstrip() + "\n"


@dataclass(frozen=True, slots=True)
class Change:
    """A posting whose outcome differs from its previously stored result."""

    posting: Posting
    before: str
    after: str


def render_refilter_report(
    run_at: datetime,
    items: Sequence[ReportItem],
    changes: Sequence[Change],
    first_filtered: int,
) -> str:
    """Every open stored posting under the current rules, and what changed.

    Same layout as the daily report, but nothing was fetched, so the source
    and company sections say so rather than looking empty or healthy.
    """
    by_outcome: dict[Outcome, list[ReportItem]] = {o: [] for o in Outcome}
    for item in items:
        by_outcome[item.result.outcome].append(item)

    lines: list[str] = [
        f"# Job Watcher refilter: {run_at:%Y-%m-%d}",
        "",
        f"Run at {_clock(run_at)}",
        "",
        f"- **Re-filtered:** {len(items)} open stored postings with the current rules. "
        "No boards were fetched.",
        f"- **Outcomes:** {len(by_outcome[Outcome.MATCH])} match, "
        f"{len(by_outcome[Outcome.FLAGGED])} flagged, "
        f"{len(by_outcome[Outcome.POSSIBLE])} possible, "
        f"{len(by_outcome[Outcome.EXCLUDED])} excluded",
        f"- **Changes since last filtered:** {len(changes)} changed outcome, "
        f"{first_filtered} filtered for the first time",
        "",
        "## Source problems",
        "",
        "Not checked: refilter reads stored postings and makes no requests. "
        "Use `run` or `fetch` to check the job boards.",
        "",
    ]
    lines += _changes(changes)
    lines += _matches(by_outcome[Outcome.MATCH], scope="")
    lines += _flagged(by_outcome[Outcome.FLAGGED], scope="")
    lines += _possible(by_outcome[Outcome.POSSIBLE], scope="")
    lines += _excluded(by_outcome[Outcome.EXCLUDED], scope="")
    return "\n".join(lines).rstrip() + "\n"


_OUTCOME_ORDER = {o.value: n for n, o in enumerate(Outcome)}


def _changes(changes: Sequence[Change]) -> list[str]:
    lines = ["## Changes", ""]
    if not changes:
        return [*lines, "No outcome changed.", ""]
    lines += ["Outcomes that differ from the last stored result, best new outcome first.", ""]
    ordered = sorted(
        changes,
        key=lambda c: (
            _OUTCOME_ORDER.get(c.after, 99),
            c.posting.company.lower(),
            c.posting.title.lower(),
        ),
    )
    for change in ordered:
        lines.append(
            f"- {_link(change.posting)}: {escape(change.posting.company)}: "
            f"{change.before} -> **{change.after}**"
        )
    return [*lines, ""]


def escape(text: str) -> str:
    """Escape text from a posting so it can't break links, emphasis, or tables."""
    return _MARKDOWN_SPECIAL.sub(lambda m: "\\" + m.group(0), " ".join(text.split()))


def _clock(moment: datetime) -> str:
    """'2026-10-04 07:30 (UTC+00:00)'. A numeric offset, because Windows
    time zone names are long and vary by machine."""
    offset = moment.strftime("%z")  # "+1100", or "" for a naive datetime
    zone = f" (UTC{offset[:3]}:{offset[3:]})" if offset else ""
    return f"{moment:%Y-%m-%d %H:%M}{zone}"


def _link(posting: Posting) -> str:
    # <...> around the URL keeps parentheses or spaces in it from ending the link.
    return f"[{escape(posting.title)}](<{posting.url}>)"


def _summary(
    run_at: datetime,
    companies: Sequence[CompanyResult],
    by_outcome: dict[Outcome, list[ReportItem]],
) -> list[str]:
    status = Counter(c.outcome for c in companies)
    recorded = [c.board for c in companies if c.board is not None]
    fetched = sum(c.fetched or 0 for c in companies if c.board is not None)
    return [
        f"Run at {_clock(run_at)}",
        "",
        f"- **Companies:** {len(companies)} checked: {status[FetchOutcome.OK]} ok, "
        f"{status[FetchOutcome.WARNING]} warning, {status[FetchOutcome.FAILED]} failed",
        f"- **Postings:** {fetched} fetched, {sum(b.new for b in recorded)} new, "
        f"{sum(b.reopened for b in recorded)} reopened, {sum(b.closed for b in recorded)} closed",
        f"- **New postings:** {len(by_outcome[Outcome.MATCH])} match, "
        f"{len(by_outcome[Outcome.FLAGGED])} flagged, "
        f"{len(by_outcome[Outcome.POSSIBLE])} possible, "
        f"{len(by_outcome[Outcome.EXCLUDED])} excluded",
        "",
    ]


def _source_problems(companies: Sequence[CompanyResult]) -> list[str]:
    problems = [c for c in companies if c.outcome is not FetchOutcome.OK]
    lines = ["## Source problems", ""]
    if not problems:
        return [*lines, "None. Every company's board was read and recorded.", ""]
    lines.append(
        "These companies were **not** recorded this run, so their new postings "
        "are missing below. None of their postings were marked closed."
    )
    lines.append("")
    for c in problems:
        label = "WARNING" if c.outcome is FetchOutcome.WARNING else "ERROR"
        lines.append(
            f"- **{label}: {escape(c.company.name)}** "
            f"(`{c.company.source}:{c.company.board}`): {escape(c.message or '')}"
        )
    return [*lines, ""]


def _by_score(items: list[ReportItem]) -> list[ReportItem]:
    return sorted(
        items,
        key=lambda i: (-i.result.score, i.posting.company.lower(), i.posting.title.lower()),
    )


def _entry(item: ReportItem, reasons: list[str], marker: str = "") -> list[str]:
    p, r = item.posting, item.result
    where = escape(p.location) if p.location else "location not stated"
    head = f"- {marker}**{_link(p)}**: {escape(p.company)}{SEP}{where}{SEP}score {r.score}"
    return [head, *(f"  - {escape(reason)}" for reason in reasons)]


def _matches(items: list[ReportItem], scope: str = "new ") -> list[str]:
    lines = ["## Matches", ""]
    if not items:
        return [*lines, f"No {scope}matches.", ""]
    tiers = sorted({i.result.tier.number for i in items if i.result.tier is not None})
    for number in tiers:
        in_tier = [i for i in items if i.result.tier and i.result.tier.number == number]
        label = in_tier[0].result.tier.label if in_tier[0].result.tier else ""
        lines += [f"### Tier {number}: {escape(label)}", ""]
        for item in _by_score(in_tier):
            lines += _entry(item, item.result.reasons)
        lines.append("")
    return lines


def _flagged(items: list[ReportItem], scope: str = "new ") -> list[str]:
    lines = ["## Flagged", ""]
    if not items:
        return [*lines, f"No {scope}flagged postings.", ""]
    lines += ["Each needs a human look; the first reason is why.", ""]
    pathway = [i for i in items if i.result.pathway_note]
    others = [i for i in items if not i.result.pathway_note]
    for item in _by_score(pathway):
        lines += _entry(item, item.result.reasons, marker="(pathway) ")
    for item in _by_score(others):
        lines += _entry(item, item.result.reasons)
    return [*lines, ""]


def _possible(items: list[ReportItem], scope: str = "new ") -> list[str]:
    lines = ["## Possible", ""]
    if not items:
        return [*lines, f"No {scope}possible postings.", ""]
    lines += ["Title fits, but no domain or work terms were found.", ""]
    for item in sorted(items, key=lambda i: (i.posting.company.lower(), i.posting.title.lower())):
        p = item.posting
        where = escape(p.location) if p.location else "location not stated"
        lines.append(f"- {_link(p)}: {escape(p.company)}{SEP}{where}")
    return [*lines, ""]


def _category(reason: str) -> str:
    for prefix, name in (
        ("title", "title"),
        ("location", "location"),
        ("travel", "travel"),
        ("sponsorship", "sponsorship"),
    ):
        if reason.startswith(prefix):
            return name
    return "other"


def _excluded(items: list[ReportItem], scope: str = "new ") -> list[str]:
    lines = ["## Excluded", ""]
    if not items:
        return [*lines, f"No {scope}postings were excluded.", ""]
    # A posting can be excluded for several reasons; each category counts it once.
    counts = Counter(cat for i in items for cat in {_category(r) for r in i.result.reasons})
    breakdown = ", ".join(f"{name} {n}" for name, n in counts.most_common())
    lines += [f"{len(items)} {scope}postings excluded (by reason: {breakdown}).", ""]
    lines += ["<details>", "<summary>Every excluded posting and why</summary>", ""]
    for item in sorted(items, key=lambda i: (i.posting.company.lower(), i.posting.title.lower())):
        p = item.posting
        why = "; ".join(escape(r) for r in item.result.reasons)
        lines.append(f"- {_link(p)}: {escape(p.company)}: {why}")
    return [*lines, "", "</details>", ""]


def _company_table(companies: Sequence[CompanyResult]) -> list[str]:
    lines = ["## Companies", ""]
    if not companies:
        return [*lines, "No companies were checked.", ""]
    lines += [
        "| Company | Board | Fetched | New | Closed | Status |",
        "|---|---|---:|---:|---:|---|",
    ]
    for c in companies:
        board = f"`{c.company.source}:{c.company.board}`"
        if c.board is None:
            counts = ["-", "-", "-"]
        else:
            counts = [str(c.fetched), str(c.board.new), str(c.board.closed)]
        status = c.outcome.value if c.message is None else f"{c.outcome.value}: {escape(c.message)}"
        lines.append(f"| {escape(c.company.name)} | {board} | {' | '.join(counts)} | {status} |")
    return [*lines, ""]
