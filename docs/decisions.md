# Decisions

Owner decisions that change the plan, the rules, or how the tool behaves, newest first. Each entry says what was decided, why, and what it affects. Filter rule changes during Phase 2 tuning are logged here too (issue 2.8).

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
