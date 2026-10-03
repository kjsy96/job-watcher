# Test fixtures

Saved responses from real public job board endpoints, so tests never hit the network. Each was captured once with a single request, then trimmed. They are used only as test input.

| File | Source | Captured | Trimming |
|---|---|---|---|
| `greenhouse_jobs.json` | `GET https://boards-api.greenhouse.io/v1/boards/gitlab/jobs?content=true` | 2026-10-03 | Kept 4 of 211 jobs, chosen to cover different location formats. Each `content` value is cut to its opening HTML blocks at a block boundary, keeping the original escaping. `meta.total` set to 4. All other fields are as returned. |

The boards used here are well-known public boards picked for coverage of real-world formats. They say nothing about which companies this tool tracks.

To refresh a fixture, make one request with the project's user agent, then trim it the same way. Update the date above and any test that depends on specific values.
