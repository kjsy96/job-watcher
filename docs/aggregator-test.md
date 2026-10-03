# Aggregator API test

The checkpoint from `PROJECT_PLAN.md`, run after v0.2.0 and before Phase 2. It asks whether a job aggregator API can cover large target employers that use platforms this tool can't read (Workday, SAP SuccessFactors, and similar).

## Adzuna, 2026-10-03

**Result: fails on complete descriptions. Large employers stay as manual checks, and the project moves on to Phase 2.**

The owner chose to accept the result documented in Adzuna's official docs, without signing up for a key. The deciding question is answered by those docs, so testing the other three with a key wouldn't change the outcome.

| Question | Answer | Source |
|---|---|---|
| **Complete descriptions?** | **No.** Adzuna's search docs state: "We currently only provide a snipped of the job description in the response." | [Adzuna search docs](https://developer.adzuna.com/docs/search) |
| **Big companies covered?** | Not tested (no key). The docs only show the UK (`gb`) as an example country and don't confirm US or Canada coverage. | [Adzuna search docs](https://developer.adzuna.com/docs/search) |
| **How delayed?** | Not tested (no key). | |
| **Terms allow this use?** | Probably yes. "Personal research" is a listed permitted use, and research use must "acknowledge Adzuna as the source". Default limits are 25 calls per minute, 250 per day, 1000 per week, and 2500 per month, far more than one daily check needs. | [Adzuna terms of service](https://developer.adzuna.com/docs/terms_of_service) |

**Why shortened descriptions fail the test:** the filters that matter most read the full posting. Travel percentage and sponsorship wording usually sit in the duties or requirements sections near the end. With only a snippet, every aggregator posting would land in Flagged for "can't tell", so the filters would add nothing for those companies.

**Outcome applied:** large target employers on unsupported platforms stay on the owner's check-by-hand list, which is kept in the gitignored `config/companies.toml`. No aggregator phase is added.

**Considered and not chosen:** using Adzuna as a title-and-link feed, with no filtering and every new matching title at a large employer automatically flagged, linked to the full posting. It has the same limitation that ruled out job alert emails (`PROJECT_PLAN.md`, out of scope). It would be a new feature rather than a pass of this test, so it was set aside.

**Revisit if:** an aggregator is found whose official docs promise full descriptions and permit personal use. Check its docs first, the same way, before signing up for anything.
