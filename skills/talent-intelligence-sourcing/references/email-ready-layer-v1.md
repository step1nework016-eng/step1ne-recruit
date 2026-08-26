# Email-ready Sourcing Layer V1

Use this layer when the user needs a pool that can support automated Email outreach. It changes source prioritization and handoff metrics without changing Fit.

## Non-negotiable separation

- `FIT_POTENTIAL` predicts job relevance.
- `AUTO_CONTACT_POTENTIAL` predicts the likelihood of finding a verified person-public Email and, ideally, a second person channel.
- Contact availability never increases Fit Score and never moves an unqualified person into the Direct Pool.
- A Fit-rich but Email-poor source remains useful for referrals or manual outreach; lower only its auto-contact priority.

## Source scoring fields

For every source anchor record:

- Named people and New Unique people.
- Qualified reviewed and Qualified yield.
- Current/recent company coverage.
- Official-domain readiness.
- Verified person Email count.
- Verified person phone and social count.
- Dual-person-channel count.
- `EMAIL_READY_RATE = QUALIFIED_EMAIL_READY / QUALIFIED_REVIEWED`.
- `AUTO_CONTACT_POTENTIAL`: HIGH / MEDIUM / LOW / UNKNOWN.
- Last run, cooldown, failure reason, Source URL, and next action.

Prefer source classes that naturally expose both identity and contact anchors: official team/author pages, named project pages, speaker pages, public conference material, public PDF/slide author blocks, member directories, portfolios, GitHub profiles/README, personal sites, and public resumes.

## Email-first contact waterfall

Run only after identity and Qualified Gate pass:

1. Current/recent company and official domain.
2. Official team, author, project, case-study, award, speaker, member, PDF, and slide pages.
3. Cake/CakeResume public profile, public resume, portfolio, About, and Contact sections.
4. LinkedIn public Profile, public Contact Info, About, Featured, and publicly linked sites.
5. Personal site, portfolio, GitHub public Profile/README, YouTube About, and verified social Bio.
6. Person-public professional phone and verified social accounts.
7. Provider Gate only when full name, current/recent company, official domain, and identity confidence exist.

Cake and LinkedIn are auxiliary contact and identity sources; they do not count toward the 70% non-recruiting discovery quota. A Gmail or phone shown voluntarily on a public Cake/LinkedIn page can count only when the page belongs to the verified candidate. Do not use login bypass, hidden fields, guessed company patterns, guessed Gmail, masked data, account recovery, leaks, or same-name assumptions.

## Required candidate routing

- `QUALIFIED_EMAIL_READY`: qualified, identity confirmed, and at least one verified person-public or re-verified professional Email suitable for an authorized outreach handoff.
- `QUALIFIED_DUAL_CHANNEL_READY`: Email-ready plus a second verified person channel.
- `EMAIL_VALIDATION_REQUIRED`: candidate Email exists but still needs source, identity, or deliverability verification.
- `LINKEDIN_MANUAL_QUEUE`: qualified, verified LinkedIn entrance, and no verified Email after the planned paths.
- `ORG_REFERRAL_QUEUE`: only an official organization entrance exists.
- `NO_AUTOMATABLE_CONTACT`: planned public paths are exhausted and no verified Email exists.

Never place `VENDOR_MATCH_PENDING_IDENTITY`, inferred Email, organization Email, or unverified same-name contact into `QUALIFIED_EMAIL_READY`.

## Rotation and failure controls

- After at least 10 Qualified reviews from one source with zero Verified Email, lower its auto-contact priority one level and open a new contact-rich anchor.
- If two runs each review at least 10 Qualified people and Email-ready yield remains below 10%, rotate the auto-contact query class. Keep the source active for Fit when its Qualified yield is useful.
- Do not rerun equivalent queries after `CONTACT_NOT_PUBLIC_AFTER_DEEP_SEARCH`. Reopen only when a new company, domain, project, event, publication, username, portfolio, Cake page, or LinkedIn Featured link appears.
- Raw lead volume alone cannot satisfy an automated-outreach sourcing objective.

## Daily targets and report

Defaults unless the job owner changes them:

- 3–5 newly verified Emails.
- At least 2 new dual-person-channel candidates.
- Report Email-ready conversion rate and Email validation backlog.

Always report actuals and shortfall. Use `DISCOVERY_ONLY` when only Raw Leads were added. A run may be `PRODUCTIVE` for sourcing while still recording `AUTO_OUTREACH_TARGET_MISSED` when it produces no new Email-ready candidate.

## Outreach boundary

This sourcing skill creates verified outreach-ready queues. It does not send messages by default. Actual sending requires explicit authorization and a separate outreach process that handles templates, sender identity, frequency caps, replies, bounces, opt-outs, suppression, and audit logs.
