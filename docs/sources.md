# Job board sources

Confirmed from each platform's official documentation on **2026-10-03** (issue 1.1). Fetchers in issues 1.3 to 1.5 are built against what is written here.

Anything marked **Not documented** was not stated in the official docs. Do not assume it. Each fetcher issue must check those points against a real response when capturing its test fixture, then update this file with what it found.

All three endpoints are public read-only GETs with no API key. Each returns every published job on a board in one response, which is how the "one request per company per run" rule is met.

## Summary

| | Greenhouse | Lever | Ashby |
|---|---|---|---|
| List endpoint | `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true` | `GET https://api.lever.co/v0/postings/{site}?mode=json` | `GET https://api.ashbyhq.com/posting-api/job-board/{job_board_name}` |
| Auth for GET | None | None | Not documented (example uses none) |
| Board identifier | `board_token` | `site` | `job_board_name` |
| Stable job ID | `id` | `id` | **Not documented** (see below) |
| Description | HTML, entity-escaped (`content`) | HTML and plain text | HTML and plain text |
| Remote signal | None (location text only) | `workplaceType` | `isRemote`, `workplaceType` |
| Posted date | Not in list response | Not documented | `publishedAt` |
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
- `company_name` and `first_published` are documented **only on the single-job endpoint**, not the list. Fetching each job individually would break the one-request-per-company rule, so `published_at` will be empty for Greenhouse and `first_seen_at` is the reliable date. `updated_at` changes on edits, so it is not a posting date.
- There's no remote field. Remote status has to come from `location.name` text, so `remote` will often be `unknown`.
- `meta.total` can be compared against `len(jobs)` as a cheap shape check.

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

**Response shape:** **Not documented.** The README only says "Jobs list as raw JSON." Issue 1.4 must confirm whether it's a bare array or a wrapper object.

**Posting fields**
- `id`: posting ID. Use this as `source_job_id`.
- `text`: posting title
- `categories`: `location`, `commitment`, `team`, `department`, `allLocations`. The primary location is also in `allLocations`.
- `country`: ISO 3166-1 alpha-2 code, or null
- `workplaceType`: `unspecified`, `on-site`, `remote`, or `hybrid`
- `description` / `descriptionPlain`: opening plus body, as HTML or plain text
- `descriptionBody` / `descriptionBodyPlain`, `opening` / `openingPlain`
- `lists[]`: `{ "text": name, "content": unstyled HTML }`. These are the requirements and responsibilities sections, **which are not part of `description`**.
- `additional` / `additionalPlain`: closing section, may be empty
- `hostedUrl`: public posting URL. `applyUrl` is the application form.
- `salaryRange` (`currency`, `interval`, `min`, `max`) and `salaryDescription` / `salaryDescriptionPlain`, all optional

**Points to handle**
- Full description text = `descriptionPlain` + each `lists[]` entry + `additionalPlain`. Travel and duty wording often sits in `lists`, so using `descriptionPlain` alone would hide it from the filters.
- **Pagination:** no default or maximum page size is documented. Issue 1.4 must confirm that one request without `limit` returns every posting. If a response can be silently truncated, it must be detected and reported, following the no-silent-failures rule.
- **No posted date is documented.** `createdAt` does not appear in the README. Issue 1.4 checks the real response, and until then `published_at` is empty for Lever.
- `country` plus `allLocations` help with location tiers and with spotting remote roles tied to another country.

## Ashby

**Official docs:** https://developers.ashbyhq.com/docs/public-job-posting-api (page last updated 2026-05-26)

**Endpoint**
- `GET https://api.ashbyhq.com/posting-api/job-board/{job_board_name}`
- Authentication is **not documented**. The docs example uses none. Issue 1.5 confirms this with a real request.

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
- **No job ID is documented.** The example job object has no `id` field, but the planned posting key is `{source}:{company_slug}:{source_job_id}`. Issue 1.5 must check the real response:
  - If an `id` field is present, use it, and note here that it's undocumented.
  - If not, derive the ID from the last path segment of `jobUrl`, and record that choice in `docs/decisions.md`.

  Either way, the fetcher must fail loudly if it can't get an ID. It must never fall back to the title, because two postings can share a title.
- Skip jobs with `isListed: false`. They're hidden from the company's own board.
- `employmentType: "Intern"` is a structured signal that can back up the title exclude rules.
- Region and country come as structured fields here, which is more reliable than Greenhouse's free text for location tiers.

## Mapping to the planned `Posting` model

| Posting field | Greenhouse | Lever | Ashby |
|---|---|---|---|
| `source_job_id` | `id` | `id` | Not documented (see Ashby) |
| `company` | from `companies.toml` | from `companies.toml` | from `companies.toml` |
| `title` | `title` | `text` | `title` |
| `location` | `location.name` | `categories.location` (+ `allLocations`) | `location` (+ `secondaryLocations`) |
| `remote` | `unknown` unless location text says so | from `workplaceType` | from `isRemote` / `workplaceType` |
| `url` | `absolute_url` | `hostedUrl` | `jobUrl` |
| `description_text` | `content`, unescaped then stripped | `descriptionPlain` + `lists` + `additionalPlain` | `descriptionPlain` |
| `published_at` | empty (only on per-job endpoint) | empty unless 1.4 finds a date | `publishedAt` |

`company` comes from our own config for all three platforms. Only Greenhouse's per-job endpoint returns a company name, and it's not needed.

## Usage terms and politeness

None of the three official docs state a rate limit for these GET endpoints. The only documented limit is Lever's, and it applies to application POSTs, which we never send. Lever's README notes that published postings "may be scraped by third parties." Neither Greenhouse's nor Ashby's docs say anything about usage terms for these endpoints.

Even without documented limits, the CLAUDE.md rules still apply:
- one request per company per run
- a clear user agent
- timeouts
- a daily cadence
