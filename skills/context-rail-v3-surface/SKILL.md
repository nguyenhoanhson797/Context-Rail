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
rail model                 # the compiled operating model, focus, unit and digests
rail next                  # the operating queue, or a worker's next card
rail show checkout-crash   # the item plus its workflow and operating card
rail why checkout-crash    # the missing proof and the command that produces it
rail explain bugfix        # the full states, transitions and gates
```

`types` is role-aware: a worker with `--item` sees only its own type. `next`
returns `{queue, items, cards, focus, scope}` for a CTO and one card for a
worker with `--item`. `next --all` ignores the queue scope, `next --into <id>`
overrides the focus for one call, and `next --focus-only` keeps only items
inside the focus. `explain` accepts a preset, a workflow or an action.

`model` is the one place the operating structure is spelled out: `unit`,
`hierarchy`, `focus`, `queue`, `progress`, `execution`, the presentation labels
and screens, and the semantic, presentation and full digests. Read it once
rather than inferring any of that from item names.

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
- **preset_label/unit** — the pack's word for the type and the canonical unit.
- **focus** — the container this item belongs to (the breadcrumb a worker needs).
- **actions** — every declared verb that applies to this type, state and actor.
- **next** — only these moves are legal; anything else is refused.
- **gate/proof** — what the move evaluates. `checks` are the selected checks;
  `acceptance` needs a fresh human acceptance check; `review` needs a separated
  review; `human` needs an explicitly named human; `children` needs every child
  done or dropped.
- **command** — the first legal next move, with the etag placeholder.

## Focus, queue and progress

- **Focus** is the container the project is working in: the active sprint, the
  current phase, the active scene. It is the container's own status, never an
  inference from its children. Move it into the active category with the pack's
  start action.
- `next` defaults to the focus-first queue. When no container matches, `--all`
  is the escape; when several match, the rail refuses with `AMBIGUOUS_FOCUS` and
  lists the candidates — pass `--into <id>` rather than guessing.
- `add` gives a unit (and a preset sharing its workflow) the focus as its
  `part_of`. An explicit `--rel part_of=...` always wins; an unrelated preset
  such as a risk or an asset stays unparented.
- `status` adds `unit`, `focus`, `groups`, `progress`, `queue` and `labels` to
  its counts. Workflow completion is counted from declared done-category
  states; execution progress comes from execution records and is never merged
  with it.
- The hierarchy orders the plan screen. Its `order` field is a frontmatter key
  such as `sequence`; a missing value sorts last, and the slug breaks every tie.

## Common verbs

The pack may ship verbs so the usual path is one call:

```powershell
rail do start-sprint --item sprint-3 --etag <etag>
rail do close-sprint --item sprint-3 --etag <etag>
rail do file-bug --set title="Checkout crash" --set parent=sprint-3
rail do triage-report --set report=01JREPORT0000000000000001
```

A verb runs one allowlisted gateway tool as you; it cannot change the actor, a
path, an etag or a profile. A refused verb names the missing condition and the
command that produces it. `rail types` lists the pack's verbs.

## Recovering from a refusal

1. Read `details.gate`, `details.missing` and `details.command`.
2. Run the command it names (`rail check ...`, `rail verify ...`,
   `rail review ...`) or fix the child it names.
3. `NO_FOCUS` means the pack needs a parent container: pass
   `--into <container>` or start one, or pass an explicit `--rel part_of=...`.
   `AMBIGUOUS_FOCUS` lists its candidates; choose one with `--into`.
   `INVALID_FOCUS` means the slug is not a declared hierarchy container.
4. Re-read `rail why <item>` before retrying. A stale verdict is not a pass.
5. If a move is still refused, `rail explain <workflow>` shows the full rule.

Do not edit the item's status by hand, and do not lower a gate: gates and the
charter are human decisions. Propose the change and stop.
