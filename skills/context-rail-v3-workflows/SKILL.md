---
name: context-rail-v3-workflows
description: 'Customize Context Rail v3 without touching the engine: pack statuses, transitions, presets, document types, profiles and charter gates, including a tested BA/PO sign-off workflow and how to switch packs. Load when asked whether a team can define its own docs system or process.'
---

# Context Rail v3 — custom workflows

Teams define the **vocabulary and the workflow**. The engine keeps a fixed
**core**. That split is what lets a light team and a gated team run the same
binary.

## The split

| Layer | Customizable | Where |
|---|---|---|
| Document types | **Yes** | Pack `presets`: primitive + tags + enum fields + template |
| Statuses | **Yes** | Any names, each mapped to one of the 5 fixed categories |
| Transitions | **Yes** | Pack `transitions`: allowed `status → [status]` moves |
| Folder layout | **Yes** | `rail.yaml` `placement` / `presets`, then `rail relocate` |
| Agent behaviour | **Yes** | Profile documents: prompt, methods, model, tools, skills |
| Evidence gates | **Yes, per project** | Charter: 4 gates × 4 rules |
| Primitives / relations / categories | **No** | 6 / 8 / 5, fixed by the engine |
| New automatic rules | **Needs engine code** | E.g. a per-transition evidence precondition |

## Built-in packs (named by workflow, not team size)

| Pack | Shape | Statuses | Extra presets |
|---|---|---|---|
| `basic` | one stream | `todo → doing → done` | `milestone`, `idea` |
| `flow` | continuous kanban | `backlog → ready → building → review → shipped` | `idea`, `story`, `epic` |
| `scrum` | sprints | `todo → ready → doing → review → done` | `epic`, `sprint`, `story` |
| `waterfall` | phase gates | `planned → designing → building → testing → done` | `requirement`, `phase`, `milestone` |

Every pack also declares `blocked` and `dropped`; the table lists the flow the
team sees. All four also declare the shared base presets: `goal`, `scope`, `task`, `bug`,
`risk`, `question`, `note`, `convention`, `code`, `decision`, `event`, `check`,
`charter`, `vision`, `wont`, `skill`, `cto`, `profile` and `fix`.

- All four ship a `code` note preset and a `code-map` skill. The implementer writes
  the notes in its candidate; the reviewer checks them against the diff. A code note
  names a source file, class, function or method; `depends_on` points at what it
  needs, so the notes pointing at a code note are its editing impact. The source
  path lives in `source_path` because the engine reserves `path`.
- `small` and `indie` are deprecated aliases for `basic` and `flow`. A store
  that still says `pack: small` keeps working and keeps its approval;
  `rail pack list` shows the canonical pack and marks it active.
- There is no `agile` pack: agile is the umbrella, `flow` and `scrum` are its
  two shapes.
- Statuses alone do not gate execution: `ready` maps to `not_started` and
  `review` maps to `active`. A pack can still close a status with
  `execution: {review: {dispatch: false}}`, which `flow` and `scrum` do. Other
  hard gating comes from `depends_on`, the `blocked` category and the charter's
  done gate.

## Pack anatomy (`packs/<name>/pack.yaml`)

Exact keys: `name`, `version`, `statuses`, `initial`, `transitions`,
`presets`, `policy`, and optional `execution`. Unknown keys are refused.

```yaml
name: my-team
version: 1
statuses: {intake: not_started, ba_review: not_started, ready: not_started,
           doing: active, blocked: blocked, done: done, dropped: dropped}
initial: intake
transitions:
  intake: [ba_review, dropped]
  ba_review: [ready, intake, dropped]
  ready: [doing, dropped]
  doing: [done, blocked, dropped]
  blocked: [doing, dropped]
  done: [doing]
  dropped: [intake]
execution:
  review: {dispatch: false}
presets:
  story: {primitive: item, tags: [story], fields: {priority: [must, should, could]}}
  note:  {primitive: note, tags: []}
  check: {primitive: check, tags: []}
policy: {stuck_hours: 24, retries: 1, measurement_min_runs: 10}
```

- Every pack must declare these presets, because the engine reads them by
  name: `charter`, `goal`, `vision`, `scope`, `wont`, `skill`, `cto`,
  `profile`, `decision` and `note`. `pack validate` reports a missing one.
- Every preset's `template` must exist in the pack. `pack validate` reports a
  missing file, and `add` refuses rather than inventing an empty body.
- A preset's `fields` are closed value lists, enforced on `add` and `update`.
  Undeclared keys stay free text: `acceptance` and `inputs` belong to no
  preset, and teams add their own.
- `execution` lets a status steer work, not just moves. `dispatch: false`
  removes an Item from `status.executable` and makes dispatch refuse;
  `merge: false` makes merge refuse. Omitted flags fall back to the category
  (workable → dispatchable, unfinished → mergeable). `flow` and `scrum` mark
  `review` as `dispatch: false`.
- `policy.retries` caps implementation attempts at one initial try plus that
  many retries, on top of the charter and profile bounds. `stuck_hours` and
  `measurement_min_runs` are the other live policy keys. Gates, budgets and
  decision rights live in the charter, not the pack.
- Tags are free text and never drive engine behaviour.
- New presets need no manifest entry: they render under the primitive's
  placement, e.g. `items/story/<slug>.md`.
- A pack may also ship `layout.yaml` with initial `placement` and `presets`.
  `init`, onboarding and `migrate` seed `rail.yaml` from it; `rail.yaml` is
  authoritative afterwards, and `pack use` never moves files.
- The pack also ships `profiles/`, `skills/` and `templates/`. Those are a
  seed: `init` copies them into the store, so editing the pack later changes
  only new stores. `pack.yaml` is read live on every command.
- `pack use` refuses a target that cannot serve the store's manifest, so a
  switch can never leave every later command unable to read `rail.yaml`.

## Profiles and skills

- A profile is a store document at `workspace/profiles/<name>/profile.md`.
- `recipe` must be one of `cto`, `implementer`, `reviewer`. `dispatch` accepts
  only an `implementer`; `review` only a `reviewer`; `auto: high` only the CTO.
- A BA role is a profile with `recipe: implementer` and a BA prompt; dispatch
  chooses it with `--profile ba` on BA items.
- Skills are Markdown notes with versions; `rollback` restores a retained one.

## Charter gates and rights

Four gates: `item_done`, `scope_done`, `merge`, `release`. Each has four
booleans: `require_checks`, `require_acceptance`, `require_review`,
`require_human`. Moving an item into a `done` category runs `item_done`, or
`scope_done` for a declared scope entry.

Three decision classes, enforced at the gateway: `cto-alone`,
`cto-reversible` (listed in the digest), `human-only` (needs a typed phrase).

## BA/PO at sprint start — three enforcement levels

**1. Dependency (hardest, no code).** A sign-off is an item; the story points at
it with `depends_on`. The engine excludes the story from executable work and
dispatch refuses it.

```powershell
rail add ba_pack "Checkout BA"
rail add story "Checkout story" --rel depends_on=checkout-ba
rail status
# "executable": ["checkout-ba"]
# "blocked": {"checkout-story": "depends_on:checkout-ba"}
```

When the BA item reaches `done`, the story becomes executable automatically.

**2. Transitions (vocabulary).** `intake → ba_review → po_review → ready →
doing → review → done`. `move` refuses anything else:

```text
transition 'intake' -> 'doing' is not allowed; allowed: ba_review, dropped
```

**3. Evidence (charter gates).** A `check` with mode `human` is the PO
acceptance; `require_acceptance` / `require_human` on `item_done` means a story
cannot close on an empty, stale or unknown proof. A gate never passes on an
empty proof set.

**4. Prompt (soft).** The BA/PO profile prompts and skills shape how the agents
behave; they are guidance, not enforcement.

A full tested pack is in `references/ba-po-pack.yaml`.

## Create, validate, switch

```powershell
rail pack create --name my-team --from scrum   # copies the whole pack tree
# edit packs/my-team/pack.yaml, profiles/, templates/
rail pack validate --name my-team
rail pack use --name my-team --expect <rail.yaml etag> \
              --map todo=intake --confirm "USE PACK my-team"
```

`pack use` is an approval-guarded migration: it rewrites statuses through the
map and refuses unmapped statuses or presets the target pack lacks. Switching
does **not** rewrite history.

## Gotchas

- `pack validate` checks the pack alone. The manifest also places presets
  (`decision`, `charter`, `goal`, `vision`, `scope`, `wont`, `skill`, `cto`,
  `profile` by default); a store-local pack must declare them or the next write
  fails with `preset placement for undeclared preset(s)`.
- One active pack per store. Different teams → different stores, or one
  superset pack where the extra steps are optional dependencies.
- Transitions are legality, not evidence. For proof before a status, use a
  dependency item or a gate check; a hard per-transition rule is an engine
  change.
- Pack policy changes and `pack use` are approval-guarded.
