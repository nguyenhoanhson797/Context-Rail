---
schema: 1
version: 1
name: review-checklist
description: The checks a separated reviewer runs before approving a candidate commit.
primitive: note
preset: skill
title: Review checklist
tags: []
---
### Review checklist

- Read the exact current producer result record (`rail loop result`) before approving;
  never treat reviewer notes as producer evidence.
- For code changes, the candidate commit matches the producer result binding and the
  Checks ran against that commit with unchanged Check definitions.
- Every declared Check passed with recorded inputs; freshness is fresh, not unknown.
- The diff stays inside the Item's scope and the charter's conventions.
- The `code area` impact lines match the diff; anything outside the predicted area is
  explained.
- New tests are justified: each guards a named failure; no new test is fine when
  existing checks prove the change; flag tests that only pin implementation.
- No protected store document, adapter file or acceptance Check was edited by the worker.
- Partial, blocked, or failed outcomes and unknowns are reported honestly instead of being
  presented as completed work.

Write your structured Markdown review verdict (`rail loop review --notes-file <md>`).
Approve only what the evidence shows; reject when result or check evidence is missing.
