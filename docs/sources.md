# Job board sources

Confirmed from each platform's official documentation on **2026-10-03** (issue 1.1). Fetchers in issues 1.3 to 1.5 are built against what is written here.

Anything marked **Not documented** was not stated in the official docs. Do not assume it. Each fetcher issue must check those points against a real response when capturing its test fixture, then update this file with what it found.

All three endpoints are public read-only GETs with no API key. Each returns every published job on a board in one response, which is how the "one request per company per run" rule is met.

## Summary

| | Greenhouse | Lever | Ashby |
|---|---|---|---|
| List endpoint | `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true` | `GET https://api.lever.co/v0/postings/{site}?mode=json` | `GET https://api.ashbyhq.com/posting-api/job-board/{job_board_name}` |
| Auth for GET | None | None | None (undocumented; confirmed by a real request) |
| Board identifier | `board_token` | `site` | `job_board_name` |
| Stable job ID | `id` | `id` | `id` (undocumented, present in practice) |
| Description | HTML, entity-escaped (`content`) | HTML and plain text | HTML and plain text |
| Remote signal | None (location text only) | `workplaceType` (real value `onsite`, docs say `on-site`) | `isRemote`, `workplaceType` |
| Posted date | `first_published` (undocumented in list, present in practice) | `createdAt`, ms since 1970 (undocumented, present in practice) | `publishedAt` |
| Rate limits for GET | Not documented | Not documented | Not documented |

## Greenhouse

**Official docs:** https://docs.greenhouse.io/job-board.html. The old address, `developers.greenhouse.io/job-board.html`, now redirects there.

**Endpoints**
- List jobs: `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs`
- Single job: `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}`
- The docs say "Job Board data is publicly available, so authentication is not required for any GET endpoints."

**Board token:** the path segment after the domain in the company's hosted board URL, `https://boards.greenhouse.io/{board_token}`.

**Query parameters (list)**
- `content=true` includes the full description, departments, and offices. **Required for us.** Without it there is no description to filter on.

**Response shape**
```json
{ "jobs": [ { ... } ], "meta": { "total": 123 } }
```

**Job fields in the list response (with `content=true`)**
- `id`: job post ID. Use this as `source_job_id`.
- `internal_job_id`, `requisition_id`
- `title`
- `updated_at`
- `location.name`: free-text location
- `absolute_url`: public posting URL
- `language`, `metadata`
- `content`: description as HTML
- `departments[]`: `id`, `name`, `parent_id`, `child_ids`
- `offices[]`: `id`, `name`, `location`, `parent_id`, `child_ids`

**Points to handle**
- `content` is HTML with **entities escaped**. The docs say editor HTML "will be automatically converted into corresponding HTML entities". It must be unescaped *before* stripping tags, or the stored text will contain literal `&lt;p&gt;`.
- `company_name` and `first_published` are documented **only on the single-job endpoint**, not the list. `updated_at` changes on edits, so it is not a posting date.
- There's no remote field. Remote status has to come from `location.name` text, so `remote` will often be `unknown`.
- `meta.total` can be compared against `len(jobs)` as a cheap shape check.

**Checked against a real response (issue 1.3, 2026-10-03, GitLab's board, 211 jobs)**
- **The list response does include `first_published` and `company_name`**, on all 211 jobs, even though the docs only show them on the single-job endpoint. The fetcher uses `first_published` for `published_at`. Because the field is undocumented here, its absence gives `None` and is not an error. A value that's present but unreadable is reported as a shape error.
- Other undocumented fields also appear: `data_compliance`, `application_deadline`, `ai_disclaimer`, `include_ai_disclaimer`, `ai_opt_out_request_url`. The fetcher doesn't use them.
- `id` is an integer, on all jobs.
- `absolute_url` uses the `job-boards.greenhouse.io` domain, not `boards.greenhouse.io`. The fetcher stores whatever URL is returned.
- `content` is entity-escaped on every job, and sometimes **double**-escaped (`&amp;nbsp;`, `&amp;amp;`). One `html.unescape` gives real HTML, and the HTML parser then decodes the entities left in the text.
- Titles can carry trailing whitespace, so the fetcher strips them.
- `meta.total` matched `len(jobs)`.
- Location formats seen: `Remote`, `Remote, United States`, `Remote, US`, `Remote, Canada; Remote, United States` (several locations in one string), and `Bangalore, India`.

## Lever

**Official docs:** https://github.com/lever/postings-api (README)

**Endpoints**
- List postings: `GET https://api.lever.co/v0/postings/{site}?mode=json`
- Single posting: `GET https://api.lever.co/v0/postings/{site}/{posting_id}`
- **EU instance:** `https://api.eu.lever.co/v0/postings/`. Companies hosted on Lever's EU instance are only reachable there, so a company entry may need a region setting.
- No auth for GET. An API key is only needed to POST applications, which this project never does.

**Site:** the first path segment of the hosted board, `https://jobs.lever.co/{site}`.

**Query parameters (list)**
- `mode=json` returns raw JSON. Other modes are iframe and HTML.
- `skip`, `limit`: pagination. **No default or maximum is documented.**
- `location`, `commitment`, `team`, `department`, `level`: filters. These are case-sensitive and OR-combined. We don't use them, because filtering happens locally.
- `group`: group by location, commitment, or team. We don't use it.

**Response shape:** **Not documented.** The README only says "Jobs list as raw JSON." The real response is a bare array (see below).

**Posting fields**
- `id`: posting ID. Use this as `source_job_id`.
- `text`: posting title
- `categories`: `location`, `commitment`, `team`, `department`, `allLocations`. The primary location is also in `allLocations`.
- `country`: ISO 3166-1 alpha-2 code, or null
- `workplaceType`: `unspecified`, `on-site`, `remote`, or `hybrid` (the real API sends `onsite`, see below)
- `description` / `descriptionPlain`: opening plus body, as HTML or plain text
- `descriptionBody` / `descriptionBodyPlain`, `opening` / `openingPlain`
- `lists[]`: `{ "text": name, "content": unstyled HTML }`. These are the requirements and responsibilities sections, **which are not part of `description`**.
- `additional` / `additionalPlain`: closing section, may be empty
- `hostedUrl`: public posting URL. `applyUrl` is the application form.
- `salaryRange` (`currency`, `interval`, `min`, `max`) and `salaryDescription` / `salaryDescriptionPlain`, all optional

**Points to handle**
- Full description text = `description` + each `lists[]` heading and content + `additional`, all converted from HTML. Travel and duty wording often sits in `lists`, so using `descriptionPlain` alone would hide it from the filters.
- **Pagination:** no default or maximum page size is documented. Checked below.
- **No posted date is documented.** `createdAt` does not appear in the README. The real response has it (see below).
- `country` plus `allLocations` help with location tiers and with spotting remote roles tied to another country.

**Checked against real responses (issue 1.4, 2026-10-03)**

Two requests were made:
- `leverdemo`, Lever's own demo board and the example in its README: 11 postings
- `palantir`, a large public board, used only for the pagination check: 319 postings

Findings:
- **Response shape:** a bare JSON array of postings, with no wrapper object.
- **Posted date:** `createdAt` is present on every posting. It's an **integer in milliseconds since 1970 (UTC)**, and the README doesn't document it. The fetcher uses it for `published_at`. If it's missing the value is `None`; if it's present but not an integer, that's a shape error. It's the posting's creation time, which can be years before a posting reappears on a board, so `first_seen_at` is still the date that matters for "new".
- **`workplaceType` real values are `remote`, `onsite`, `hybrid`, `unspecified`.** The docs spell it `on-site`, but the API sends `onsite`. The fetcher accepts both. A mapping built only from the docs would have quietly made every on-site role `unknown`.
- **Pagination:** one request with no `skip` or `limit` returned all 319 postings on the large board, all with unique IDs. 319 isn't a round page size, so there's no default cap at that scale, and the fetcher makes one request with no paging.
  - **Limitation:** Lever gives no total count, so truncation can't be detected directly the way Greenhouse's `meta.total` allows. A cap above 319 can't be ruled out. Issue 1.8's "zero jobs where there used to be some" check is the backstop for a sudden drop.
- **EU instance:** deferred, and no `Company` region field was added. A company hosted on the EU instance would get an error status from the global endpoint, which is reported as a `SourceError` and never as an empty list. Add a region setting when a real target needs it.
- `descriptionPlain` keeps non-breaking spaces (5 of 11 postings), so the fetcher converts the HTML fields (`description`, `lists[].content`, `additional`) with the same `html_to_text` used for Greenhouse. Stored text is then consistent across sources.
- Some real postings have an empty `description`, with all content in `lists`, or no content at all. Both are valid postings. The empty one is left for the filters to flag.
- `lists[].content` is a run of `<li>` items with no surrounding `<ul>`. Each item still lands on its own line.
- `country` is present (`US`, `GB`, `CA` seen). `salaryRange` appears on some postings only.
- Response headers show no rate-limit headers.

## Ashby

**Official docs:** https://developers.ashbyhq.com/docs/public-job-posting-api (page last updated 2026-05-26)

**Endpoint**
- `GET https://api.ashbyhq.com/posting-api/job-board/{job_board_name}`
- Authentication is **not documented**. The docs example uses none, and a real request confirmed none is needed (see below).

**Job board name:** the last part of the hosted board URL. For example, `https://jobs.ashbyhq.com/Ashby` gives `Ashby`.

**Query parameters**
- `includeCompensation=true` adds compensation data. Not needed for filtering, so it's left off to keep responses small.

**Response shape**
```json
{ "apiVersion": "1", "jobs": [ { ... } ] }
```

**Job fields (from the documented example)**
- `title`
- `location`: primary location, as a string
- `secondaryLocations[]`: `location` (string) and `address`
- `address.postalAddress`: `addressLocality`, `addressRegion`, `addressCountry`
- `department`, `team`
- `isListed`: if false, the job "should only be available via direct link"
- `isRemote` (boolean), `workplaceType` (`OnSite`, `Remote`, or `Hybrid`)
- `descriptionHtml`, `descriptionPlain`
- `publishedAt`: ISO 8601 datetime
- `employmentType`: `FullTime`, `PartTime`, `Intern`, `Contract`, or `Temporary`
- `jobUrl`: public posting URL. `applyUrl` is the application form.

**Points to handle**
- **No job ID is documented.** The example job object has no `id` field, but the planned posting key is `{source}:{company_slug}:{source_job_id}`. The real response has one (see below). The plan before checking was:
  - If an `id` field is present, use it, and note here that it's undocumented.
  - If not, derive the ID from the last path segment of `jobUrl`, and record that choice in `docs/decisions.md`.

  Either way, the fetcher must fail loudly if it can't get an ID. It must never fall back to the title, because two postings can share a title.
- Skip jobs with `isListed: false`. They're hidden from the company's own board.
- `employmentType: "Intern"` is a structured signal that can back up the title exclude rules.
- Region and country come as structured fields here, which is more reliable than Greenhouse's free text for location tiers.

**Checked against a real response (issue 1.5, 2026-10-03, Ashby's own board `Ashby`, 62 jobs)**
- **Job ID: the real response does include `id`**, a UUID string, on all 62 jobs, even though the docs never show it. It's unique, and it equals the last path segment of `jobUrl` on all 62. The fetcher uses `id` and **requires** it, with no fallback. If Ashby ever drops this undocumented field, every Ashby fetch fails loudly, and the fix is a deliberate choice (most likely deriving from `jobUrl`, which gives the same values) rather than a silent switch. Nothing goes in `docs/decisions.md`, since the documented-gap fallback wasn't needed.
- **Authentication:** none needed. A plain GET returned 200.
- **Response headers:** `cache-control: public, max-age=60` and no rate-limit headers.
- **`descriptionHtml` is real HTML, not entity-escaped** (unlike Greenhouse). An `&lt;` in it is a literal `<` in the writing, e.g. "engineers are in &lt;2h meetings". It must **not** be unescaped before parsing, or text like `&lt;team lead&gt;` would turn into a tag and disappear.
- `descriptionPlain` has non-breaking spaces (33 of 62 jobs), so the fetcher converts `descriptionHtml` with the shared `html_to_text`, the same as the other sources.
- Every field in the docs example is present. The `publishedAt` format is consistent: ISO 8601 with milliseconds and an offset, e.g. `2024-03-04T14:29:08.532+00:00`.
- `secondaryLocations` ranges from 0 to 19 entries. The fetcher joins the primary and secondary locations with `; `, skipping duplicates.
- **Limited variety on this board:** all 62 jobs are listed, remote, and full-time, so `isListed: false`, `OnSite`, `Hybrid`, and the other employment types are covered by tests that modify the fixture, not by real examples.
- **`remote` mapping:** `workplaceType` decides first (`Remote` is yes, `OnSite` and `Hybrid` are no). With no usable `workplaceType`, `isRemote: true` is yes, and anything else is `unknown`. `isRemote: false` alone can't tell on-site from hybrid.
- **Personal data:** many real descriptions are written in the first person by the hiring manager, with their name and LinkedIn profile link. The fixture leaves those jobs out.

## Mapping to the planned `Posting` model

| Posting field | Greenhouse | Lever | Ashby |
|---|---|---|---|
| `source_job_id` | `id` | `id` | `id` (undocumented, required) |
| `company` | from `companies.toml` | from `companies.toml` | from `companies.toml` |
| `title` | `title` | `text` | `title` |
| `location` | `location.name` | `categories.location` (+ `allLocations`) | `location` (+ `secondaryLocations`) |
| `remote` | `unknown` unless location text says so | from `workplaceType` | from `isRemote` / `workplaceType` |
| `url` | `absolute_url` | `hostedUrl` | `jobUrl` |
| `description_text` | `content`, unescaped then stripped | `description` + `lists` + `additional`, from HTML | `descriptionPlain` |
| `published_at` | `first_published` if present, else empty | `createdAt` if present, else empty | `publishedAt` |

`company` comes from our own config for all three platforms. Greenhouse also sends `company_name`, but it isn't used, because the name in `companies.toml` is the one the owner chose.

## Usage terms and politeness

None of the three official docs state a rate limit for these GET endpoints. The only documented limit is Lever's, and it applies to application POSTs, which we never send. Lever's README notes that published postings "may be scraped by third parties." Neither Greenhouse's nor Ashby's docs say anything about usage terms for these endpoints.

Even without documented limits, the CLAUDE.md rules still apply:
- one request per company per run
- a clear user agent
- timeouts
- a daily cadence
