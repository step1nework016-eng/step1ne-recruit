# Candidate review workflow

## Gates

1. Parse the JD and complete the Archetype Lock, including target maturity and visibility/fame ceiling. Do not treat the title as sufficient.
2. Find exactly five calibration samples. Require at least four realistic matches—not merely capable but famous or unreachable examples—before bulk sourcing or contact enrichment.
3. If calibration fails, mark `ROUTE_CALIBRATION_FAILED`, retain the mismatch pattern, revise the route, and retest.
4. Ingest a named lead with source URL, source type, year, company/school, project, and observed skill.
5. Resolve identity using at least two consistent anchors. Log same-name conflicts.
6. Confirm JD eligibility: education, location, salary/seniority, work mode, licensing, and disqualifiers.
7. Confirm capability through project, tool, certification, work sample, competition, technical content, or role evidence.
8. Classify recruitability before contact enrichment.
9. Verify current or recent company. Distinguish current-team evidence from historical project-company evidence.
10. Verify the official company domain. Do not infer it from a name alone.
11. Search person-public email across company, project, author, member, PDF, portfolio, username, and professional-profile paths.
12. Check public Cake/CakeResume Profile, resume, portfolio, About, and Contact fields for voluntarily displayed Gmail, work Email, professional phone, Alias, and external links.
13. Check public LinkedIn Contact Info, About, Featured, and external links. Use LinkedIn for identity/contact enrichment, not primary discovery quota.
14. Search person-public professional phone across team, member, service, speaker, project, and portfolio pages.
15. Search social/profile accounts and accept only when name plus at least one organization/project/skill anchor matches.
16. Record organization referral separately. It never counts as a person channel.
17. Assign coverage and route `QUALIFIED_EMAIL_READY`, `QUALIFIED_DUAL_CHANNEL_READY`, `EMAIL_VALIDATION_REQUIRED`, `LINKEDIN_MANUAL_QUEUE`, `ORG_REFERRAL_QUEUE`, or `NO_AUTOMATABLE_CONTACT`.
18. Run provider only after name, company, official domain, and identity gate pass. Re-verify provider output.
19. Decide direct outreach, confirm first, referral, long-term pool, or reject.
20. Expand valuable candidates and score the source on both Fit yield and Email-ready yield.
21. Write every success, failure, query class, cost, and next action to the run log.

If a completed run is later found to use the wrong archetype, mark `FAILED_ROUTE_FALSE_POSITIVE`, remove every affected row from usable totals, retain it only in mismatch/audit history, and return to calibration.

## Contact status codes

- `DIRECT_EMAIL_VERIFIED`
- `DIRECT_PHONE_VERIFIED`
- `DIRECT_SOCIAL_VERIFIED`
- `ORG_REFERRAL_VERIFIED`
- `VENDOR_MATCH_PENDING_IDENTITY`
- `CONTACT_NOT_PUBLIC_AFTER_DEEP_SEARCH`
- `UNVERIFIED_SAME_NAME`
- `HUMAN_ACTION_REQUIRED`
- `PROVIDER_BLOCKED_PLAN_REQUIRED`
- `EMAIL_VALIDATION_REQUIRED`
- `QUALIFIED_EMAIL_READY`
- `QUALIFIED_DUAL_CHANNEL_READY`
- `LINKEDIN_MANUAL_QUEUE`
- `ORG_REFERRAL_QUEUE`
- `NO_AUTOMATABLE_CONTACT`

## Stop conditions

Stop repeating equivalent queries after all planned public paths are covered. Keep the candidate, record the gap, use a lawful referral, and add new source-rich candidates. Do not manufacture a second contact channel.
