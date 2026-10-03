# Job Watcher - Project Plan

Version 11 - October 2026 (v11: recorded the owner's decisions on repo visibility, name, filters, and report location; company list still open. v10: added pathway title terms to sponsorship tiers, so occupation-based work permit candidates are noted and not excluded on eligibility wording alone. v9: added location tiers that require visa sponsorship, sponsorship detection in issue 2.3 (now 8 points), and handling for ambiguous short codes like "CA". v8: replaced the single location allow list with preference tiers, US-wide remote handling, non-US remote exclusion, and flagging of ambiguous locations. v7: added story point estimates to every issue, with phase totals. v6: restructured filtering into separate role title, domain, and work term lists with an overlap score and a new Possible tier, so generic titles in strong-fit industries are not missed. v5: added issue 2.9, a UI review after the Phase 2 week of real use. v4: added Phase 5, Claude-assisted company discovery with owner approval; moved company discovery out of the out-of-scope list. v3: prepared for a public repo by neutralizing example locations, rewording the purpose section, and removing resume wording. v2: added the aggregator API test checkpoint after Phase 2; recorded the job alert email option as considered and set aside)

## 1. Purpose

Manually searching job boards every day is slow and inconsistent. Job Watcher checks a fixed list of target companies every day, finds postings that are new since the last run, applies filter rules, and writes a short report of what is worth looking at.

Later phases connect the posting database to Claude through a custom MCP server, so an AI assistant can answer questions about new postings and help review fit. The automated steps stay in plain code; the assistant is used only where judgment is needed.

## 2. Success criteria

- A single command (`python -m jobwatcher run`) fetches, stores, filters, and reports. It completes once per day, at whatever time the laptop is first on and online, without needing a fixed schedule.
- Each daily report shows only postings not seen before, grouped as Match (strongest overlap first), Flagged (needs a human look), Possible (title fits, no domain or work overlap found), and a count of Excluded with reasons available.
- A source failure is visible in the report, never silent.
- (Phase 3) Claude Desktop or Claude Code can query the posting database through a custom MCP server.
- (Phase 4) Claude can produce a ranked fit review of new postings against the owner's background, with the gap for each one stated plainly.
- (Phase 5) A monthly discovery run proposes new companies that fit the owner's background, and approved ones are added to the company list.

## 3. Scope

### In scope (v1)

- Companies that host jobs on Greenhouse, Lever, or Ashby
- Rule-based filtering: title keywords, description keywords, location, travel, exclusion terms
- Local SQLite storage with deduplication and "first seen" / "last seen" tracking
- Markdown daily report
- Windows Task Scheduler setup
- Custom MCP server exposing read-only tools over the database
- Claude-assisted fit review that produces a recommendation only
- Claude-assisted company discovery that proposes new companies for the owner to approve

### Out of scope (v1)

- LinkedIn, Indeed, Glassdoor, or any site requiring login or restricting automated access
- Workday, SuccessFactors, Taleo, iCIMS, and other large-employer systems (most big OEMs such as Caterpillar, Komatsu, Sandvik). Pulling from these directly is not planned. Coverage of large employers through a job aggregator API is tested at the checkpoint after Phase 2.
- Parsing job alert emails (LinkedIn or company career sites). Considered and set aside: alerts contain only titles and links, so full descriptions would still need manual reading.
- Adding companies to the list without the owner's approval. Discovery proposes; the owner decides.
- Applying, emailing, messaging, or any action outside the local machine
- Calling the Claude API from scripts (avoids ongoing cost). The fit review runs inside Claude Desktop or Claude Code through MCP.
- A web UI. Reconsidered after the Phase 2 week of real use (see issue 2.9).

## 4. Architecture

```
companies.toml ──► fetchers (greenhouse / lever / ashby)
                        │  normalize to one Posting format
                        ▼
                   SQLite database ◄──── MCP server (Phase 3, read-only tools)
                        │                      ▲
                        ▼                      │
filters.toml ───► rule engine                Claude Desktop / Claude Code
                        │                      (Phase 4 fit review)
                        ▼
                 reports/YYYY-MM-DD.md
```

### Proposed repo layout

```
job-watcher/
  CLAUDE.md
  PROJECT_PLAN.md
  README.md
  pyproject.toml
  .gitignore
  .github/workflows/ci.yml
  config/
    companies.example.toml
    filters.example.toml
  src/jobwatcher/
    __init__.py
    __main__.py          # CLI entry point
    models.py            # Posting dataclass, enums
    sources/
      base.py            # Source interface
      greenhouse.py
      lever.py
      ashby.py
    store.py             # SQLite access
    filters.py           # rule engine
    report.py            # markdown report
    mcp_server.py        # Phase 3
  tests/
    fixtures/            # saved, trimmed JSON responses per source
    test_*.py
  scripts/
    run_daily.bat        # for Task Scheduler
```

### Data model (one table to start)

`postings`
- `id` (text, primary key): `{source}:{company_slug}:{source_job_id}`
- `source` (greenhouse / lever / ashby)
- `company`
- `title`
- `location` (raw text as published)
- `remote` (yes / no / unknown)
- `url`
- `description_text` (HTML stripped)
- `published_at` (if provided)
- `first_seen_at`, `last_seen_at`
- `status` (open / closed, where closed means it disappeared from the board)
- `filter_result` (match / flagged / excluded)
- `filter_reasons` (JSON list of plain-language reasons)

A posting that disappears from a board is marked closed, not deleted.

### Config files

`companies.toml`
```toml
[[company]]
name = "Example Industrial AI Co"
source = "greenhouse"     # greenhouse | lever | ashby
board = "exampleco"       # the company's board identifier on that platform
sector = "industrial AI"  # free text, used for grouping in reports
```

`filters.toml` (structure shown with short example values; the real lists live in the gitignored file)
```toml
[roles]
# Title must contain at least one of these to be considered at all
title_include = ["implementation", "deployment", "field engineer", "test engineer"]
title_exclude = ["intern", "vp ", "account executive"]

[domain]
# Industries and settings, matched in the description or the company's sector
terms = ["manufacturing", "mining", "energy"]

[work]
# Kinds of work, matched in the description
terms = ["feasibility", "commissioning", "data validation"]

[location]
non_us_remote_terms = ["canada", "emea"]
ambiguous_terms = []

[location.tier1]
label = "Preferred"
remote_terms = ["remote"]
us_wide_terms = ["united states"]
state_codes = ["CO"]
place_terms = ["denver"]

[location.tier2]
label = "Acceptable"
place_terms = ["boulder"]

[location.tier3]
label = "Abroad, sponsorship needed"
requires_sponsorship = true
country_terms = ["example country"]
pathway_title_terms = ["engineer"]
pathway_note = "Possible occupation-based work permit route: confirm fit"

[sponsorship]
hard_no_terms = ["unable to sponsor"]
eligibility_terms = ["must be eligible to work in example country"]
positive_terms = ["visa sponsorship"]

[description]
flag_terms = ["workshop", "webinar", "train the trainer"]

[travel]
max_percent = 40          # above this: excluded; not stated: flagged
```

All term matching is case-insensitive and on whole words or phrases, so a short term does not match inside a longer word.

### Filter outcomes

Location is handled in preference tiers rather than a single allow list. Each posting's location field is matched against the tiers; a posting listing several locations gets its best tier. Remote roles listed across the whole country count as remote, remote roles tied to another country are excluded, and location text that can't be placed with confidence is flagged rather than guessed. A tier can be marked as requiring visa sponsorship. Postings in that tier are checked against sponsorship terms: a clear "no sponsorship" statement excludes the posting, a clear offer of sponsorship allows it, and silence flags it for a human look. A sponsorship tier can also list pathway title terms for occupations that may qualify for a simpler, occupation-based work permit. Postings whose titles match get a note explaining the possible route, sort to the top of Flagged, and have eligibility-only wording ("must be eligible to work in...") flagged instead of excluded, since the occupation-based permit may satisfy it. Explicit refusals still exclude. A short code that could mean two places (for example "CA" for California or Canada) is treated as ambiguous unless other text settles it. When a location includes a state or province code, that code decides the tier, so a city name shared across states (for example Salem, MA versus Salem, OR) is not mismatched.

Role titles and the fields a candidate fits are kept separate on purpose. Many good-fit roles have generic titles (for example "Project Engineer" or "Solutions Manager"), and what makes them a fit is the industry and the work described, not the title.

- **Match**: a role title term hit, plus at least one domain or work term in the description, location acceptable, travel within the limit, no flag terms. Matches are sorted by how many distinct domain and work terms they hit, so the strongest overlap appears first.
- **Flagged**: would be a Match, but something needs a human look (travel not stated, a flag term found, location unclear). Every flag lists its reason.
- **Possible**: a role title term hit, but no domain or work terms found. Shown in the report as a short list of titles and links only, since generic postings can still be worth a glance.
- **Excluded**: title exclude hit, no role title term, location matching no tier (or remote tied to another country), or travel above the limit. Reasons stored, shown as a count with details available.

## 5. Phases

Each numbered item below is one GitHub Issue. Each phase ends with a tagged release.

**Story points** use a Fibonacci scale (1, 2, 3, 5, 8) and are relative estimates of effort and uncertainty, not hours. They assume Claude Code does most of the coding, with the owner reviewing, testing, and approving every change. Items that are mostly the owner's own time (writing the profile summary, running the tool for a week, reviewing results) are pointed for that effort too. Re-estimate during sprint planning once real velocity is known. Total across all phases and the checkpoint: 108 points.

### Phase 0 - Repo setup (v0.1.0)

Phase estimate: 7 points.

0.1 [2 pts] Create repo, `pyproject.toml`, package skeleton, `.gitignore` including every path in CLAUDE.md's "stay out of git" list
0.2 [3 pts] CI workflow running pytest, ruff check, ruff format --check, and mypy on every PR and push to main. Confirm CI fails on a deliberately broken test, then revert.
0.3 [1 pt] Issue and PR templates matching the Workhorse repo
0.4 [1 pt] README: what it does, what it doesn't do, how to set up config from the example files

Done when: an empty package passes CI and a deliberate failure is shown to be caught.

### Phase 1 - Fetch and store (v0.2.0)

Phase estimate: 23 points.

1.1 [2 pts] **Verify endpoints first.** Before writing any fetcher, confirm each platform's current public job board endpoint and response shape from its official documentation, and record the confirmed URLs and field names in `docs/sources.md`. Do not rely on remembered endpoints.
1.2 [2 pts] `Posting` model and `Source` interface
1.3 [3 pts] Greenhouse fetcher, with fixture-based tests
1.4 [2 pts] Lever fetcher, with fixture-based tests
1.5 [2 pts] Ashby fetcher, with fixture-based tests
1.6 [5 pts] SQLite store: create schema, insert new, update `last_seen_at`, mark disappeared postings closed
1.7 [2 pts] CLI: `python -m jobwatcher fetch` runs all companies and prints a per-company summary (fetched count, new count, errors)
1.8 [5 pts] Error handling: timeouts, HTTP errors, unexpected response shape, and "zero jobs where there used to be some" all reported, and one company failing never stops the others

Done when: running fetch twice in a row reports new postings the first time and zero new the second, and a deliberately wrong board name shows up as an error in the summary.

### Phase 2 - Filter, report, schedule (v0.3.0)

Phase estimate: 29 points.

2.1 [5 pts] Rule engine reading `filters.toml`, returning outcome, overlap score (count of distinct domain and work terms hit), and reasons. Reasons name the specific terms that matched, so the owner can see why a posting landed where it did.
2.2 [3 pts] Tests for each rule: match, no match, can't tell
2.3 [8 pts] Travel and sponsorship detection: find stated travel percentages or phrases, and sponsorship statements for tiers that require it. When unclear, flag. Sponsorship tests cover a clear no, a clear yes, silence, and eligibility-only wording for both pathway and non-pathway titles. Include tests built from realistic phrasing ("up to 25% travel", "travel as needed", "occasional travel").
2.4 [3 pts] Markdown report: run summary and source errors at the top, then new Matches grouped by location tier (tier 1 first) and sorted by overlap score within each tier, with matched terms and location shown, then new Flagged with reasons, then Possible as a compact title-and-link list, then Excluded count
2.5 [1 pt] `python -m jobwatcher run` = fetch + filter + report
2.6 [3 pts] Once-per-day logic in the program itself, so it works with a laptop that is shut or offline at unpredictable times:
   - Record the date of the last successful run. If today's run has already succeeded, exit quietly.
   - Before fetching, check whether any source is reachable. If none are (offline), log "offline, will retry" and exit without counting it as today's run.
   - If some sources fail but others succeed, count it as today's run and list the failed sources in the report, per the no-silent-failures rule.
   - Tests for each case: already ran today, offline, partial failure, full success.
2.7 [2 pts] `scripts/run_daily.bat` and written Windows Task Scheduler setup steps in the README. The task is triggered at logon and every hour, with "start only if a network connection is available" and "run as soon as possible after a scheduled start is missed" turned on. It does not wake the laptop. The frequent triggers just give the program chances to run, and the logic in 2.6 makes sure only one real run happens per day.
2.8 [3 pts] Run it daily for one week and tune rules with the owner. Log every rule change and the reason in `docs/decisions.md`.
2.9 [1 pt] UI review: at the end of the week, the owner notes which steps felt clumsy (reading reports, reviewing excluded postings, approving candidates, tracking what was done with a posting). Record the decision in `docs/decisions.md`: no UI, a single-file HTML report (no server, read-only), or a small local UI phase scoped to the specific clumsy step.

Done when: a week of normal laptop use (shut and offline at irregular times) produces exactly one report per day the laptop was online, and the reports are ones the owner finds useful, with rule changes logged.

### Checkpoint - Aggregator API test (after the v0.3.0 release, before Phase 3)

Checkpoint estimate: 3 points (one issue covering the throwaway script and the owner's evaluation).

**Why:** Greenhouse, Lever, and Ashby cover mostly startups and mid-size companies. Large employers (Caterpillar, Komatsu, Sandvik, and similar) mostly use Workday or other systems that can't be pulled from directly. A job aggregator API is the only allowed route found so far that might provide full descriptions for those companies. Some aggregators return only shortened descriptions, which would break the travel and training filters, so this is tested before anything is built.

**When:** As soon as the v0.3.0 release is tagged. Claude Code stops here and reminds the owner before starting any Phase 3 work.

**How (about one afternoon):**
1. The owner picks one or two aggregators to test (Adzuna is one candidate), reads their terms of use and free-tier limits, and signs up for an API key personally. The key goes in `.env`, which is gitignored.
2. Claude Code writes a throwaway script in `spikes/aggregator_test/` (not part of the main package, no CI requirement) that pulls 20 to 30 postings matching the owner's title keywords, including searches for 3 to 5 large target companies.
3. The script saves results to a CSV with: company, title, posting date, date first returned by the aggregator, description length, and link.
4. The owner checks the results against four questions and records the answers in `docs/aggregator-test.md`:
   - **Complete descriptions?** Open 5 postings on the company's own site and compare against the aggregator text, especially whether travel and duties sections are included.
   - **Big companies covered?** Do known open roles at the large target companies appear?
   - **How delayed?** Days between the original posting date and the aggregator returning it.
   - **Terms allow this use?** Personal, low-volume, daily use within the free tier.

**Outcomes:**
- **Passes all four:** add an aggregator phase to this plan (new source, same Posting format, same filters, duplicate detection against Greenhouse/Lever/Ashby postings). Employers found through the aggregator that are not on the company list are also collected into a "new company candidates" section of the daily report, feeding Phase 5.
- **Fails on complete descriptions or terms:** record the result, keep large employers as manual checks, and move on to Phase 3.
- **Partial pass:** The owner decides, with the tradeoff written into `docs/decisions.md`.

The owner can choose to defer this checkpoint, but it is recorded as deferred in `docs/decisions.md` rather than skipped silently.

### Phase 3 - MCP server (v0.4.0)

Phase estimate: 15 points.

Goal: let Claude query the database through tools, read-only.

3.1 [2 pts] Read the current Python MCP SDK documentation and record the setup used in `docs/mcp.md`
3.2 [5 pts] Server with read-only tools:
   - `get_new_postings(since_days)` - postings first seen in the last N days, with filter outcome and reasons
   - `search_postings(query, outcome, company)` - keyword search over title and description
   - `get_posting(id)` - full detail for one posting
   - `get_run_summary()` - last run's counts and source errors
3.3 [3 pts] Tests for each tool function, independent of the MCP transport
3.4 [3 pts] Connect to Claude Desktop and to Claude Code, document both in the README
3.5 [2 pts] Manual test script: a list of questions to ask Claude through the tools, with expected behavior, run and recorded

No tool in this phase writes to the database or reaches the network.

Done when: Claude, connected through the server, correctly answers "what new implementation roles were posted this week, and which were flagged for travel?"

### Phase 4 - Claude fit review (v0.5.0)

Phase estimate: 13 points.

Goal: Claude ranks new postings against the owner's background and states the main gap for each one. Recommendation only.

4.1 [3 pts] Create `profile/profile_summary.md` (gitignored), a short, accurate summary the owner writes or approves from their own career notes: target roles, hard filters, real strengths, known gaps. The file name stays fixed so tools never need reconfiguring. The top of the file carries a short header:
   ```
   source_version: <version of the career notes this was built from, e.g. v16>
   last_reviewed: <YYYY-MM-DD>
   ```
   Both fields are updated by hand whenever the summary is compared against a newer version of the career notes, even if nothing in the summary changes.
4.2 [2 pts] Add a read-only MCP tool `get_profile_summary()` that returns the summary plus its header fields. If the header is missing, or `last_reviewed` is more than 60 days old, the tool returns a plain warning alongside the summary, and the fit review states that warning at the top of its output.
4.3 [5 pts] Write a reusable fit-review prompt (or Claude Code skill) that tells Claude to:
   - rate each new Match and Flagged posting as Strong fit / Moderate fit / Stretch / Skip (named differently from the Possible filter tier to avoid confusion)
   - give one line on why and one line on the biggest gap, stated plainly
   - never overstate the owner's experience; treat the profile summary's gaps as real
   - end with a shortlist, not with any action
4.4 [3 pts] Run the review on two weeks of real output and compare against the owner's own judgment. Record the disagreements and adjust the prompt.

Done when: the owner agrees with most of the ratings, and the disagreements are understood and logged.

### Phase 5 - Company discovery (v0.6.0)

Phase estimate: 18 points.

Goal: find companies that fit the owner's background and are not yet on the list, and propose them for approval. Nothing is added automatically.

5.1 [8 pts] Write a Claude Code skill (`discover-companies`) intended to run about once a month. Using web search and the profile summary, it:
   - searches for companies in the owner's target categories (defined in a gitignored `config/discovery.toml`, with a committed example file)
   - uses companies whose postings were rated Strong fit in Phase 4 as examples, and looks for similar companies
   - skips companies already on the list or already rejected
   - checks each candidate's careers page to identify its job board platform (Greenhouse, Lever, Ashby, or other)
5.2 [2 pts] Output goes to `reports/company-candidates-YYYY-MM.md`, one entry per company: name, category, one line on why it fits, one line on any concern, job board platform, and careers page link. Companies on unsupported platforms (Workday and similar) are still listed, clearly marked, so the owner can decide whether to track them by hand.
5.3 [5 pts] Approval step: the owner marks each candidate approve or reject. Approved companies on supported platforms are added to `companies.toml`. Rejected companies go to a gitignored `config/rejected_companies.toml` with a short reason, so they are not proposed again.
5.4 [1 pt] Add a read-only MCP tool `get_company_list()` so the skill and fit review can see which companies are already tracked or rejected.
5.5 [2 pts] Run discovery twice, one month apart, and record in `docs/decisions.md` how many proposals were approved and why others were rejected. Adjust categories and the skill prompt from what is learned.

Done when: two monthly runs have produced proposals the owner found worth reviewing, with approvals added to the list and rejections recorded.

## 6. Risks and how they are handled

| Risk | Handling |
|---|---|
| Endpoint or response format changes | Fixtures plus a per-source shape check; a changed shape is reported as a source error |
| Silent coverage loss (company moves to Workday) | "Zero jobs where there used to be some" is reported in every run |
| Filters too tight, good roles missed | Flagged tier exists for exactly this; excluded postings are stored with reasons and can be reviewed |
| Personal data committed to public repo | `.gitignore` set up in Phase 0 before any personal file exists; example configs only |
| Over-trusting the fit review | Phase 4 compares Claude's ratings to the owner's own; final decisions always stay with the owner |
| Discovery fills the list with poor-fit companies | Every proposal needs owner approval; rejections are recorded with reasons and never re-proposed |

## 7. Owner decisions

Resolved (October 2026):
1. Repo visibility: public, with all personal files gitignored
2. Repo name: `job-watcher`
3. Starting filter rules: reviewed; real rules live in the gitignored `config/filters.toml`, to be tuned during the Phase 2 week of real use
4. Report location: the repo's gitignored `reports/` folder

Still open:
5. Starting company list: in progress alongside Phase 0. Needed before issue 1.7 (first full fetch run). Each company's job board platform must be confirmed when it is added.
