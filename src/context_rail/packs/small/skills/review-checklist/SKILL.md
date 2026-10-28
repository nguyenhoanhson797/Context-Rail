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
# Review checklist

- The candidate commit is the one the Checks ran against, and the Check definitions are
  unchanged since they passed.
- Every declared Check passed with recorded inputs; freshness is fresh, not unknown.
- The diff stays inside the Item's scope and the charter's conventions.
- No protected store document, adapter file or acceptance Check was edited by the worker.
- Failures and unknowns are visible in the report instead of being presented as success.

Approve exactly what the evidence shows. Refuse when the evidence is missing.
