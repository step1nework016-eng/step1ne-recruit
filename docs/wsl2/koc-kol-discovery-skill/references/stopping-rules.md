# Discovery Stopping Rules

## Multi-platform campaigns: target is PER PLATFORM, not one shared pool

Real incident (2026-09-15, UXKB campaign): once Instagram alone had produced ~30 candidates, the
campaign's overall `match_coverage.gap` was nearly zero — so when Threads/Dcard/小紅書 searches
ran afterward, the shared-pool target formula below gave them almost no effort budget, and each
new platform came back with only 2-6 qualified creators. The client's brief explicitly wanted
presence across ALL 4 platforms; a near-empty campaign-wide gap must never be read as "the other
platforms don't need searching."

**Rule: when a campaign's match profile lists more than one platform, each platform gets its own
target — do not divide one shared gap number across platforms.** The `start-external-search`
response includes `searchTargetPerPlatform` — if it's a number, that IS the consultant-set target
per platform, use it directly. If it's `null`, default to **10 qualified candidates per platform**.
Either way this is subject to:
- the platform's real available supply for this niche is genuinely exhausted (all reasonable
  search-intent variants tried, hard effort ceiling below reached, and still short — report the
  shortfall honestly per "Completion" below; don't pad with weak candidates to hit the number).

A single-platform campaign still uses the shared-pool formula below unchanged.

## Primary target (single-platform campaigns, or per-platform minimum when unset above)

When campaign overview exposes `match_coverage.gap`, define:

`gap = number of additional qualified campaign candidates needed`

The skill should aim to report:

`target = max(gap + buffer, gap, 10)` — the `10` floor applies per platform per the rule above,
so a nearly-filled shared gap never collapses a new platform's target to near-zero.

Recommended buffer:

- gap 1–5: add 2 extra
- gap 6–15: add ~25% extra, rounded up
- gap 16+: add ~20% extra, rounded up

Reason: some candidates will later be rejected by human review or backend filtering.

Example:

- gap = 12
- buffer = 3
- target qualified reports = 15

## If overview/gap is unavailable

Use a conservative default target of 12 qualified creators for one run.

## Hard effort ceiling

To avoid runaway searches, stop the run when any of these occurs:

1. qualified target reached;
2. all search intents have been searched and one alternate phrasing per weak intent has also been attempted;
3. 60 unique profiles have been inspected in one run;
4. repeated browser/search blocking prevents reliable reads;
5. user explicitly asks to stop.

## Search-intent balance

Do not spend the full profile ceiling on the first keyword.

Suggested allocation:

- first pass: up to 10 unique profiles per search intent;
- second pass only for intents that produced useful qualified creators;
- stop early once target qualified reports is met.

## Completion

Even if the qualified target is not reached, finish the search job after exhausting reasonable search variants and report the shortfall honestly.
