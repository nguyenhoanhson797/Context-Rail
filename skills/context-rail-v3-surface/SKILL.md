---
name: context-rail-v3-surface
description: 'Work a Context Rail project from the compiled surface alone: types, next, show, explain and why. Use when an agent needs to know which workflow applies, its current state and the exact proof, without reading pack.yaml or the engine source.'
---

# Context Rail v3 — read the surface, not the pack

One print answers three questions: **which workflow applies**, **where the item
is**, and **what proof is required**. Never read `pack.yaml`, the charter or the
engine source; ask the rail.

## One print

```powershell
rail types                 # every document type, its workflow and use line
rail next                  # the CTO queue, or a worker's next card
rail show checkout-crash   # the item plus its workflow card
rail why checkout-crash    # the missing proof and the command that produces it
rail explain bugfix        # the full states, transitions and gates
```

`types` is role-aware: a worker with `--item` sees only its own type. `next`
returns `{queue, cards}` for a CTO and one card for a worker with `--item`.
`explain` accepts a preset, a workflow or an action.

## What a card means

```
workflow: bugfix
state: fixing
next: verifying, reported, blocked
gate: item_done
proof: checks repro-check, acceptance; review
command: rail change move checkout-crash verifying --expect <etag>
```

- **workflow/state** — the lifecycle and where the item is now.
- **next** — only these moves are legal; anything else is refused.
- **gate/proof** — what the move evaluates. `checks` are the selected checks;
  `acceptance` needs a fresh human acceptance check; `review` needs a separated
  review; `human` needs an explicitly named human; `children` needs every child
  done or dropped.
- **command** — the first legal next move, with the etag placeholder.

## Common verbs

The pack may ship verbs so the usual path is one call:

```powershell
rail do start-sprint --item sprint-3 --etag <etag>
rail do close-sprint --item sprint-3 --etag <etag>
rail do file-bug --set title="Checkout crash" --set parent=sprint-3
```

A verb runs one allowlisted gateway tool as you; it cannot change the actor, a
path, an etag or a profile. A refused verb names the missing condition and the
command that produces it. `rail types` lists the pack's verbs.

## Recovering from a refusal

1. Read `details.gate`, `details.missing` and `details.command`.
2. Run the command it names (`rail check ...`, `rail verify ...`,
   `rail review ...`) or fix the child it names.
3. Re-read `rail why <item>` before retrying. A stale verdict is not a pass.
4. If a move is still refused, `rail explain <workflow>` shows the full rule.

Do not edit the item's status by hand, and do not lower a gate: gates and the
charter are human decisions. Propose the change and stop.
