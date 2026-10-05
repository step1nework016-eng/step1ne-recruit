# Search Strategy

## Goal

Find public creator profile pages that match backend-provided campaign intent.

## Which platforms to search

The backend's `start-external-search` response includes a `platforms` array. Search **every platform
listed there**, not just Instagram — Instagram was the only one implemented until 2026-09-15; the
patterns below now cover all six values used by `campaign_platform_strategies`
(`instagram`, `threads`, `dcard`, `xiaohongshu`, `tiktok`, `youtube`). If `platforms` is empty/missing,
default to Instagram only (legacy behavior).

Run each backend-provided `searchIntent` once per requested platform before moving to alternate
phrasings — breadth across platforms matters more than exhausting one platform's query variants.

## Query construction by platform

**Instagram** — `site:instagram.com <searchIntent>`
- `site:instagram.com "美妝" "開箱"`
- `site:instagram.com 保養 開團 台灣`

**Threads** — `site:threads.net <searchIntent>` (Threads profile URLs are `threads.net/@<handle>`)
- `site:threads.net 保養 心得`

**Dcard** — search within the relevant board rather than a generic site: query, since Dcard is a
forum (author ≠ a browsable creator profile the way IG/Threads are):
- `site:dcard.tw 美妝 板 <searchIntent>` to find posts, then open the author's Dcard profile page
  (`dcard.tw/@<handle>`) to check post history/frequency before treating them as a repeat creator,
  not a one-off poster.

**小紅書 (Xiaohongshu)** — `site:xiaohongshu.com <searchIntent>`. Note: many profile pages require
login to view fully; if a profile can't be read past the login wall, treat it as a recoverable
failure (see error-handling.md) rather than guessing at follower counts or bio content.

**TikTok** — `site:tiktok.com/@ <searchIntent>` (profile URLs are `tiktok.com/@<handle>`)
- `site:tiktok.com 保養 推薦`

**YouTube** — `site:youtube.com/@ <searchIntent>` (channel URLs are `youtube.com/@<handle>` or
`youtube.com/channel/<id>`); prefer the channel's "About" page for bio/subscriber count over a
single video page.

Do not overfit to one exact query on any platform.

## Search order

1. Run each backend-provided intent at least once per requested platform.
2. If results are weak, create one alternate phrasing for that platform.
3. Prefer diversity across intents/platforms over exhausting one query.
4. Prioritize profile URLs over individual post/video URLs.

## Profile URL heuristic

Prefer URLs shaped like a profile/channel root, not a single piece of content:

| Platform | Profile pattern | Not a profile |
|---|---|---|
| Instagram | `instagram.com/<handle>/` | `/p/...`, `/reel/...`, `/stories/...`, `/explore/...` |
| Threads | `threads.net/@<handle>` | `/@<handle>/post/...` |
| Dcard | `dcard.tw/@<handle>` | a single `/f/<board>/p/<id>` post |
| Xiaohongshu | `xiaohongshu.com/user/profile/<id>` | `/explore/<note_id>` |
| TikTok | `tiktok.com/@<handle>` | `/@<handle>/video/<id>` |
| YouTube | `youtube.com/@<handle>` or `/channel/<id>` | `/watch?v=<id>` |

A single post/video can be useful as evidence, but the creator must be normalized to their profile
handle before reporting — same rule as Instagram, applied to every platform above.

## Setting `primary_platform` when reporting

`POST /creators` takes `primary_platform` as a free string (no fixed enum on the creator record).
Use the lowercase platform key consistent with `campaign_platform_strategies`: `instagram`,
`threads`, `dcard`, `xiaohongshu`, `tiktok`, `youtube`. Don't invent new spellings per search —
this value is what downstream platform-strategy matching keys off of.

## Search quality

Good search results should help answer:

- Is this an individual creator?
- Does the account produce relevant content repeatedly?
- Is there evidence of reviews/collaboration/group-buying if requested?
- Is the follower range plausible?

Do not report accounts based only on keyword coincidence.
