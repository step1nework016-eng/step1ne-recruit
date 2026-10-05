# KOC/KOL Discovery Skill Pack

This pack defines the AI agent responsible for external creator discovery in the KOC/KOL matching system.

## Files

- `SKILL.md` — primary skill instructions
- `agents/discovery-agent.md` — role/behavior prompt
- `references/api-contract.md` — backend endpoints and payloads
- `references/search-strategy.md` — web search strategy
- `references/creator-classification.md` — account type judgment guide
- `references/evidence-rules.md` — what counts as valid evidence
- `references/stopping-rules.md` — target size and stop conditions
- `references/error-handling.md` — retries and failure behavior
- `references/example-run.md` — end-to-end example

## Architecture principle

The agent searches and reads public creator information.
The backend remains the source of truth for persistence, deduplication, scoring, campaign states, and workflow transitions.
