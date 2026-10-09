# Filter rules

How the rule engine (`src/jobwatcher/filters.py`) decides each posting's outcome, rule by rule. It's written for tuning `config/filters.toml` during the Phase 2 week of real use (issue 2.8). Examples here use made-up places; the real rules live only in the gitignored config.

Every rule is tested for **match**, **no match**, and **can't tell** in `tests/test_rule_matrix.py`. Where a rule has no can't-tell case, the matrix says why.

## Outcomes

| Outcome | Meaning |
|---|---|
| **Match** | The title fits, the description overlaps with the target industries or work, and the location is in a tier. Ranked by overlap score. |
| **Flagged** | Would be a Match, but something needs a human look: unclear location, travel, or sponsorship, a description flag term, or a title flag term next to a role term. The reason to look is listed first. |
| **Possible** | The title fits, but no domain or work term was found. Shown as a short list, since generic postings can still be worth a glance. |
| **Excluded** | Fails a rule. Every reason is recorded, not just the first, so excluded postings can be reviewed. |

Order of checks: **Excluded** beats everything. Then **Possible** (no overlap), then **Flagged** (needs a look), then **Match**.

**Overlap score** = distinct domain terms + distinct work terms found. A term counts once however often it appears, and a term in both lists counts once.

## How terms match

- **Case doesn't matter.** "MINING" matches `mining`.
- **Whole words only.** `mine` does not match "determine", and `us` does not match "business".
- **Phrases match across line breaks.** `data validation` matches "data" at the end of one line and "validation" at the start of the next.
- **Singular and plural both match** for the last word of a term: `solutions` matches "Solution Architect", and `field service` matches "Field Services". Words shorter than 4 letters stay exact, so codes like `us` and `ca` never stretch to "u" or "cas". Other word forms are separate: `deployed` doesn't match "deploys".
- **Spaces around a term are ignored.** `"vp "` is the same as `vp`, and still won't match "VPN".

## Title rules

| Rule | Config | Match | No match | Can't tell |
|---|---|---|---|---|
| Role title | `[roles] title_include` | Title has a role term, so the posting is considered | **Excluded**: "title matches no role term" | n/a: every posting has a title |
| Title exclude | `[roles] title_exclude` | **Excluded**: "title contains excluded term(s)" | Not excluded | n/a: a word is in the title or it isn't |
| Title flag | `[roles] title_flag_terms` | With no role term: **Excluded**, and the reason names the term ("title matches no role term (and mentions ...)") | No effect | A role term also hits, so the title is ambiguous: **Flagged**: "title mentions ..., which is usually not a fit" |

Use `title_flag_terms` for kinds of role that are usually not a fit but share words with ones that are, so a person decides when a title mixes the two. Use `title_exclude` only for words that rule a posting out every time.

## Description rules

| Rule | Config | Match | No match | Can't tell |
|---|---|---|---|---|
| Domain | `[domain] terms` | Adds to the overlap score. Also matched against the company's `sector` in `companies.toml`. | No domain overlap; work terms can still make a Match | n/a: absence isn't uncertainty. With no work terms either, the outcome is **Possible**. |
| Work | `[work] terms` | Adds to the overlap score | No work overlap; domain terms can still make a Match | n/a: same as Domain |
| Description flag | `[description] flag_terms` | **Flagged**: "description mentions: ..." | No effect | n/a: the rule itself means "needs a look" |

## Location rules

The posting's location text is split on `;` (several locations), and each part is judged separately. **The best tier among the parts wins.** A placed part beats a flagged part, which beats an excluded part.

For each part, in order:

1. **State or province code.** A code after a comma, such as `Springfield, VT`, places the part in the tier that lists the code (`state_codes`, `province_codes`).
2. **Remote abroad.** A remote part that names a place in `non_us_remote_terms`, such as "Remote - India", is **excluded**.
3. **Place names.** `place_terms` match anywhere in the part.
   - A tier that lists codes only accepts its place names with no code or with one of its own codes. So "Burlington, MA" never matches a tier with `burlington` and code `VT`.
   - A tier with no codes accepts its place names with any code, which suits a city that spans two states.
4. **Country names.** `us_wide_terms` and `country_terms` count only for a bare country ("United States") or a remote listing ("Remote - US"). An on-site "Austin, TX, United States" is not "remote US".
5. **Plain "Remote".** "Remote" (or another listed remote term) with nothing else goes to the first tier that lists it.

If nothing places a part:

| Situation | Result |
|---|---|
| The part contains an `ambiguous_terms` entry ("Springfield" alone, or "CA", which could be California or Canada) | **Flagged** |
| The part is remote but the rest isn't recognized ("Remote - European Union") | **Flagged**, with a hint to add the place to a tier or to `non_us_remote_terms` |
| The board marks the job remote, but its city or country fits no tier | **Flagged** |
| No location given | **Flagged**: "location not stated" |
| Anything else | **Excluded**: "matches no location tier" |

**Tuning tips:**
- A place that keeps getting flagged as "no tier recognizes ..." belongs either in a tier or in `non_us_remote_terms`.
- A place that's wrongly excluded may need its state or province code added to a tier, or adding as a place name to a tier with no codes.

## Travel

**Config:** `[travel] max_percent`. If `max_percent` is left out, the travel rule is off.

**How it reads travel:** every percentage near a travel word ("travel", "traveling", and so on) in the same sentence becomes a range.

| Wording | Range |
|---|---|
| "25%", "~25% travel", "approximately 25%" | 25 to 25 |
| "15-30%", "15 to 30 percent", "15%-30%" | 15 to 30 |
| "up to 25%", "less than 25%", "under 25%", "will not exceed 25%" | 0 to 25 |
| "50%+", "at least 50%", "more than 50%" | 50 to 100 |

**How it decides**, using all of the posting's ranges together:

| Result | When | Outcome |
|---|---|---|
| **Match** | Every range tops out at or below `max_percent`. "No travel" or "travel is not required" counts as 0%. | Passes; the reason shows the quote |
| **No match** | Every range starts above `max_percent` (e.g. "~75%" against 40) | **Excluded** |
| **Can't tell** | Ranges straddle the limit ("up to 50%" against 40 could be 10% or 50%) or conflict | **Flagged** |
| **Can't tell** | Travel is described without a percentage ("twice per year", "occasional travel", "90+ days per year") | **Flagged**, with the sentence quoted. Days or weeks per year aren't converted, because calendar vs. working days would be a guess. |
| **Can't tell** | Travel isn't mentioned at all | **Flagged**: "travel not stated" |

## Sponsorship

**Config:** `[sponsorship]` terms. **Applies only** when the posting's best location tier has `requires_sponsorship = true`, so postings in other tiers ignore sponsorship wording.

| Result | When | Outcome |
|---|---|---|
| **Match** | An offer term (`positive_terms`) appears | Passes |
| **No match** | A refusal term (`hard_no_terms`) appears | **Excluded** |
| **No match** | Only eligibility wording (`eligibility_terms`), and the title isn't a pathway title | **Excluded** |
| **Can't tell** | Only eligibility wording, and the title matches the tier's `pathway_title_terms` | **Flagged**: the pathway permit may satisfy the requirement |
| **Can't tell** | A refusal and an offer in different sentences | **Flagged**: "conflicting statements" |
| **Can't tell** | None of the terms appear | **Flagged**: "sponsorship not stated" |

**Watch out for:**
- **Negated offers.** An offer term inside a sentence that also has a refusal term doesn't count. "Visa sponsorship is not available" contains the offer phrase "visa sponsorship", but it's a refusal.
- **Pathway titles.** When the title matches the tier's `pathway_title_terms`, the tier's `pathway_note` is added as a "note:" line, on a Match or a Flagged posting. The report sorts these postings to the top of Flagged.
- **Unmatched wording.** Eligibility wording only matches the exact phrases you configure. A posting that says "must be legally authorized to work in" followed by a list of countries won't match unless that phrase is in `eligibility_terms`. Until then, it's flagged as "not stated", which is safe.
