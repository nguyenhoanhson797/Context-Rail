---
schema: 1
version: 1
name: test-first
description: Write the failing test, watch it fail for the right reason, then implement.
primitive: note
preset: skill
title: Test first
tags: []
---
# Test first

1. Write the test that pins the behavior, using the shared fixtures rather than a local
   variant.
2. Run only that test and read the failure: it must fail because the feature is absent,
   not because of a typo or a bad import.
3. Write the smallest implementation that makes it pass.
4. Run the task's tests, then the default lane, before you report success.

A test you never saw fail proves nothing.
