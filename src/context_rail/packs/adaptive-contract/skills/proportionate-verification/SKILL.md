---
schema: 1
version: 1
name: proportionate-verification
description: Choose the smallest check a change tier justifies, without adding a test suite or mocking internals.
primitive: note
preset: skill
title: Proportionate verification
tags: []
---
### Proportionate verification

The tier selects the evidence. More tests are not automatically better evidence.

- **Trivial**: run the build and typecheck through `rail check run <item>`.
  Add no tests. If no executable check exists, record the exact command and
  observation in the result instead of inventing a check.
- **Standard**: run the existing checks through `rail check run <item>`. Add a
  test only when an existing check cannot catch a named failure this change can
  introduce. One focused test at the boundary is enough.
- **Critical**: after the recorded human approval, use at most a small set:
  one boundary or compatibility test, one failure path, and one smoke test. No
  internal mocks, no assertion on private call order, no fixture that only
  restates the implementation.
- Prefer a test that already exists and reaches the change. If a test reaches the
  change but is not declared as a Check, ask the CTO to declare it rather than
  copying it.
- Never add a test to raise a count, never widen the tier to justify more tests,
  and never weaken an existing check to make the change pass.
- Record in the result which check proved the change and which tests you
  deliberately did not add.
