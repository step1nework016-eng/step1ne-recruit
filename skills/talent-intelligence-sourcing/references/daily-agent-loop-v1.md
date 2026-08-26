# Daily Talent Sourcing Agent Loop V1

Use this mode only after the JD route, Talent DNA, hard gates, and persistent working dataset exist. The daily agent is an incremental worker, not a fresh sourcing run.

## Required persistent state

Before each run, load:

- Locked JD, Talent DNA, Job Route, hard gates, and disqualifiers.
- Candidate Memory Ledger containing every previously seen identity, alias, organization, source URL, first-seen date, last-seen date, gate status, contact status, and next eligible action.
- Source Memory containing query classes, anchors, last-run date, unique yield, duplicate rate, Qualified yield, domain readiness, Email-ready yield, dual-channel yield, failures, and cooldown.
- Work queues: `IDENTITY_QUEUE`, `QUALIFIED_QUEUE`, `CONTACT_QUEUE`, `EMAIL_VALIDATION_QUEUE`, `EMAIL_READY_QUEUE`, `LINKEDIN_MANUAL_QUEUE`, `CLIENT_READY_QUEUE`, and `SOURCE_EXPANSION_QUEUE`.
- Previous run log and unresolved human checkpoints.

If persistent state is unavailable, return `BLOCKED_NO_PERSISTENT_STATE`. Never start a supposedly incremental run from an empty memory.

## Daily pull order

Choose work from the oldest eligible queue in this order:

1. Resolve identity conflicts that block high-priority people.
2. Review P1/P2 leads through the Qualified Gate.
3. Enrich contacts only for `DIRECT_TARGET` and qualified `CONTACT_AFTER_CONFIRMATION` rows.
4. Validate discovered Emails and route Qualified candidates into Email-ready, dual-channel, LinkedIn-manual, referral, or no-automatable-contact states.
5. Confirm client-ready gaps for candidates with a verified outreach entrance.
6. Discover new raw leads only when downstream backlogs are below their work-in-progress limits.

Do not optimize raw lead count while Qualified or Contact queues are congested.

## Default work-in-progress limits and daily targets

Use these defaults unless the job owner sets different values:

| Metric | Default |
| --- | ---: |
| Maximum unreviewed P1/P2 backlog | 20 |
| Maximum Qualified candidates awaiting contact work | 10 |
| New unique Raw Lead target | 10–20 |
| Qualified Gate review target | 5–10 |
| Contact Enrichment target | 2–5 |
| Newly verified Email target | 3–5 |
| New dual-person-channel target | at least 2 |
| New high-yield source anchor target | 1 per week |

Targets are workload goals, not permission to invent results. Report actual output and shortfall.

## Incremental discovery rules

Before saving a person, compare exact and normalized name, aliases, organization, project, school, skill, location, username, and timeline against the Candidate Memory Ledger.

- Existing identity with new evidence: update the existing Candidate and record `EVIDENCE_UPDATE`; do not count a new lead.
- Existing identity from another source: record `CROSS_SOURCE_DUPLICATE`; do not recreate the Candidate.
- Same name with insufficient anchors: record `UNVERIFIED_SAME_NAME` and send to Identity Queue.
- New identity with source URL: create `NEW_UNIQUE_RAW_LEAD` and send through normal gates.

Only `NEW_UNIQUE_RAW_LEAD` counts toward daily discovery output.

## Source rotation and cooldown

Maintain an independent state for every source anchor and query class:

- `ACTIVE`: eligible today.
- `COOLDOWN_7D`, `COOLDOWN_14D`, or `COOLDOWN_30D`: recently exhausted or too repetitive.
- `RERUN_ON_NEW_ANCHOR`: rerun only after a new company, project, event, username, domain, publication, certification cohort, or date range appears.
- `HUMAN_ACTION_REQUIRED`: automation is blocked but other sources continue.
- `RETIRED_FOR_THIS_JD`: repeatedly low-fit or non-recruitable for this job only.

Switch away from a query class when any of these occur:

- Duplicate rate is 70% or higher after at least 10 reviewed names.
- Fewer than 3 new unique leads appear within the first 20 relevant results.
- Qualified yield is below 10% across two runs with at least 10 reviewed names per run.
- At least 10 Qualified people from a source produce zero Verified Email: lower only its auto-contact priority and open a contact-rich anchor.
- Email-ready yield is below 10% across two runs with at least 10 Qualified reviews per run: rotate the auto-contact query class without retiring a Fit-rich source.
- The planned public paths are exhausted and no new identity anchor exists.

Never globally blacklist a source because it failed for one JD.

## Queue state machine

Use only these transitions:

`DISCOVERED → IDENTITY_PENDING → QUALIFIED_REVIEW → DIRECT_TARGET / CONTACT_AFTER_CONFIRMATION / ADJACENT_TARGET / REFERRAL_ONLY / LONG_TERM_POOL / REJECTED`

Only qualified `DIRECT_TARGET` and `CONTACT_AFTER_CONFIRMATION` rows may transition to:

`CONTACT_PENDING → EMAIL_VALIDATION_REQUIRED / QUALIFIED_EMAIL_READY / QUALIFIED_DUAL_CHANNEL_READY / LINKEDIN_MANUAL_QUEUE / ORG_REFERRAL_QUEUE / NO_AUTOMATABLE_CONTACT / CONTACT_NOT_PUBLIC_AFTER_DEEP_SEARCH`

Only rows that pass all client readiness checks may transition to:

`CLIENT_READY`.

Expanded candidates always return to `DISCOVERED` and cannot inherit the parent candidate's fit.

## Daily completion report

Every run must write a delta report containing:

- Run date, job ID, agent/run ID, start/end time, and status.
- Sources and query classes attempted.
- Names reviewed, new unique people, evidence updates, duplicates, and identity conflicts.
- Qualified Gate reviews and classification counts.
- Person-public Email, phone, verified social, organization referral, Email-ready, LinkedIn-manual, no-automatable-contact, and dual-person-channel counts.
- Email-ready conversion rate, Email validation backlog, and `AUTO_OUTREACH_TARGET_MISSED` when no new Email-ready candidate is produced.
- New Client-ready candidates.
- Source yield, duplicate rate, cooldown changes, blockers, and credit use.
- Exact next action for every incomplete high-priority row.

Use daily success levels:

- `PRODUCTIVE`: at least one new Qualified candidate, one newly verified person-direct contact, or one new Client-ready candidate.
- `DISCOVERY_ONLY`: new unique Raw Leads were found but no downstream conversion occurred.
- `MAINTENANCE_ONLY`: only evidence/status updates or deduplication occurred.
- `ZERO_DELTA`: no new identity, evidence, qualification, contact, or source anchor was produced.
- `BLOCKED`: required state, access, or human decision prevented useful execution.

Do not call a run productive solely because searches were executed.

## Safety and autonomy boundaries

- Use only lawful public evidence and public professional contact entrances.
- Never guess Gmail, company Email patterns, private phone numbers, or social accounts.
- Never bypass login, CAPTCHA, paywalls, account recovery, access controls, or hidden fields.
- Paid providers require the existing Provider Gate and user approval for credits.
- Sending outreach, changing external systems, or revealing candidate data outside the authorized working dataset requires explicit authorization.

## Scheduler prompt

For every scheduled run, execute this instruction:

> Continue the existing job-specific talent sourcing project as an incremental daily agent. Load the locked JD, Talent DNA, Job Route, Candidate Memory Ledger, Source Memory, Email-ready Layer, queues, and previous run log. Select work using the daily pull order and work-in-progress limits. Do not recreate existing candidates or rerun exhausted equivalent queries. Apply identity resolution and the Qualified Gate before Contact Enrichment. For Qualified people, run the Email-first waterfall including public Cake/CakeResume and LinkedIn contact paths, then validate and route Email-ready versus manual-only results. Record every success, duplicate, false positive, failure, blocker, cooldown, and next action. Finish with new Unique, Qualified, Verified Email, Email-ready, dual-channel, LinkedIn-manual, no-automatable-contact, and Client-ready counts. If there is no measurable delta, report `ZERO_DELTA`; if no new Email-ready result exists, also report `AUTO_OUTREACH_TARGET_MISSED`.
