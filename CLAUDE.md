# CLAUDE.md - Job Watcher

Read this file at the start of every session. Full scope, design, and phase plan live in `PROJECT_PLAN.md`. Read the relevant phase section there before starting any issue.

If `CLAUDE.local.md` exists, read it too. It holds private working context for the owner and is gitignored.

## What this project is

A personal tool that pulls job postings from a list of target companies through public job board data (Greenhouse, Lever, Ashby), filters them against configurable rules, and produces a daily shortlist of new matches. Later phases expose the posting database to Claude through a custom MCP server so Claude can help review fit.

The owner makes every application decision. This tool stops at a shortlist. It never applies, emails, or contacts anyone.

## Design principles (non-negotiable)

1. **Deterministic code does the repeatable work. Claude does the judgment.** Fetching, storing, deduplicating, and rule-based filtering are plain Python. No LLM calls in phases 1 and 2.
2. **Flag, don't guess.** If a rule can't tell whether a posting matches (for example, travel percentage not stated), flag it with a reason instead of silently including or excluding it. Every exclusion and flag records why.
3. **No silent failures.** A company whose board fails to load, returns an unexpected shape, or returns zero jobs when it normally has some is reported in the run summary. A broken source must never look like "no new jobs."
4. **Polite, minimal network use.** One request per company per run, a clear user agent, timeouts, and a daily cadence. Use only the public job board endpoints. No scraping of LinkedIn, Indeed, or any site that requires login or restricts automated access.
5. **Personal data never enters git.** See "Files that must stay out of git" below.

## Workflow for every change (no "too small" exception)

1. File a GitHub Issue first.
2. Branch off `main` using `<issue-number>-<short-slug>`.
3. Commit messages explain why, not just what.
4. Run the full check locally before opening a PR: `pytest`, `ruff check`, `ruff format --check`, `mypy`.
5. Open a PR with a test plan. CI must pass.
6. Wait for the owner's review and approval. Squash-merge.
7. Tag a release at the end of each phase.

## Stack

- Python 3.14, Windows is the primary environment (paths and scheduling must work on Windows)
- `httpx` for HTTP, `sqlite3` (standard library) for storage, `tomllib` (standard library) for config
- `pytest`, `ruff`, `mypy` (strict on `src/`), GitHub Actions for CI
- Phase 3: official Python MCP SDK (`mcp` package)

## Testing rules

- Tests never hit the network. Each source has saved JSON fixtures in `tests/fixtures/`, captured once from a real public endpoint and trimmed.
- Every filter rule has tests for match, no match, and the "can't tell, so flag" case.
- When a bug is found, add a test that reproduces it before fixing it.

## Files that must stay out of git

- `config/companies.toml` (real target list). Commit `config/companies.example.toml` instead.
- `config/filters.toml` (real rules). Commit `config/filters.example.toml` instead.
- `config/discovery.toml` (real target categories, Phase 5). Commit `config/discovery.example.toml` instead.
- `config/rejected_companies.toml` (Phase 5)
- `data/` (SQLite database)
- `reports/` (generated shortlists)
- `profile/` (anything derived from the owner's career notes, used in phase 4)
- `.env` and any credentials
- `CLAUDE.local.md`

## Checkpoints where you must stop and prompt the owner

- **Aggregator API test.** As soon as the v0.2.0 release (end of Phase 1) is tagged, do not start Phase 2. Tell the owner the aggregator test is due, summarize the steps from the "Checkpoint - Aggregator API test" section of `PROJECT_PLAN.md`, and ask whether to run it now or defer it. If deferred, record that in `docs/decisions.md`. If the owner starts a session after v0.2.0 and the test has not been run or deferred, give that reminder before doing anything else.

## Things to ask the owner about rather than decide alone

- Adding or removing a filter rule, or changing what counts as an exclusion versus a flag
- Adding a new data source beyond Greenhouse, Lever, and Ashby
- Anything that would send data anywhere other than the local machine
- Scope changes to a phase
