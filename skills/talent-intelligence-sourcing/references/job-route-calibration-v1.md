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
4. Bulk sourcing is allowed only when at least four samples are `MATCH` and none reveals a systemic route conflict. Being famous, senior, expensive, commercially established, or unlikely to accept the role is a mismatch whenever the lock targets emerging, trainable, or actively interested talent.
5. Otherwise set `ROUTE_CALIBRATION_FAILED`, summarize the false-positive pattern, revise the lock, and run another five-sample test.

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

Only after calibration passes may the agent build a 30–100 person pool, perform contact deep-search, trigger graph expansion, or start recurring daily sourcing. Store the five samples in the working workbook and never count failed-route candidates as usable output.
