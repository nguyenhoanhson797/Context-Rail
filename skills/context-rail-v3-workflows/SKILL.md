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
| Folder layout | **Yes** | `rail.yaml` `placement` / `presets`, then `rail change repath` |
| Agent behaviour | **Yes** | Profile documents: prompt, methods, model, tools, skills |
| Evidence gates | **Yes, per project** | Charter: 4 gates × 4 rules |
| Primitives / relations / categories | **No** | 6 / 8 / 5, fixed by the engine |
| New automatic rules | **Needs engine code** | E.g. a per-transition evidence precondition |

## Built-in packs (named by workflow, not team size)

| Pack | Shape | Statuses | Extra presets |
|---|---|---|---|
| `basic` | one stream | `todo → doing → done` | `milestone`, `idea` |
| `flow` | continuous kanban | `backlog → ready → building → review → shipped` | `idea`, `story`, `epic` |
| `scrum` | sprints | `todo → ready → doing → review → done` | `backlog`, `epic`, `sprint`, `story` |
| `waterfall` | phase gates | `planned → designing → building → testing → done` | `requirement`, `phase`, `deliverable` |
| `video` | scenes and shots | `draft → storyboard → generating → review → approved → published` | `scene`, `shot`, `asset`, `review` |

Every pack also declares `blocked` and `dropped`; the table lists the flow the
team sees. All four also declare the shared base presets: `goal`, `scope`, `task`, `bug`,
`risk`, `question`, `note`, `convention`, `code`, `decision`, `event`, `check`,
`charter`, `vision`, `wont`, `skill`, `cto`, `profile` and `fix`.

- All four keep a `code` note preset for hand-written intent that the code graph cannot
  see (runtime coupling, configuration, why a module exists). Nobody has to write
  one: the code graph derives files, symbols and callers from git, and the
  implementer never writes the store. The source path lives in `source_path`
  because the engine reserves `path`. A store from before the graph moves over with
  `rail --actor human ops codegraph adopt`.

## The operating model sits beside the workflow

A v2 pack can add one `operating` block: the unit, the hierarchy, the focus
container, the queue and the progress population. It **selects and orders** the
workflow; it never changes a transition, a quality gate or an action. The two
are separate layers:

- `operating` says which sprint or scene is current, which presets `next`
  returns, and what counts as progress;
- `workflows` still say which moves are legal and what proof each move needs.

So a sprint's `start-sprint` action moves it into the active category (making it
the focus) by following the sprint workflow; the operating block only declares
that the active sprint is the focus. Do not encode a transition in `operating`,
and do not widen a gate from a presentation file.

`surface.yaml` is the third layer: labels, screens and card/filter/column
fields. A screen may pin one `preset` (or `presets`) and a `group_by`; all are
checked against the pack. It cannot add a preset, workflow, status or action,
and it is not approved.
- `small` and `indie` are deprecated aliases for `basic` and `flow`. A store
  that still says `pack: small` keeps working and keeps its approval;
  `rail ops pack list` shows the canonical pack and marks it active.
- There is no `agile` pack: agile is the umbrella, `flow` and `scrum` are its
  two shapes.
- Statuses alone do not gate execution: `ready` maps to `not_started` and
  `review` maps to `active`. A pack can still close a status with
  `execution: {review: {dispatch: false}}`, which `flow`, `scrum` and `video` do.
  The top-level `execution` block covers only the default workflow: a named
  workflow that uses the status must declare the same flag itself, or items on
  it stay dispatchable in review. Other hard gating comes from `depends_on`, the
  `blocked` category and the charter's done gate.

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
  placement, e.g. `items/story/<slug>.md`, unless a preset placement overrides
  it.
- A pack may also ship `layout.yaml` with initial `placement` and `presets`.
  `init`, onboarding and `migrate` seed `rail.yaml` from it; `rail.yaml` is
  authoritative afterwards, and `pack use` never moves files.
- The pack also ships `profiles/`, `skills/` and `templates/`. Those are a
  seed: `init` copies them into the store, so editing the pack later changes
  only new stores. `pack.yaml` is read live on every command.
- `pack use` refuses a target that cannot serve the store's manifest, so a
  switch can never leave every later command unable to read `rail.yaml`.

## Placement and required metadata

`rail.yaml` is the authority on where documents live:

```yaml
placement:                 # by primitive, the fallback
  item: "items/{preset}/{slug}.md"
  record: "records/{yyyy}-{mm}/{id}.json"
presets:                   # by preset, wins over the primitive
  sprint: "{part_of}/{slug}.md"
  task: "{part_of}/{field:area}/{slug}.md"
  result: "records/{about}/{id}.json"
  "kind:report.filed": "records/reports/{id}.json"
```

**Agents never pass a path, a parent folder or a final title.** They pass `--rel`
and `--set`; the resolver reads the work graph and derives the path:

```powershell
rail change add sprint "Sprint 3" --rel part_of=phase-1
rail change add task "Implement checkout" --rel part_of=sprint-3 --set area=frontend
rail change update implement-checkout --set area=backend --expect <etag>
```

- `{part_of}` nests a document under its parent's path; `{part_of.slug}` takes
  only the parent's slug as one segment. `{about}` and `{verifies}` do the same
  for a record and a check. A segment-only token is how you keep a flat layout.
- `{nearest:preset}` and `{root:preset}` walk `part_of`/`about`/`verifies` to
  the nearest or outermost ancestor of that preset.
- `{item}` and `{item.slug}`/`{item.title}` are only legal in a
  `kind:<kind>` record placement, because only a record knows its item.
  `{kind}` is record-only and places a record by its kind.
- A record must stay under `records/`; a document may not enter it. `skill`,
  `profile`, `cto` and `actor` are fixed to their `workspace/` shapes.
- A path-affecting edit moves the document and every descendant in one commit;
  records move with them and are never rewritten.

Presets may also require metadata:

```yaml
presets:
  task:
    required: [area]
    defaults: {area: general}
    fields: {area: [frontend, backend, general]}
```

`add` merges `defaults`, then refuses a missing or empty required field; `update`
revalidates the merged document. A default used by `{field:...}` must be a
non-empty string. Adding a required field to a live pack is a breaking change:
give it a default, or file fix items for the existing documents. Engine-written
presets (`event`, `check`, `note`, `goal`, `charter`, …) need a default for every
required field; `pack validate` reports one that does not.

For the full authoring guide, load `context-rail-v3-pack-authoring`.

## Validate and repair

```powershell
rail ops pack validate --name my-team     # read-only: presets, templates, placement
rail ops doctor                           # the store: paths and metadata
rail change repath --dry-run              # every move the placement implies
rail change repath                        # apply them in one commit
```

Doctor codes that mean a layout or metadata problem:

| Code | Severity | Meaning |
|---|---|---|
| `placement_mismatch` | warning | the file is not where the placement renders |
| `placement_unresolved` | error | no target for the relation the placement walks |
| `placement_ambiguous` | error | more than one target for that relation |
| `placement_cycle` | error | `part_of`/`about`/`verifies` loops |
| `placement_invalid` | error | an unsafe or unresolvable rendered path |
| `required_field_missing` / `required_field_invalid` | error | preset metadata is absent or outside its enum |
| `placement_historical` | warning | an existing record lost its anchor; it is never rewritten |
| `record_metadata_missing` / `record_metadata_invalid` | warning | an existing record's metadata is stale |

`repath` reports `moves` and `skipped`; a document with no resolvable anchor is
in `skipped` with its code, not silently ignored. `rename` moves the document
and retargets links; descendants follow through `repath`.

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
rail change add ba_pack "Checkout BA"
rail change add story "Checkout story" --rel depends_on=checkout-ba
rail read status
# "executable": ["checkout-ba"]
# "blocked": {"checkout-story": "depends_on:checkout-ba"}
```

When the BA item reaches `done`, the story becomes executable automatically.

**2. Workflows (vocabulary).** The example pack declares three named lifecycles
over one status dictionary: a story runs `intake → ba_review → po_review →
ready → doing → review → done`, the BA pack runs `intake → ba_review →
po_review → done`, and a bug runs `reported → fixing → verifying → closed`.
`move` refuses anything the item's own workflow does not allow:

```text
transition 'po_review' -> 'doing' is not allowed in workflow 'story';
allowed: ready, ba_review, dropped
```

The pack also ships verbs for the common path, so the flow is one call each:

```powershell
rail do send-to-ba  --item checkout-story --etag <etag>
rail do send-to-po  --item checkout-story --etag <etag>
rail do accept-po   --item checkout-story --etag <etag>
rail do start-story --item checkout-story --etag <etag>
```

**3. Evidence (charter gates).** A `check` with mode `human` is the PO
acceptance; `require_acceptance` / `require_human` on `item_done` means a story
cannot close on an empty, stale or unknown proof. The example's `ba` workflow
selects `po-acceptance` when it closes, and its `bugfix` workflow selects
`repro-check`; a missing check refuses and names the command that declares it.

**4. Prompt (soft).** The BA/PO profile prompts and skills shape how the agents
behave; they are guidance, not enforcement.

A full tested v4 pack is in `references/ba-po-pack.yaml`: three workflows,
explicit `container` on every item preset, per-workflow quality rules and six
declarative verbs. `rail ops pack validate --name scrum-ba` reports it valid, and
`rail types` / `rail explain story` / `rail explain accept-po` show it to an
agent without opening the YAML.

## Create, validate, switch

```powershell
rail ops pack create --name my-team --from scrum   # copies the whole pack tree
# edit packs/my-team/pack.yaml, profiles/, templates/
rail ops pack validate --name my-team
rail --actor human ops pack use --name my-team --expect <rail.yaml etag> \
              --map todo=intake --confirm "USE PACK my-team"
```

`ops pack use` is an approval-guarded migration: it rewrites statuses through the
map and refuses unmapped statuses, presets the target pack lacks, and a required
field the existing documents cannot satisfy. When two workflows share a status
name but need different targets, qualify the map entry with the source workflow:
`--map sprint:todo=doing --map bugfix:todo=fixing`; a plain entry still applies
to every item. Switching does **not** rewrite history. For the full authoring
loop — presets, required metadata, placement recipes and pitfalls — load
`context-rail-v3-pack-authoring`.

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
- Migration to a structural layout is refused: a legacy store has no work graph,
  so `ops migrate` cannot render `{part_of}` or a `kind:` placement. Migrate
  flat, then add the structure.
- Pack policy changes and `pack use` are approval-guarded.
