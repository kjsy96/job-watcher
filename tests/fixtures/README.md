# Test fixtures

Saved responses from real public job board endpoints, so tests never hit the network. Each was captured once with a single request, then trimmed. They are used only as test input.

| File | Source | Captured | Trimming |
|---|---|---|---|
| `greenhouse_jobs.json` | `GET https://boards-api.greenhouse.io/v1/boards/gitlab/jobs?content=true` | 2026-10-03 | Kept 4 of 211 jobs, chosen to cover different location formats. Each `content` value is cut to its opening HTML blocks at a block boundary, keeping the original escaping. `meta.total` set to 4. All other fields are as returned. |
| `lever_postings.json` | `GET https://api.lever.co/v0/postings/leverdemo?mode=json` (Lever's own demo board, the example in its README) | 2026-10-03 | Kept 5 of 11 postings, chosen to cover each real `workplaceType`, a three-location posting, a posting whose content is only in `lists`, an empty posting, and US/CA/GB countries. Postings that name a person were left out. Fields are as returned. |
| `ashby_jobs.json` | `GET https://api.ashbyhq.com/posting-api/job-board/Ashby` (Ashby's own board, the example in its docs) | 2026-10-03 | Kept 4 of 62 jobs: the only ones whose descriptions don't name a person or link a profile. They're chosen to cover 0, 1, 3, and 11 secondary locations. `descriptionHtml` is cut at a block boundary and `descriptionPlain` is cut to a similar length. After trimming, I checked that no names remained. All other fields are as returned. |

The boards used here are well-known public boards picked for coverage of real-world formats. They say nothing about which companies this tool tracks.

To refresh a fixture, make one request with the project's user agent, then trim it the same way. Update the date above and any test that depends on specific values.
