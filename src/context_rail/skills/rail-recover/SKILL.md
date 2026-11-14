---
schema: 1
version: 1
name: rail-recover
description: Recover from a refused rail command by reading its error code and details, retrying only what the error allows, and stopping when the budget is spent.
primitive: note
preset: skill
title: Recover from a refusal
tags: []
---
### Recover from a refusal

Read the refusal before you retry. The error has a `code` and `details`. Act on them, not on a guess.

- `CONFLICT`: the document changed after you read it. Read it again with `rail show <slug>`, then retry with the new etag as `--expect`. Never overwrite a change you have not read.
- `AMBIGUOUS_FOCUS` or `NO_FOCUS`: pick the container on purpose with `rail next --into <container>`, or see the candidates with `rail overview`. The escapes never skip a gate or a permission.
- Store integrity: run `rail ops doctor` to list the issues, then `rail ops reindex` to rebuild the index.
- Rollback restores an earlier version of an unprotected skill or profile as a new version: `rail ops rollback --target-class <skill|profile> --target <workspace path> --version <n> --expect <etag>`. Read the current `version` and `etag` with `rail show <slug>`. It is refused if that version's text was not kept; do not guess another number.
- A protected target (`allowed_tools`, `budget`, `budgets`, `allowed_methods`, `gates` or the Acceptance section) needs the human, who runs it with `--confirm "APPROVE <slug>"`. In a store with roles a seat cannot roll back at all. In either case, and after any refused rollback, file a proposal with `rail loop report <item> --report-file <json>` that names the target and the version, then stop.
- Attempt budgets: when you have used the attempts your profile allows, stop. Say "I could not finish", name the refusal and what you tried, and leave the Item as it is.

Never retry a refusal with the same input and hope for a different result.
