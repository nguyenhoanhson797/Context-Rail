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

- The take matches the shot's storyboard and acceptance: framing, movement, duration
  and the details the acceptance names.
- It holds continuity with the neighbouring shots in the scene: characters, wardrobe,
  lighting and screen direction.
- The commit carries the prompt, model, seed and references needed to regenerate it.
- New tests are justified: each guards a named failure; no new test is fine when
  existing checks prove the change; flag tests that only pin implementation.

The shared review items (candidate binding, Checks with recorded inputs, scope,
protected edits and visible failures) apply to every take as well.

Approve exactly what the evidence shows. Refuse when the evidence is missing.
