# Decisions

Owner decisions that change the plan, the rules, or how the tool behaves, newest first. Each entry says what was decided, why, and what it affects. Filter rule changes during Phase 2 tuning are logged here too (issue 2.8). Because this file is public, rule changes are described in general terms; the specific terms and places live only in the gitignored `config/filters.toml` (design principle 5).

## 2026-10-06: Fourth location tier removed

**Decision:** The owner removed the fourth, lowest-priority location tier added on 2026-10-04. Its place names and region labels went back on the non-US exclusion list, and the discovery searches for those countries were dropped.

**Why:** Roles there wouldn't be pursued, so listing them cost review time and cluttered the report.

**Result on the same 136 open postings:** 7 postings moved from Flagged to Excluded (1 Match / 9 Flagged / 126 Excluded after the change). Discovery now uses 66 Adzuna calls per run instead of 138.

**Affects:** `config/filters.toml` and `config/discovery.toml` only (private). No code.

## 2026-10-04: Discovery drops employers whose ads name no industry term

**Decision:** Discovery drops any employer whose ad titles, snippets, and Adzuna categories contain none of the owner's industry terms. The setting is `require_industry_term`, on by default and in the owner's config. The number dropped is still reported on every run, so the cut is visible.

**Why:** The first real run (138 calls) found 1,366 new employers, and 1,060 of them showed no industry term at all. The owner judged that a relevant ad names its industry within its first 500 characters, so those employers are clutter rather than missed opportunities. This supersedes the original "rank, don't drop" design in issue 2b.2.

**Tradeoff:** An employer in a target industry whose snippets never name it will be missed. That's accepted for a much shorter list. Turning the setting off restores the old behavior, with those employers ranked last.

**Affects:** `src/jobwatcher/discovery.py`, `config/discovery.example.toml`, and the owner's private `config/discovery.toml`.

## 2026-10-04: Discovery feed added (Phase 2b), using Adzuna for discovery only

**Decision:** Add a weekly discovery feed after issue 2.7, before the tuning week. The plan is now v13. It searches the Adzuna API with the owner's role and industry terms, groups results by employer, detects each new employer's job board, and lists candidates in a discovery report for the owner to approve or reject. Phase 5 (Claude-assisted discovery) reuses its board detection and approval step.

**Why:**
- **Where the owner's time goes.** Finding companies and roles they haven't heard of is where most of the owner's search time goes. The daily run only watches companies already on the list.
- **Snippets are fine for discovery.** The aggregator test ruled Adzuna out for *judging* postings, because its descriptions are shortened. Discovery only needs to learn that an unfamiliar company is hiring for a matching role. Approved companies are then read in full from their own Greenhouse, Lever, or Ashby board and filtered normally. Adzuna data is never used for filtering decisions.

**Adzuna terms review (owner, 2026-10-04):**
- **Permitted use.** The terms permit "personal research", and a personal job search fits.
- **The aggregation restriction doesn't apply.** The clause against using data "in aggregation … to deliver any ongoing work or research … without written consent" belongs to a paragraph about commercial, government, or academic organisations on a 14-day trial, which doesn't describe this use.
- **Attribution.** Published Adzuna data must credit "The Adzuna API", so discovery reports will carry the credit even though they're private.
- **Limits.** Default limits are 250 calls per day and 2,500 per month, far above a weekly run.
- **Caveat.** "Personal research" isn't defined in the terms. Revisit if the tool is ever used for anything other than the owner's own search.

**Design constraint from the terms:** the tool never follows Adzuna's job links to find an employer's board. They're Adzuna's paid click-throughs, and automated clicks would misuse them.

**Affects:** `PROJECT_PLAN.md` (Phase 2b added, Phase 5 trimmed) and `CLAUDE.md` (Adzuna approved for discovery only).

## 2026-10-04: Filter rules tuned after the first real run

**Decision:** After reviewing how the rule engine sorted the first 136 real postings, the owner:
- added several title terms for roles that were being missed
- removed two title terms made redundant by singular/plural matching
- added a fourth, lowest-priority location tier for remote and on-site jobs in a group of countries, with sponsorship required. Some place names and region labels moved from the non-US exclusion list into that tier. (Removed again on 2026-10-06; see above.)

**Why:** The first pass excluded relevant titles over wording differences, and the owner wants a wider net abroad at the lowest priority rather than an outright exclusion.

**Result on the same 136 postings:** 4 Match / 1 Flagged / 131 Excluded became 13 Match / 5 Flagged / 118 Excluded.

**Known limitation:** a tier's country names are matched anywhere in the location text, so a place whose name contains a listed country's name (such as a region or town named after a country) lands in that country's tier. This is rare, so it's accepted rather than handled in the engine.

**Affects:** `config/filters.toml` only (private). No code.

## 2026-10-04: Terms match singular and plural forms

**Decision:** The last word of every filter term also matches its regular singular or plural: solution/solutions, deployment/deployments, process/processes, utility/utilities. Words shorter than 4 letters stay exact.

**Why:** Strict whole-word matching missed real titles over a single "s" ("field service" vs "Field Services Engineer", "solutions" vs "Solution Architect"). Only the last word is inflected, because that's how English pluralizes a phrase. Short terms are mostly codes and abbreviations used by the location rules ("us", "ca", "uk", "vp"), where an added or dropped "s" changes the meaning: "us" must never match "u" or "uss".

**Affects:** `src/jobwatcher/matching.py` (#37). Irregular plurals and other word forms ("deployed" vs "deploys") still need their own terms.

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
