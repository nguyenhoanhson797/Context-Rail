---
schema: 1
version: 1
name: take-review
description: The checks a separated reviewer runs before approving a take.
primitive: note
preset: skill
title: Take review
tags: []
---
### Take review

- Read the exact current producer result record (`rail loop result`) before approving;
  never treat reviewer notes as producer evidence.
- The take matches the shot's storyboard and acceptance: framing, movement, duration
  and the details the acceptance names.
- It holds continuity with the neighbouring shots in the scene: characters, wardrobe,
  lighting and screen direction.
- The result record (and candidate commit, when present) carries the prompt, model, seed
  and references needed to regenerate it.
- The take stays inside the Item's scope and the charter's conventions.
- New checks are justified: each guards a named failure of the take; no new check is
  fine when the existing checks prove it; flag checks that only pin an incidental detail
  such as a seed or a file name.
- For candidate-backed takes, the candidate commit matches the producer result binding
  and Checks ran with unchanged Check definitions.
- Every declared Check passed with recorded inputs; freshness is fresh, not unknown.
- No protected store document, adapter file or acceptance Check was edited by the worker.
- Partial, blocked, or failed outcomes and unknowns are reported honestly instead of being
  presented as completed work.

Write your structured Markdown review verdict (`rail loop review --notes-file <md>`).
Approve only what the evidence shows; reject when result or check evidence is missing.
