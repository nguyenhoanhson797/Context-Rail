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
- Rollback is human-only for every skill and profile target, protected or not. The rail refuses `rail ops rollback` to a seat, so do not run it or retry it. File a proposal with `rail loop report <item> --report-file <json>` instead: name the target, the version to restore, and the current `version` and `etag` from `rail show <slug>`, then stop. The human runs `rail ops rollback --target-class <skill|profile> --target <workspace path> --version <n> --expect <etag>`. It applies to no other document, and it is refused if the text of that version was not kept; do not guess another number.
- A restore that changes `allowed_tools`, `budget`, `budgets`, `allowed_methods`, `gates` or the Acceptance section is a protected target. The human also types `--confirm "APPROVE <slug>"`. Stop and ask.
- Attempt budgets: when you have used the attempts your profile allows, stop. Say "I could not finish", name the refusal and what you tried, and leave the Item as it is.

Never retry a refusal with the same input and hope for a different result.
