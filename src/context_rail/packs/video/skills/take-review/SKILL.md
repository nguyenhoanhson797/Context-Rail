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
# Take review

- The candidate commit is the one the Checks ran against, and the Check definitions are
  unchanged since they passed.
- Every declared Check passed with recorded inputs; freshness is fresh, not unknown.
- The take matches the shot's storyboard and acceptance: framing, movement, duration
  and the details the acceptance names.
- It holds continuity with the neighbouring shots in the scene: characters, wardrobe,
  lighting and screen direction.
- The commit carries the prompt, model, seed and references needed to regenerate it.
- No protected store document, adapter file or acceptance Check was edited by the worker.
- Failures and unknowns are visible in the report instead of being presented as success.

Approve exactly what the evidence shows. Refuse when the evidence is missing.
