---
# Effort the harness reported. Replace unknown with a real number; never guess.
usage: {tokens: unknown, requests: unknown}
# duration_s: 5400
# model: the model that did the work
# harness: the tool that ran it
---
# {title}

## Summary

What changed, and the main outcome.

## Contract

The objective, blast radius and non-goals this change was held to
(`Not applicable: <reason>` for a trivial change).

## Tier decision

Why this was recognized as trivial, standard or critical; name the trigger that
would have raised or lowered it.

## Verification

The checks and tests actually run, what each proved, and what was deliberately
not added. Name any existing check that already covers the change.

## Lessons

One lesson that should change the next verification decision, or
`Not applicable: <reason>` when nothing surprised you.

## Evidence

Candidate commit, checks, measurements, and any reviewer-facing artifact.

## Next steps

Follow-up work, open questions, or handoff notes.
