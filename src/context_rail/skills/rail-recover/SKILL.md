---
schema: 1
version: 1
name: rail-recover
description: Recover from a refused rail command by reading its error code and details, retrying only what the error allows, and stopping when the budget is spent.
primitive: note
preset: skill
tags: []
---
### Recover from a refusal

Read the refusal before you retry. The error has a `code` and `details`. Act on them, not on a guess.

- `CONFLICT`: the document changed after you read it. Read it again with `rail show <slug>`, then retry with the new etag as `--expect`. Never overwrite a change you have not read.
- `AMBIGUOUS_FOCUS` or `NO_FOCUS`: pick the container on purpose with `rail next --into <container>`, or see the candidates with `rail overview`. The escapes never skip a gate or a permission.
- Store integrity: run `rail ops doctor` to list the issues, then `rail ops reindex` to rebuild the index.
- Rolling back: `rail ops rollback --target-class <skill|profile> --target <workspace path> --version <n> --expect <etag>` restores an earlier version of a skill or profile file as a new version. It applies to no other document. Read the current `version` and `etag` first with `rail show <slug>`. If the text of that version was not kept, the command is refused; do not guess another number.
- A protected target is human-only: a restore that changes `allowed_tools`, `budget`, `budgets`, `allowed_methods`, `gates` or the Acceptance section needs a person. Stop and ask; they run it with `--confirm "APPROVE <slug>"`.
- Attempt budgets: when you have used the attempts your profile allows, stop. Say "I could not finish", name the refusal and what you tried, and leave the Item as it is.

Never retry a refusal with the same input and hope for a different result.
