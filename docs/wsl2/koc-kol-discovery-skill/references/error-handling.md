# Error Handling

## Principle

Profile-level failures are recoverable. Backend job-level failures are not silently recoverable.

## Profile-level failures

Examples:
- private account;
- deleted account;
- login wall;
- temporary browser read failure;
- missing public follower count;
- malformed URL;
- post/reel result without resolvable creator profile.

Action:
- increment failure/skip count;
- keep short internal note;
- do not report invented data;
- continue.

## Search result duplication

If handle already exists in the session-inspected set:
- skip opening again;
- do not count as a newly inspected profile.

## Creator API failure

If `POST /creators` fails:
- retry once if failure appears transient;
- if it still fails, record the creator as unsent and continue other candidates;
- include unsent count in the final summary.

## Candidate API failure

If creator creation succeeds but candidate reporting fails:
- retry once;
- do not repeatedly recreate the creator;
- record unsent candidate state in session notes.

## Start API failure

Do not search before a successful start call.

Explain the backend error and stop.

## Finish API failure

Do not claim the campaign entered review.

Tell the user:
- discovery work completed;
- finish call failed;
- backend may still show `searching_external`;
- provide job ID for manual/debug follow-up.
