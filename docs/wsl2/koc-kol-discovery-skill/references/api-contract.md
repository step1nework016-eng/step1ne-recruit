# Backend API Contract

Base URL in local development:

`http://localhost:8787/api/v1`

Required header:

`X-Workspace-Id: <workspace_id>`

---

## 1. Start external search

`POST /campaigns/:campaignId/start-external-search`

Expected campaign state before call:

`needs_external_search`

Example response:

```json
{
  "jobId": "job_xxx",
  "campaignId": "camp_xxx",
  "searchIntents": ["美妝 開箱", "保養 開團", "敏感肌 保養 推薦"],
  "categories": ["美妝", "保養", "敏感肌"],
  "regions": ["TW"],
  "platforms": ["instagram"],
  "followerMin": 5000,
  "followerMax": 30000,
  "preferredSignals": ["product_review", "brand_collaboration"],
  "exclude": ["brand_account", "store_account"]
}
```

The backend transitions the campaign to `searching_external`.

---

## 2. Create/update creator

`POST /creators`

Example body:

```json
{
  "primary_platform": "instagram",
  "handle": "wheniam20",
  "display_name": "Amo美妝開箱",
  "profile_url": "https://instagram.com/wheniam20",
  "followers": 12000,
  "following": 617,
  "bio_raw": "完整bio原文",
  "email": "example@gmail.com",
  "external_link": "https://linktr.ee/example",
  "line_id": "example_line",
  "creator_type": "personal_koc",
  "source": "external_search:web_search:美妝 開箱"
}
```

Optional fields should be omitted when unavailable rather than invented.

Allowed `creator_type` values:

- `personal_koc`
- `creator`
- `influencer`
- `brand_account`
- `store_account`
- `media_account`
- `unknown`

The backend safely handles repeated discovery of the same account.

---

## 3. Add creator to campaign candidate pool

`POST /campaigns/:campaignId/candidates/from-creator`

Example body:

```json
{
  "creator_id": "creator_xxx",
  "source_query": "美妝 開箱",
  "evidence_text": "Bio標示美妝分享，公開內容有多篇保養品實測與品牌合作貼文。",
  "source_url": "https://instagram.com/example"
}
```

The backend handles:

- hard campaign filters;
- fit scoring;
- fit level;
- campaign-candidate creation;
- evidence persistence.

---

## 4. Finish external search

`POST /campaigns/:campaignId/finish-external-search`

Body:

```json
{
  "job_id": "job_xxx"
}
```

The backend:

- recalculates the full candidate pool;
- recalculates remaining gap;
- transitions campaign from `searching_external` to `reviewing`;
- records activity logs.

---

## 5. Campaign overview (when available)

If available, call before discovery to inspect current gap:

`GET /campaigns/:campaignId/overview`

Use `match_coverage.gap` as the primary input for the discovery target.
