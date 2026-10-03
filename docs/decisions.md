# Decisions

Owner decisions that change the plan, the rules, or how the tool behaves, newest first. Each entry says what was decided, why, and what it affects. Filter rule changes during Phase 2 tuning are logged here too (issue 2.8).

## 2026-10-03: Aggregator API test result: Adzuna fails, large employers stay manual

**Decision:** Accept the documented result for Adzuna (it fails on complete descriptions), keep large target employers as manual checks, and start Phase 2. No API key was requested.

**Why:** Adzuna's official docs say the API returns only a snippet of each job description. The plan's outcome rule for "fails on complete descriptions" is to record it, keep large employers as manual checks, and move on. A title-and-link feed was considered and set aside as a separate feature. Details are in `docs/aggregator-test.md`.

**Affects:** No code. Phase 2 is designed for full descriptions from Greenhouse, Lever, and Ashby only.

## 2026-10-03: A board that suddenly returns 0 jobs is a warning, not recorded

**Decision:** When a board answers successfully with 0 jobs while the store still has open postings for it, the fetch shows a WARNING for that company and does **not** record the result. Its postings stay open, and the run exits 1. A board with 0 jobs and nothing open, such as a newly added company with no openings, is just OK.

**Why:** Design principle 3: a broken source must never look like "no new jobs." A board going from some jobs to none at once usually means something changed: the company moved to an unsupported platform, renamed its board, or the API had a glitch. It rarely means every job closed the same day. Recording it would close every posting and look like a quiet day.

**Tradeoff:** If a company really does close every opening, the warning repeats each run until it posts again or is removed from `config/companies.toml`. The alternative was to record it (closing everything) and warn, which loses nothing if it was real. But if it was a glitch, the postings would come back as "reopened" and the history would be muddied.

**Affects:** `src/jobwatcher/fetch.py` (issue 1.8).

**Status:** confirmed by the owner on 2026-10-03, after reviewing the alternative.

## 2026-10-03: Aggregator API test moved to after Phase 1

**Decision:** Run the "Checkpoint - Aggregator API test" right after the v0.2.0 release (end of Phase 1) and before Phase 2. It was previously after v0.3.0 (end of Phase 2).

**Why:**
- **Most targets are unreadable today.** While building the starting company list for issue 1.7, 11 of the 17 target companies named turned out to be on platforms this tool can't read: SAP SuccessFactors, Workday, Dayforce, Paycor, UKG, HiBob, or self-hosted job pages. Most of the large employers and manufacturers on the list are in that group. Whether an aggregator can cover them decides how much of the target list the tool reaches at all, so it should be known before building filters, reports, and scheduling.
- **The result shapes Phase 2.** If an aggregator passes but returns shortened descriptions, the travel and sponsorship filters need a "description incomplete, so flag" rule. Knowing that before Phase 2 avoids reworking the filters.
- **Why after Phase 1 and not immediately.** An aggregator source would plug into the `Posting` model, the store, and the `fetch` command. Issues 1.7 and 1.8 finish that pipeline, so the test runs against its real shape.

**Affects:** `PROJECT_PLAN.md` (v12): the checkpoint section moves and now leads into Phase 2. The `CLAUDE.md` checkpoint rule now triggers at v0.2.0. The steps of the test itself are unchanged.

## 2026-10-03: Workable not added as a source

**Decision:** Don't add Workable as a fourth source for now.

**Why:** Workable's official developer API (`workable.readme.io`) needs each company's own access token. The public job-list feed that third-party tools use is the internal feed behind Workable's embeddable careers widget. Workable doesn't document it as an API for outside use, and only scraper vendors describe it. Greenhouse, Lever, and Ashby each document a public API meant for outside use. Design principle 4 limits this tool to public job board endpoints, and none of the current target companies use Workable.

**Revisit if:** several target companies turn out to use Workable, or Workable documents a public job feed. HiBob was found to be in the same position and was set aside for the same reason.

## 2026-10-03: Hybrid roles map to remote = no

**Decision:** In the Lever and Ashby fetchers, a `hybrid` workplace type gives `remote = no`, not `unknown`.

**Why:** Hybrid means regular office attendance, so the role is tied to a location. The location tiers in Phase 2 then judge it on its location.

**Affects:** `src/jobwatcher/sources/lever.py`, `src/jobwatcher/sources/ashby.py` (issues 1.4 and 1.5).
