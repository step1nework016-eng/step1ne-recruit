# KOC/KOL Discovery Skill

## Purpose

This skill is the external discovery worker for the KOC/KOL matching system.

It does **not** own the database, campaign state machine, deduplication, fit scoring, or long-term candidate management. Those responsibilities belong to the backend.

The skill has one job:

> Given a campaign that needs external discovery, use the agent's native web search and browser capabilities to find suitable public creator accounts, read their public profile information, classify them, collect concrete evidence, and report qualified creators to the backend one by one.

---

## When to use this skill

Use this skill when the user asks to:

- run external creator discovery for a campaign;
- find more KOC/KOL candidates because the existing database is insufficient;
- continue an external search job for a campaign currently in `needs_external_search`;
- replenish a campaign's candidate pool.

Do not use this skill for:

- creating or editing campaigns;
- changing campaign status manually;
- scoring candidates locally;
- reviewing/shortlisting candidates;
- sending outreach email;
- scraping sites with custom scripts/headless automation;
- paid third-party scraping APIs.

---

## Required inputs

Before execution, resolve:

- `campaignId`
- `workspaceId`
- backend base URL

Development default:

`http://localhost:8787/api/v1`

Every backend request must include:

`X-Workspace-Id: <workspaceId>`

If `campaignId` or `workspaceId` is missing and cannot be inferred from the current task context, ask for it.

---

## Core execution flow

### Step 0 — Check campaign gap when possible

If campaign overview is available, inspect the current match coverage before starting.

Use the gap to decide how many qualified candidates should be added during this run.

See `references/stopping-rules.md`.

### Step 1 — Start external search

Call:

`POST /campaigns/:campaignId/start-external-search`

Save the returned:

- `jobId`
- `searchIntents`
- `categories`
- `regions`
- `platforms`
- `followerMin`
- `followerMax`
- `preferredSignals`
- `exclude`

Never change campaign state yourself.

### Step 2 — Build search queries

For each `searchIntent`, generate platform-focused searches.

For Instagram, prefer queries such as:

`site:instagram.com <searchIntent>`

Do not use custom scraping code.

Search broadly enough to discover profile pages, but prioritize creator profile URLs over post/reel URLs.

See `references/search-strategy.md`.

### Step 3 — Inspect candidate profiles

For every promising profile:

1. Normalize the handle.
2. Skip handles already inspected in this skill session.
3. Open/read the public profile using the agent browser capability.
4. Extract public data where visible:
   - handle
   - display name
   - followers
   - following
   - **recent post engagement** — open the account's last 3–5 public posts (or videos, for
     TikTok/YouTube) and record likes, comments, and views (views only exist for video platforms).
     Report the average across whatever posts you could actually read as `avg_likes`,
     `avg_comments`, `avg_views`, and compute `engagement_rate` as
     `(avg_likes + avg_comments) / followers` (omit if followers is 0 or unknown). Also report
     `engagement_sample_count` = how many posts you actually sampled. This exists because follower
     counts are buyable and are not a reliable screening signal on their own — a real engagement
     rate is. If engagement rate looks implausibly low relative to follower count (e.g. under
     ~0.5% with no other explanation), set `quality_flag: "suspicious"` when reporting the creator
     in Step 4 rather than silently passing it through. Also keep the per-post numbers (not just
     the average) — after creating/updating the creator in Step 4, call
     `POST /creators/:creatorId/engagement-samples` with
     `{"samples":[{"platform":"instagram","post_url":"...","likes":..,"comments":..,"views":..,"engagement_rate":..},...]}`,
     one entry per post you actually opened, using the real post URL for each. This lets a human
     click through and see the actual posts behind the average, not just a number. Skip a field
     you couldn't read rather than guessing it.
   - full bio text — read the COMPLETE bio verbatim (e.g. via the browser's javascript_tool
     reading the bio element's textContent/innerText), not a paraphrase or a partial read of
     what's visible in one screenshot. A truncated bio silently loses whatever contact info sits
     at the end of it (email/LINE/link are commonly the last lines) — the backend auto-extracts
     email/LINE ID/external link from bio_raw at upsert time, but only from whatever text
     actually made it into bio_raw, so an incomplete read here can't be recovered downstream.
   - email
   - LINE ID
   - external link
   - profile photo image URL (the account's current avatar/profile picture — pass it as
     `avatar_url` when creating/updating the creator in Step 4. The backend downloads and caches
     this into its own storage on first capture, so it's fine to pass IG's signed/temporary CDN
     URL as-is — you don't need to worry about it expiring)
5. If the external link looks like a link-in-bio aggregator (domain contains `linktr.ee`,
   `beacons.ai`, `lit.link`, `campsite.bio`, `linkin.bio`, `carrd.co`, or similar), open that page
   too and extract every other social platform link it lists (Instagram/Threads/Facebook/YouTube/
   TikTok/website/shop, whatever is actually present — don't guess or invent ones that aren't
   there). Pass these as `social_links` when creating/updating the creator in Step 4, formatted as
   an array of `{"platform":"instagram","url":"https://..."}` objects (lowercase platform name).
   Skip this step entirely if the external link isn't an aggregator — don't visit ordinary shop/
   website links looking for social links, only actual link-in-bio pages.
6. Classify creator type.
7. Compare the account against campaign constraints.
8. Find at least one concrete piece of evidence explaining why the creator matches the campaign.

See:

- `references/creator-classification.md`
- `references/evidence-rules.md`

### Step 4 — Report qualified creators immediately

For every qualified creator, report immediately rather than batching all results.

First create/update creator:

`POST /creators`

Then use the returned creator ID:

`POST /campaigns/:campaignId/candidates/from-creator`

Always include `discovery_channel` in this call — format `"<platform>:<angle-or-batch-label>"`
(e.g. `"threads:PDRN鮭魚精華"`, `"instagram:manual"`). This lets the consultant filter the
candidate list by which channel/search-angle found each person later. Falls back to just the
creator's platform if omitted, but omitting it loses the angle-level detail — always pass it when
you know which search intent/angle produced this creator.

Do not locally calculate the final fit score. The backend owns scoring.

### Step 5 — Continue until stopping condition

Continue across search intents until one of the stopping rules is reached.

Primary goal:

- fill the current campaign gap with a reasonable safety buffer;
- avoid wasting browser/search operations once the target has been reached.

See `references/stopping-rules.md`.

### Step 6 — Finish search

Always attempt to close a started job cleanly when the search run is complete:

`POST /campaigns/:campaignId/finish-external-search`

Body:

```json
{
  "job_id": "job_xxx"
}
```

### Step 7 — Hand off to the audit skill

Real gap found 2026-09-16: a discovery run that stops right after reporting new creators leaves
them with whatever data quality the search happened to surface — no guaranteed avatar, no
guarantee the bio wasn't truncated, no check for a Linktree/other-platform link. Don't treat
"reported to the backend" as "done."

After finishing a search (or as the last step of any unattended/scheduled trigger that doesn't
have a human reviewing results immediately), run `koc-kol-audit-skill` against the creators you
just added this run — not the whole pool, just the fresh batch. This is a separate skill/pass, not
something to fold into the search loop above; do it after Step 6, not interleaved with searching.

Then summarize the run to the user.

---

## Progress reporting

This is a potentially long-running task. Keep progress updates short and useful.

Recommended checkpoints:

- after search job starts;
- after every 5 newly reported qualified creators;
- when switching to a new search intent;
- when a meaningful blocker occurs;
- at final completion.

Example:

> 已完成 2/3 組搜尋方向，目前讀過 21 個帳號，新增 8 位合格候選人，3 位因品牌/商店帳號排除，2 位無法讀取。

Do not spam the user with every individual profile read.

---

## Session-level deduplication

Maintain an in-memory set of normalized handles/URLs inspected during this run.

Before opening a profile, check whether it has already been inspected.

This optimization exists only to reduce unnecessary work. The backend remains the source of truth for deduplication and upsert behavior.

Normalize common Instagram URL variants to the same handle.

Examples:

- `https://instagram.com/hannah.daily`
- `https://www.instagram.com/hannah.daily/`
- `@hannah.daily`

All map to:

`hannah.daily`

---

## Failure handling

A single unreadable profile must not abort the entire search.

Typical recoverable failures:

- private account;
- deleted profile;
- login wall;
- temporary rate limit;
- incomplete profile data;
- post/reel URL instead of profile URL;
- missing follower count;
- browser read failure.

Default action:

1. record the failure in session notes;
2. skip that profile;
3. continue searching;
4. include aggregate failure counts in progress/final summary.

If the backend start/finish API fails, do not fabricate success. Surface the failure clearly.

See `references/error-handling.md`.

---

## Quality rules

A creator should normally be reported only when:

- the account appears to be a real individual creator/KOC/KOL, unless campaign rules explicitly allow another type;
- follower count is within the requested range, when visible;
- the account is relevant to at least one campaign search intent/category;
- there is concrete evidence from bio/profile/public content supporting relevance;
- excluded account types are filtered out.

Do not report an account only because its username looks relevant.

---

## Evidence rule

Every reported campaign candidate must include evidence.

Good evidence:

- bio explicitly mentions beauty reviews / product testing;
- profile shows repeated skincare review content;
- public content indicates prior brand collaboration;
- bio/link indicates group-buying / affiliate collaboration;
- visible content aligns with the target audience/content style.

Weak evidence:

- username contains "beauty" only;
- profile picture looks like a creator;
- follower count alone.

Evidence should be concise and factual, not speculative.

---

## Creator type classification

Allowed values:

- `personal_koc`
- `creator`
- `influencer`
- `brand_account`
- `store_account`
- `media_account`
- `unknown`

Prefer conservative classification.

If unsure whether an account is a person or a brand/store, use `unknown` rather than pretending certainty.

See `references/creator-classification.md`.

---

## Hard prohibitions

Do not:

- write a crawler;
- use Playwright/Selenium/headless browser automation as a scraping loop;
- repeatedly fetch Google/Instagram from the backend server;
- send all results as one giant batch;
- manually mutate campaign status;
- invent emails, follower counts, evidence, or profile content;
- mark a search as finished if it was never successfully started.

---

## Final response format

At completion, give the user a compact summary:

```text
外部搜尋完成 ✅

案件：<campaign>
搜尋方向：3 組
檢查帳號：34
新增/更新 Creator：16
加入案件候選人：12
排除品牌/商店帳號：5
無法讀取：3

目前已交回後端重新統計候選人池，案件進入人工審核階段。
```

If the search stopped early due to a blocker, state exactly what failed and what was successfully completed before the failure.
