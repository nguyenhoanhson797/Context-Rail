---
schema: 1
version: 1
name: lean-testing
description: Pick the smallest check that proves a change, and add a permanent test only when it guards a named failure.
primitive: note
preset: skill
tags: []
---
### Lean testing

Choose the smallest check that proves the change. The goal is evidence, not a larger test suite. A change can be complete with zero new tests.

Before you write anything, look for what already proves the behavior:

- Run the declared Checks for the Item with `rail check run <item>`. A declared Check is the proof the Item asks for.
- Read the existing tests near the code you changed. If one already fails on the broken behavior and passes on the fixed one, reuse it.
- Use the shared fixtures and helpers the project already has. Do not write a local variant.

Add a permanent test only when both hold: it guards a named failure (say which behavior breaks without it), and it asserts a property the outside world requires, such as a refusal code, a file format or a budget. A test that only restates the implementation does not qualify.

When a new test is warranted, write it first, run it alone and watch it fail for the right reason: the feature is absent, not a typo or a bad import. Then write the smallest code that makes it pass.

Every new refusal (an error the engine returns) gets a negative test. Never add a test only to raise a count.

In the report, name the check that proved the change, and the test you added if any. If no check proves the change, say so instead of claiming success.
