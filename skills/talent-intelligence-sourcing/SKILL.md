---
name: talent-intelligence-sourcing
description: Calibrate ambiguous jobs into an approved candidate archetype, classify them into sourcing ecosystems, then build, enrich, review, score, expand, and operate recurring source-backed candidate pools and verified Email-ready queues. Use for headhunting, talent mapping, passive-candidate sourcing, autonomous incremental sourcing, job-specific source routing, five-person route calibration, company-first sourcing, public Email/phone/social enrichment including Cake and LinkedIn auxiliary checks, deduplication, recruitability screening, source scoring, and spreadsheet-backed sourcing across engineering, real-person or virtual livestreaming, executive support, creative, healthcare, trades, software, sales, and research roles.
---

# Talent Intelligence Sourcing

Build an auditable candidate pool from public evidence. Optimize for usable candidates, not raw names.

## Load the packaged playbook

For every end-to-end sourcing run, read [full-prompt-v3.4.md](references/full-prompt-v3.4.md) completely before searching. Treat it as the locked operating policy; use this `SKILL.md` as the concise execution index.

Read [job-strategy-router.md](references/job-strategy-router.md) completely before sourcing any new JD. Re-run the route for every job even when a prior role looks similar.

Read [job-route-calibration-v1.md](references/job-route-calibration-v1.md) completely for every new JD. A pasted JD is not sufficient authorization to bulk-source when the title or work content maps to more than one candidate ecosystem. Require `ARCHETYPE_LOCKED`, then test exactly five candidates. Do not scale sourcing or enrich contacts until at least four of five samples match the locked archetype.

For recurring, scheduled, or autonomous sourcing on an existing JD, also read [daily-agent-loop-v1.md](references/daily-agent-loop-v1.md) completely. Require a persistent Candidate Memory Ledger, Source Memory, work queues, and prior run log. A daily run must continue incrementally; it must not restart from a blank search or count known candidates as new output.

When the user wants automated Email outreach readiness, read [email-ready-layer-v1.md](references/email-ready-layer-v1.md) completely. Optimize source priority for both Fit yield and Verified Email yield while keeping the scores separate. Produce an Email-ready queue and a separate LinkedIn-manual queue; do not treat public-profile count as automated outreach capacity.

When the output needs a spreadsheet and the user has not supplied an existing workbook, copy [Talent_Intelligence_Sourcing_Template_V22_Archetype_Calibration.xlsx](assets/Talent_Intelligence_Sourcing_Template_V22_Archetype_Calibration.xlsx) to the task output directory and populate the copy. Never edit the packaged asset in place. The V22 template adds Job Route Calibration and five-sample approval gates to the V21 Email-ready and daily-agent controls. Reusable rules are guidance rather than live candidate results.

## Start with the job and lock the archetype

1. Read the JD and produce Talent DNA: hard requirements, substitutes, salary, location, seniority, adjacent titles and skills, target companies, adjacent industries, and disqualifiers.
2. Detect ambiguous labels. Ask at most three high-impact questions only for dimensions the JD cannot resolve: candidate form, work/content mode, maturity, market visibility/fame ceiling, and explicit exclusions.
3. Write an Archetype Lock with positive archetypes, negative archetypes, target maturity, visibility/fame ceiling, evidence required, and forbidden source ecosystems. Require human approval or explicit prior context.
4. Route the locked archetype. Assign a primary talent ecosystem, optional secondary ecosystem, and overlays. Never leave a broad label such as `CREATOR_LIVESTREAM` unresolved; refine it to real-person or virtual livestreaming.
5. Source exactly five calibration candidates without contact deep-search. Require at least four matches across capability and recruitability. A famous, overqualified, commercially established, or otherwise unrealistic person is not a `MATCH` when the lock asks for emerging or trainable talent. Otherwise mark `ROUTE_CALIBRATION_FAILED`, record false-positive patterns, revise the lock, and retest.
6. Separate `Capability` from `Recruitability`. Do not let professors, founders, managers, architects, owners, influencers, or people with rich public profiles rank as direct candidates merely because they are easy to find.
7. Assign `DIRECT_TARGET`, `CONTACT_AFTER_CONFIRMATION`, `ADJACENT_TARGET`, `REFERRAL_ONLY`, `LONG_TERM_POOL`, or `REJECTED` before contact work.

## Build sources before searching individuals

Do not build a bulk source map until the five-sample calibration gate passes. Sources that mostly produce a forbidden archetype must be blocked for that JD even if they produce many names or Emails.

Check all eight source families: certifications, courses, associations, events, projects/tenders, technical works, target companies/alumni, and communities/referrals.

Re-rank those source families using the job route. Examples: construction roles favor projects and target companies; creator roles favor public content accounts and credited collaborations; licensed healthcare roles favor license, training, clinic, and professional-association evidence. A source that performs poorly for one ecosystem must not be globally blacklisted.

Prefer company-first records when current-company or contact coverage is weak:

1. Official team pages.
2. Named project teams and collaborators.
3. Award entries requiring relevant tools or deliverables.
4. Technical authors and speakers tied to a company.
5. Alumni and former employees.

Score every source on both `FIT_POTENTIAL` and `AUTO_CONTACT_POTENTIAL`. Contact potential changes source execution priority, never Candidate Fit. A source that produces Qualified people but no Verified Email after at least 10 Qualified reviews should remain available for Fit while the agent opens a more contact-rich source anchor.

Require a source URL for every person. Keep recruiting platforms auxiliary and below 30% of new leads.

## Review each candidate

Follow the full gates in [workflow.md](references/workflow.md). Never skip identity resolution.

Minimum identity rule: exact name plus at least two consistent anchors among company, school, project, skill, location, role, and timeline. Log conflicting same-name results instead of merging them.

Treat project-company evidence as recent or historical unless an official current-team page confirms employment.

## Enrich contacts in priority order

Search in this order:

1. Person-public professional email.
2. Person-public work/professional phone.
3. Verified personal professional social/profile.
4. Official organization referral email, phone, or form.

For each medium, search exact name with company, project, skill, school, username, author pages, public PDFs, company team pages, association member pages, and public professional profiles. Search both academic and non-academic paths.

For every Qualified person, check public Cake/CakeResume profiles, resumes, portfolios, About/Contact fields, and public LinkedIn Contact Info, About, Featured, and external links. Publicly displayed Gmail, work Email, or professional phone can be saved only after identity verification. Cake and LinkedIn remain auxiliary contact/identity sources and do not count toward the non-recruiting discovery quota.

Use the route-specific contact waterfall and stop rules in [job-strategy-router.md](references/job-strategy-router.md). Public Cake/CakeResume and LinkedIn checks are mandatory for Qualified people when publicly accessible; GitHub, portfolios, social bios, public phone listings, and academic pages remain route-specific conditional paths.

Never guess Gmail, company email formats, private phones, or social accounts. Never use leaks, account recovery, CAPTCHA bypass, login bypass, or hidden data. Organization channels never count as person-direct channels.

Use `NOT_FOUND`, `CONTACT_NOT_PUBLIC_AFTER_DEEP_SEARCH`, `UNVERIFIED_SAME_NAME`, or `HUMAN_ACTION_REQUIRED` honestly. Two direct media are a target, not permission to lower confidence.

## Use providers safely

Only send a person to a company-domain email finder when `Full Name + Current/Recent Company + Official Domain + Identity Confidence` are present.

Run paid providers in batches of 1–5 after showing maximum credits and receiving consent. Treat provider results as `VENDOR_MATCH_PENDING_IDENTITY` until company, role, project, or school evidence confirms them. Inferred email must not be used for outreach.

If an API is blocked, record the technical state and switch sources; never rewrite it as `NOT_FOUND`.

## Score outcomes and sources

Keep Fit Score, Recruitability Class, and Contactability separate. Track source yield: discovered names, new unique people, direct/adjacent/referral counts, official-domain readiness, direct email/phone/social hits, duplicates, and failure reasons.

If domain readiness is below 30%, stop large provider batches and switch to official team, project, company, author, and alumni sources.

Every A-level direct candidate must trigger at least five graph-expansion attempts through company, former company, collaborators, authors, speakers, and communities.

## Required output

Maintain a spreadsheet or structured dataset with Job Route Calibration, five calibration candidates, approval results, Source Strategy, Candidate Pool, Contact Enrichment, dual-channel coverage, run log, entity-resolution log, search-path coverage, review SOP, provider waterfall, company/domain gate, company-first leads, Talent DNA, Source Score, and Next Expansion.

If a bulk run later proves that the route was wrong, mark the run `FAILED_ROUTE_FALSE_POSITIVE`, remove its candidates from usable totals, retain them only in the mismatch audit, and return to archetype calibration. Rich contacts never preserve a failed route.

Write every sourcing attempt into the working workbook, including successful discoveries, `NOT_FOUND`, false-positive identity matches, inaccessible sources, query paths, stop reasons, credit use, and the next action. Keep the packaged template blank and reusable; save each live sourcing run as a separate working copy.

For daily-agent mode, lead with delta metrics rather than lifetime totals: new unique identities, new Qualified candidates, newly verified Emails, new Email-ready candidates, new LinkedIn-manual candidates, new dual-channel candidates, and new Client-ready candidates. Mark runs with no measurable state change as `ZERO_DELTA`; mark runs with no new Email-ready output as `AUTO_OUTREACH_TARGET_MISSED` when automated Email capacity is the objective. Search activity alone is not production.

Lead with measured results: unique candidates, direct targets, person emails, person phones, verified social profiles, organization referrals, provider-ready rows, gaps, and next sources. Do not call a raw lead count a client-ready count.
