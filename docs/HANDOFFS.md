# Structured Handoffs & Universal Continue

## Standard Picoschema Format

Every handoff record contains:
- **TASK**: Task identifier and title.
- **OBJECTIVE**: Expected goal.
- **COMPLETED**: Bullet points of finished items.
- **FILES MODIFIED**: List of altered file paths.
- **TESTS**: Test commands executed and results.
- **ERRORS**: Any blockers or issues encountered.
- **DECISIONS**: Architectural or design decisions made.
- **GIT STATE**: Clean/dirty state and branch info.
- **REMAINING WORK**: Immediate unfinished items.
- **NEXT ACTION**: Clear single-line command or prompt for the next agent.
- **RECOMMENDED AGENT**: Next agent in the workflow chain.
- **RECOMMENDED MODEL**: Suggested model for next step.

## Universal Continue (`brain continue`)

Resumes execution without re-explaining context:
- Discovers the latest active or ready task.
- Reads `handoffs/current.md`.
- Inspects git working tree state.
- Dispatches continuation prompt to the recommended agent.
