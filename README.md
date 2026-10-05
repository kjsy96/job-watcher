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

Phase 1 (fetch and store) is complete as of v0.2.0. Phase 2 is in progress: **`run` works**. It fetches every board, filters the new postings, and writes a daily Markdown report. Still to come in Phase 2 are the once-per-day logic, the Windows Task Scheduler setup, and a week of rule tuning. See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the full phase plan.

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

## Usage

### Daily run

Fetch every board, filter the new postings, and write the day's report:

```powershell
uv run python -m jobwatcher run
```

The report goes to `reports/YYYY-MM-DD.md`, named for the local date.

**`run` counts at most one run per day,** so it's safe to start it every hour. Later attempts on the same day exit straight away without fetching. Use `--force` to run again anyway; a forced run writes `YYYY-MM-DD-2.md` and never replaces an earlier report.

**When the laptop is offline** (every company fails with no network response at all), `run` records nothing, writes no report, and doesn't count the day, so the next attempt tries again. If some boards fail but at least one works, that counts as the day's run, and the report lists the failures first.

Sections, in order:

1. **Run summary**
2. **Source problems.** These are always listed before any results.
3. **Matches**, grouped by location tier and ranked by overlap score
4. **Flagged**, with the reason to look shown first
5. **Possible**
6. **Excluded**: a count, with the reasons available
7. **Companies**: a table of every company checked

Each new posting's outcome and reasons are also stored in the database. [docs/rules.md](docs/rules.md) explains how each outcome is decided.

The defaults are `config/companies.toml`, `config/filters.toml`, `data/jobwatcher.db`, and `reports/`, all relative to the current folder. Use `--config`, `--filters`, `--db`, and `--reports` to change them.

### Re-check stored postings after editing the rules

Re-run the current filter rules over every open posting already stored, without fetching anything:

```powershell
uv run python -m jobwatcher refilter
```

It writes `reports/refilter-YYYY-MM-DD.md` with the same layout as the daily report, plus a **Changes** section listing every posting whose outcome differs from its last stored result (for example `flagged -> match`). Run it after each edit to `config/filters.toml` to see exactly what the edit changed across all current postings.

### Fetch only

Fetch every company's board once and record the results, without filtering or a report:

```powershell
uv run python -m jobwatcher fetch
```

It prints one row per company (fetched, new, reopened, and closed counts, or the error) and a totals line. The defaults are `config/companies.toml` and `data/jobwatcher.db`, relative to the current folder. Use `--config` and `--db` to point elsewhere.

| Exit code | Meaning |
|---|---|
| 0 | Every company was fetched and recorded, or (`run`) today already had a counted run |
| 1 | The run finished, but at least one company failed or has a warning (see its row) |
| 2 | Nothing ran: bad arguments, an invalid config, or an unusable database |
| 3 | `run` only: offline. Nothing was recorded and today isn't counted, so the next attempt retries. |

A company is only recorded when its board was read successfully. Anything else is shown in its row and leaves that company's data untouched for the run:

- **ERROR**: the board couldn't be read or used. Causes include a timeout, a connection failure, an HTTP error status, a response that isn't JSON or has an unexpected shape, or an unexpected error (the full traceback goes to stderr).
- **WARNING**: the board answered with 0 jobs while postings were still open for it. This usually means the board moved or broke, so the postings are kept open rather than marked closed. The warning repeats each run until the board has jobs again or the company is removed from the config.

One company's problem never stops the others.

## Discovering new companies

The daily run only watches companies already in `config/companies.toml`. Discovery finds employers you haven't heard of yet, using the [Adzuna API](https://developer.adzuna.com) only to learn who is hiring for your kinds of roles. Adzuna's short descriptions are never used to judge postings: once you approve an employer, its full postings come from its own job board and go through the normal filters.

**Setup:**
1. Get a free Adzuna API key.
2. Copy `.env.example` to `.env` and fill in `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`. `.env` is gitignored.
3. Copy `config/discovery.example.toml` to `config/discovery.toml` and set your role terms, industry terms, and countries. The example's comments explain each setting.

**Run it** (about weekly; it takes a few minutes, because calls are spaced to respect Adzuna's limits):

```powershell
uv run python -m jobwatcher discover
```

It writes `reports\discovery-YYYY-MM-DD.md`, grouped by what to do next:

- **Ready to approve:** the employer's Greenhouse, Lever, or Ashby board was found, and one of its job titles matches the employer's ads.
- **Check first:** a board exists under the guessed name, but no titles match. It may belong to a different organization.
- **No readable board found:** the employer may use Workday or similar, so track it by hand, or reject it.
- **Not checked yet:** board lookups are limited per run (`detect_top`). Later runs continue down the list, and no employer is looked up twice.

**Then decide.** Nothing is ever added automatically:

```powershell
uv run python -m jobwatcher approve "Employer Name"
```

```powershell
uv run python -m jobwatcher reject "Employer Name" --reason "recruiter, not an employer"
```

- **`approve`** adds the employer to `config/companies.toml`, keeping your comments, and the next daily run starts watching it. If you found the board yourself, add `--source lever --board theirboard`. `--sector` is optional, and left empty by default, because a sector counts toward every one of that company's posting scores.
- **`reject`** adds the employer to the gitignored `config/rejected_companies.toml` with your reason, so discovery never proposes it again.

## Scheduled daily run (Windows Task Scheduler)

Task Scheduler starts `scripts\run_daily.bat` at logon and every hour. That's safe because `run` counts only one real run per day, and being offline doesn't use it up. The task never wakes the laptop, and it runs with no window.

### Set it up with the script

From the repo folder in PowerShell, preview first, then register:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -DryRun
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
```

This creates `\JobWatcher\Job Watcher daily run` for your user, with no admin rights needed. Running it again updates the task.

### Or set it up by hand

In **Task Scheduler**, choose **Create Task**:

1. **General:** name it `Job Watcher daily run`, and choose **Run only when user is logged on**.
2. **Triggers:**
   - **At log on** (your user).
   - **One time**, starting today at 00:00, with **Repeat task every: 1 hour**, **for a duration of: Indefinitely**.
3. **Actions:** **Start a program**.
   - Program: `C:\Windows\System32\conhost.exe`
   - Arguments: `--headless "<repo>\scripts\run_daily.bat"`
   - Start in: `<repo>`

   Replace `<repo>` with this folder's full path. `conhost --headless` keeps a console window from flashing up every hour.
4. **Conditions:**
   - Tick **Start only if the following network connection is available: Any connection**.
   - Untick **Wake the computer to run this task**.
   - Untick **Start the task only if the computer is on AC power**.
5. **Settings:**
   - Tick **Run task as soon as possible after a scheduled start is missed**.
   - Set **Stop the task if it runs longer than: 30 minutes**.
   - Set **If the task is already running: Do not start a new instance**.

### Check that it's working

- **`logs\run.log`** (gitignored) gets one block per attempt: the time, run's output, and the exit code. Most hourly blocks say "Already ran today".
- **`reports\`** gets one `YYYY-MM-DD.md` per day the laptop was online.
- **Task Scheduler's "Last Run Result"** shows run's exit code:

| Last Run Result | Meaning |
|---|---|
| `0x0` | OK, or already ran today |
| `0x1` | Ran, but some companies failed. The report lists them first. |
| `0x2` | Config or database problem; nothing ran. See `logs\run.log`. |
| `0x3` | Offline. Today isn't counted, so the next hour retries. |

### Remove it

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Remove
```

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
