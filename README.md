# Job Watcher

A personal tool that checks a list of target companies' public job boards once a day, finds postings that are new since the last run, filters them against configurable rules, and writes a short Markdown shortlist of what is worth a look.

## What it does

- Pulls postings from companies that host jobs on **Greenhouse, Lever, or Ashby**, using each platform's public job board data
- Stores every posting in a local SQLite database, tracking when it was first and last seen, and marking postings closed when they disappear
- Filters new postings into four groups, with the reason recorded for every decision:
  - **Match**: the title fits, the description overlaps with target industries or kinds of work, and location and travel are acceptable. Sorted by strongest overlap first.
  - **Flagged**: would be a Match, but something needs a human look (travel not stated, location unclear, a flag term found)
  - **Possible**: the title fits, but no industry or work overlap was found. Listed briefly, since generic titles can still be worth a glance.
  - **Excluded**: counted, with reasons available
- Reports source failures at the top of every report. A broken job board never looks like "no new jobs."
- Later phases add a read-only MCP server so Claude can query the database and help review fit, and a monthly company discovery step that proposes new companies for approval

## What it doesn't do

- **It never applies, emails, or contacts anyone.** It stops at a shortlist. Every decision stays with the owner.
- No LinkedIn, Indeed, Glassdoor, or any site that requires a login or restricts automated access
- No Workday, SuccessFactors, Taleo, or iCIMS boards (most large employers)
- No LLM calls in the fetch, filter, and report pipeline. That work is plain, deterministic Python.
- No data leaves your machine. The database, config, and reports all stay local.
- One request per company per run, with timeouts and a clear user agent

## Status

Phase 0 (repo setup) is in progress. The package installs and CI runs, but **`fetch` and `run` are not built yet**. See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the full phase plan.

| Phase | What | Release |
|---|---|---|
| 0 | Repo setup, CI, templates, README | v0.1.0 |
| 1 | Fetch and store | v0.2.0 |
| 2 | Filter, report, daily schedule | v0.3.0 |
| 3 | MCP server | v0.4.0 |
| 4 | Claude fit review | v0.5.0 |
| 5 | Company discovery | v0.6.0 |

## Setup

Requires Windows (the primary environment), [uv](https://docs.astral.sh/uv/), and Python 3.14 or newer. uv can install Python for you.

1. Clone the repo and install dependencies:

   ```powershell
   git clone https://github.com/kjsy96/job-watcher.git
   cd job-watcher
   uv sync
   ```

2. Create your config files from the examples:

   ```powershell
   Copy-Item config\companies.example.toml config\companies.toml
   Copy-Item config\filters.example.toml config\filters.toml
   ```

3. Edit both files:
   - `config/companies.toml`: one `[[company]]` block per target company. `source` is `greenhouse`, `lever`, or `ashby`, and `board` is the company's identifier on that platform, usually the slug in its public job board URL.
   - `config/filters.toml`: role titles, industries, kinds of work, location tiers, sponsorship terms, flag terms, and the travel limit. Comments in the example explain each section.

   Your real `companies.toml` and `filters.toml` are gitignored, so they can't be committed by accident. Only the `.example.toml` files are tracked.

## Development

Every change follows the workflow in [CLAUDE.md](CLAUDE.md): an issue first, a branch named `<issue-number>-<short-slug>`, then a PR with a test plan that passes CI.

Run the full check before opening a PR. CI runs the same four checks on every PR and every push to `main`.

```powershell
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy
```

Tests never hit the network. Each job board source is tested against saved JSON fixtures in `tests/fixtures/`.
