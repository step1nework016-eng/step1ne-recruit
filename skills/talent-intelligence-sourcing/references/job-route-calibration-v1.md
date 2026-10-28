# Job Route Calibration V1

Use this gate for every new JD before bulk sourcing.

## Goal

Minimize human input without treating a job title as a complete candidate definition. Ask only questions whose answers change the talent ecosystem, source map, evidence standard, or exclusions.

## Intake sequence

1. Parse all facts already present in the JD. Do not re-ask them.
2. Detect ambiguous dimensions: candidate form, actual work/content mode, maturity, market visibility/fame ceiling, employment mode, and explicit exclusions.
3. Ask at most three short multiple-choice questions. Prefer one question when one answer resolves the route.
4. Create an Archetype Lock containing:
   - target outcome and day-to-day behavior;
   - positive candidate archetypes;
   - forbidden or adjacent archetypes;
   - target maturity and visibility/fame ceiling;
   - required public evidence;
   - permitted source ecosystems;
   - blocked source ecosystems;
   - hard gates that remain `CONTACT_AFTER_CONFIRMATION`.
5. Set `ARCHETYPE_LOCKED` only after explicit user approval or unambiguous prior context.

## Five-sample gate

1. Find exactly five named samples with source URLs.
2. Do not perform Email or phone deep-search during calibration. Public contact details encountered incidentally may be logged but must not influence Fit.
3. Label each sample `MATCH`, `MISMATCH`, or `UNCERTAIN` against the complete Archetype Lock, including capability, maturity, visibility, and realistic recruitability.
4. Full bulk sourcing (up to 20 new people per run) is allowed when at least four samples are `MATCH` and none reveals a systemic route conflict. Being famous, senior, expensive, commercially established, or unlikely to accept the role is a mismatch whenever the lock targets emerging, trainable, or actively interested talent.
5. Otherwise set `ROUTE_CALIBRATION_FAILED`, summarize the false-positive pattern, and revise the lock. Under the Step1ne override below, a failed calibration no longer blocks the run: continue with a capped search (at most 10 new people) using the revised lock, and every result is tagged `方向待確認`.

## Step1ne override (2026-10-28, approved by Jacky)

This section overrides every "do not bulk-source / do not deep-search contacts until 4/5 pass" sentence in SKILL.md, full-prompt-v3.4.md, job-strategy-router.md and workflow.md for Step1ne runs.

Why: in 101 recorded runs, 58 failed calibration and 50 returned 0 people. The gate required five *new* samples, but for narrow roles (e.g. 后里 BIM) the whole public population was already in the Candidate Memory Ledger, so a new five-person sample could not exist and every run ended empty.

1. **Known passing candidates may be calibration samples.** People already in the Candidate Memory Ledger with grade A or B (marked `可當校準樣本` in the prompt) may be used as calibration samples and logged as `KNOWN_SAMPLE`. They still never count as new output and are never written to `candidates`.
2. **Calibration is a warning, not a gate.** If calibration passes (4/5 or 5/5), run normally: up to 20 new people. If it fails, still run the formal search with the revised lock, capped at **10 new people** for that run, and report `calibration_result = ROUTE_CALIBRATION_FAILED` with the false-positive pattern in `run_summary`.
3. **Tagging.** Every person saved from a failed-calibration run is tagged `方向待確認` (note + `raw_json.direction_unconfirmed = true`). Consultants confirm or reject the direction on the swipe page; the grade rules (must-check per condition, max B without a verified résumé) are unchanged.
4. **Unchanged:** archetype lock, protected-characteristic rules, no login / no guessed Email, profile-page gate, and the per-condition grading. `FAILED_ROUTE_FALSE_POSITIVE` still applies if a consultant later finds the whole batch is the wrong archetype.

Enforced in code by `talent_sourcing_agent.py` (`CALIB_FAIL_CAP`, `calibration_failed`, `direction_unconfirmed`).

## Ambiguous livestream rule

Never leave `直播主` or `livestreamer` at the broad `CREATOR_LIVESTREAM` level.

- `REAL_PERSON_LIVESTREAM`: real-person on-camera talent, chat, singing, dancing, instruments, lifestyle, live commerce, product demonstration, hosting, or trainable adjacent talent.
- `VIRTUAL_CREATOR_LIVESTREAM`: VTuber, avatar-led, virtual talent, or primarily virtual/game-stream identities.
- `GAME_STREAMER`: gameplay-first streaming where hosting, talent performance, or selling is not the primary work outcome.

These are separate sourcing ecosystems. Do not cross them unless the Archetype Lock explicitly permits it.

For a real-person Douyin role, default positive archetypes may include emerging real-person talent creators, people publicly expressing interest in livestreaming, early-stage live-commerce hosts, event hosts, sales demonstrators, singers, dancers, musicians, models, performers, and trainable short-video creators. Default exclusions include VTubers, avatar-only creators, pure game streamers, behind-the-scenes livestream planners, senior creator managers, organization-only contacts, celebrity-level creators, and established influencers whose commercial position makes the role unrealistic.

Do not infer interest from appearance or generic social posting. Require an observable signal such as livestream participation, auditions, creator recruitment posts, public statements of interest, beginner creator activity, hosting/sales performance, or recent platform experimentation. Record `INTEREST_SIGNAL` and `VISIBILITY_TIER` (`EMERGING`, `GROWING`, `ESTABLISHED`, `CELEBRITY`). Only tiers permitted by the Archetype Lock may count as `MATCH`.

Do not use appearance, age, gender, ethnicity, disability, or other protected characteristics as sourcing or scoring criteria unless a lawful occupational requirement is explicitly established. Use observable job behavior instead: real-person on-camera presence, communication, performance, selling, schedule, equipment, and platform willingness.

## Audit statuses

- `ARCHETYPE_NEEDS_CONFIRMATION`
- `ARCHETYPE_LOCKED`
- `CALIBRATION_IN_PROGRESS`
- `CALIBRATION_PASSED_4_OF_5`
- `CALIBRATION_PASSED_5_OF_5`
- `ROUTE_CALIBRATION_FAILED`
- `FAILED_ROUTE_FALSE_POSITIVE`

## Scaling rule

Only after calibration passes may the agent build a 30–100 person pool or trigger graph expansion. (Step1ne override: a failed calibration still allows one capped run of at most 10 people with public-contact lookup, tagged `方向待確認`; recurring daily sourcing continues.) Store the five samples in the working workbook and never count failed-route candidates as usable output.
