---
name: context-rail-v3-pack-authoring
description: 'Author or adjust a Context Rail pack: presets, required metadata, defaults, declarative placement, records under records/, validation with pack validate, doctor and repath. Load when creating a new pack, adding document types, or changing where documents live.'
---

# Context Rail v3 — authoring a pack

A pack is the **project type**: it names the document types, the statuses and
the policy. `rail.yaml` then says **where** those documents live. You edit the
pack and the layout; you never edit engine code to add a document type.

## When to author a pack

- A new project type needs its own words (`story`, `sprint`, `requirement`).
- A new document type, field or enum value is needed.
- A metadata contract is needed: some documents must carry `area` or
  `component` before they can be written.
- The folder layout has to change.
- A new agent role, template or worker skill is needed.

If the project fits `basic`, `flow`, `scrum` or `waterfall`, copy one with
`rail ops pack create` and adjust it rather than starting from nothing.

## Pack anatomy

```text
packs/<name>/
  pack.yaml        # name, version, statuses, transitions, presets, policy, operating
  layout.yaml      # optional: initial placement + preset placements
  surface.yaml     # optional: labels, screens, card/filter/column fields
  profiles/        # cto.md, implementer.md, reviewer.md, ...
  skills/          # worker skills copied into the store's workspace
  templates/       # one body template per primitive or preset
```

`pack.yaml` is read live on every command. `profiles/`, `skills/`, `templates/`
and `layout.yaml` are a **seed**: `init` copies them into the store, so editing
the pack later only changes new stores. `rail.yaml` is authoritative once a
store exists; never edit a pack expecting an existing store to follow.

Unknown keys are refused, not ignored. A pack that fails to parse cannot run a
store.

## Preset contract

```yaml
presets:
  task:
    primitive: item          # item, note, record, check, actor, policy
    tags: [task]             # free text, never engine behaviour
    fields:                  # closed value lists, enforced on add and update
      area: [frontend, backend, infra, general]
      component: [checkout, auth, billing]
    required: [area, component]
    defaults:
      area: general
    template: templates/item.md
```

- `required` names must be unique, non-reserved and a plain field name.
  Reserved: `primitive`, `preset`, `title`, `status`, `tags`, `created`,
  `updated`, `digest`, `slug`, `path`, `id`, the relation names, and for a
  record also `kind`, `at`, `actor`, `data`.
- `defaults` may only name fields that are also `required`. A default is a JSON
  scalar (string, number, boolean); lists, mappings, null and dates are refused
  at pack load.
- A default used by `{field:...}` in a path must be a non-empty string, because
  it becomes a path segment.
- `add` merges defaults, then refuses a missing or empty required field.
  `update` revalidates the merged document, so a cleared field refuses too.
- A record preset validates its `data` mapping. A markdown record validates its
  frontmatter.
- The engine writes these presets itself, so a required field on one of them
  must have a default: `event`, `check`, `fix`, `note`, `goal`, `vision`,
  `scope`, `wont`, `charter`, `profile`, `cto`, `skill`. `pack validate`
  reports the omission; `pack use` refuses the target.

Every preset's `template` must exist in the pack or `add` refuses. Every pack
must declare `charter`, `cto`, `decision`, `goal`, `note`, `profile`, `scope`,
`skill`, `vision` and `wont`; the engine reads them by name.

## Workflows, quality and verbs

A pack may declare named workflows; a preset selects one with `workflow: <name>`.

```yaml
workflows:
  sprint:
    initial: todo
    transitions: {todo: [doing], doing: [review, done], review: [done], done: []}
    execution: {review: {dispatch: false}}
    quality:
      done: {gate: item_done}
  bugfix:
    initial: reported
    transitions: {reported: [fixing], fixing: [verifying], verifying: [closed], closed: []}
    quality:
      closed: {gate: bug_done, checks: [repro-check]}

presets:
  sprint: {primitive: item, tags: [sprint], container: true, workflow: sprint}
  bug: {primitive: item, tags: [bug], container: false, workflow: bugfix}
```

- `statuses` stays pack-wide: one name, one category. Top-level
  `initial`/`transitions`/`execution` are the `default` workflow; `workflows`
  may not declare `default`.
- `container: true` for a phase, sprint, epic, goal or scope (not a unit of
  work); `container: false` for a task, bug, story or fix; `null` keeps the old
  "has a child" inference.
- A workflow quality rule names a charter gate and optionally the checks it
  selects. Moving into that status evaluates it; a missing check refuses and
  names the command that produces it.
- Add a gate to the charter when a workflow names one; `pack validate` and
  `doctor` report an unknown gate.

Actions are declarative verbs over the pack:

```yaml
actions:
  start-sprint:
    on: sprint
    from: [todo, ready]
    tool: change move
    args: {slug: "{item}", status: doing, expect: "{etag}"}
    use_when: "a sprint is ready to start"
  close-sprint:
    on: sprint
    from: [doing, review]
    tool: change move
    args: {slug: "{item}", status: done, expect: "{etag}"}
    requires: {children: true}
    use_when: "every sprint child is finished"
```

One action is one allowlisted gateway tool: `add`, `update`, `link`, `move`,
`rename`, `check`, `triage`, `report` (write) or `next`/`status` (read). It may
not set `path`, `actor`, `key`, `profile` or `store`, call `do`, `cto tick`,
`pack use`, `relocate`, `merge` or `adapters sync`, and it runs with the caller's
permissions. `do` records one `action.executed`; read-only actions write nothing.

## The operating model (pack version 2)

Version 2 adds one optional `operating` block. It changes command behaviour
(scope, defaults, grouping and progress), so it is semantic and approved, never
a theme. A v1 pack keeps loading with a neutral model and may not declare it.

```yaml
version: 2
operating:
  version: 1
  unit: shot
  hierarchy:
    - {preset: scene, order: sequence, required: true}
  focus: {preset: scene, category: active, order: sequence, resolve: unique}
  queue: {presets: [shot], require: transition, focus: priority}
  progress: {group: scene, of: [shot], complete: {states: [approved]}}
  execution: {actions: [render]}
```

| Key | Contract |
|---|---|
| `unit` | an item preset; the default board and card |
| `hierarchy` | item presets with `container: true`, outermost first; `order` names a field or `sequence`/`created`/`updated`/`title`/`slug`; `required` makes a parent mandatory |
| `focus` | a hierarchy preset plus a fixed category; `resolve` is `unique` (default), `first`, `explicit` or `none` |
| `queue` | item presets, `require: transition` (default) or `action`, `focus: priority` (default) or `only` |
| `progress` | `group` is a hierarchy preset; `of` defaults to the unit; completion names `states` in every referenced workflow and in the `done` category, or `category: done`, or per-preset `presets` |
| `execution` | declared action names whose runs count as execution progress |

Everything is by name and validated: an unknown preset, workflow, status,
category, field or action is refused, as is a duplicate hierarchy preset, a
focus outside the hierarchy, or a completion state not in the `done` category.
The compiler is the only interpreter; commands and agents read `rail model`,
never this block.

`focus` is the container's own status. Give the pack a start action that moves
it into the active category:

```yaml
actions:
  start-scene:
    on: scene
    from: [draft]
    tool: change move
    args: {slug: "{item}", status: storyboard, expect: "{etag}"}
    use_when: "a scene is ready to work on"
```

`add` then defaults a unit's parent to the focus unless the caller passes an
explicit `--rel part_of=...`. A required hierarchy with no focus refuses with
`NO_FOCUS`; it never guesses.

## Presentation (`surface.yaml`)

Presentation is separate, optional and **not part of the approval**. A missing
file gives neutral defaults: title-cased preset labels and the standard screen
list.

```yaml
version: 1
labels:
  shot: {one: Shot, many: Shots}
  scene: {one: Scene, many: Scenes}
screens:
  - {id: board, kind: board, title: Shots, preset: shot, group_by: status}
  - {id: plan, kind: timeline, title: Scenes, preset: scene}
  - {id: runs, kind: queue, title: Render queue, preset: render}
  - {id: catalog, kind: catalog, title: Assets, presets: [asset, render]}
fields:
  card: [model, duration, status]
  filters: [scene, model, status]
  columns: [kind, model, duration]
```

Screen kinds: `summary`, `board`, `plan`, `timeline`, `workflow`, `execution`,
`explorer`, `queue`, `catalog`, `records`, `pack`. A screen may pin one
`preset` (or a `presets` list) and a `group_by`; all are checked against the
pack and against declared fields. Labels and fields may only name presets and
fields the pack declares. `surface.yaml` cannot introduce a preset, workflow,
status or action. The shell renders the screens in the pack's order and under
the pack's titles; an unknown kind or field is a `surface.yaml` validation
error, never a guess.

Semantic versus presentation, in one line: a label, screen or card change is
presentation and keeps the human approval; a unit, focus, queue, progress,
transition, preset or placement change is semantic and needs reapproval.

## Placement contract

`rail.yaml` holds two mappings:

```yaml
placement:                 # by primitive, the fallback
  item: "items/{preset}/{slug}.md"
  note: "notes/{preset}/{slug}.md"
  record: "records/{yyyy}-{mm}/{id}.json"
presets:                   # by preset, wins over the primitive
  sprint: "{part_of}/{slug}.md"
  task: "{part_of}/{field:area}/{slug}.md"
  result: "records/{about}/{id}.json"
  "kind:report.filed": "records/reports/{id}.json"
```

A pack's `layout.yaml` carries the same two mappings and only seeds `rail.yaml`
on `init`, onboarding and `migrate`.

### Tokens

| Token | Meaning | Scope |
|---|---|---|
| `{slug}` | the document slug | path segment |
| `{preset}` | the preset name | path segment |
| `{id}` | the record's ULID | record only |
| `{yyyy}` `{yy}` `{mm}` `{dd}` | the document or record timestamp | path segment |
| `{field:name}` | a required or defaulted field, slugified | path segment |
| `{kind}` | the record kind, slugified | record only |
| `{part_of}` | the `part_of` target's path | document graph |
| `{part_of.slug}` / `{part_of.title}` | the target's slug or title slug | one segment |
| `{about}` | the `about` target's path | record graph |
| `{about.slug}` / `{about.title}` | the target's slug or title slug | one segment |
| `{verifies}` | the `verifies` target's path | check graph |
| `{verifies.slug}` / `{verifies.title}` | the target's slug or title slug | one segment |
| `{nearest:preset}` | the nearest ancestor of that preset | path prefix |
| `{root:preset}` | the outermost ancestor of that preset | path prefix |
| `{item}` | the dispatch or run item | kind placement only |
| `{item.slug}` / `{item.title}` | the item's slug or title slug | kind placement only |

- A primitive placement must start with a literal segment; the primitive's
  folder is fixed by it. A preset placement may start with a literal or a
  **path** token such as `{part_of}`, which inherits the parent's folder. Every
  template that has tokens must contain `{slug}` or `{id}`. Two presets may not
  share a token-bearing template; a token-free template is a singleton and may
  be shared. This is how `charter.md` and `workspace/cto/profile.md` stay valid.
- `{item}` and `{item.*}` are only for a `kind:<kind>` record placement, because
  only a record knows its item at write time. `{kind}` is record-only.
- A record placement must stay under `records/`; a document placement must not
  enter it.
- `skill`, `profile`, `cto` and `actor` are fixed to
  `workspace/skills/{slug}.md`, `workspace/profiles/{slug}/profile.md`,
  `workspace/cto/profile.md` and `workspace/profiles/{slug}/profile.md`. Those
  paths are read by versioning, the doctor and adapter sync.
- Path segments are safe by construction: no `..`, no separators, no NUL, no
  Windows device names, at most 80 characters a segment and 200 the whole path.
  A value with `/` or `\` is slugified into one segment.

## Design recipes

**Sprint / phase nesting.** Each level carries `part_of` and every level uses
the parent's path:

```yaml
presets:
  phase: "items/phase/{slug}.md"
  sprint: "{part_of}/{slug}.md"
  task: "{part_of}/{slug}.md"
  result: "records/{about}/{id}.json"
```

A phase at `items/phase/phase-1.md` gives `items/phase/phase-1/sprint-3.md`,
then `items/phase/phase-1/sprint-3/implement-checkout.md`, and a result record
below the task.

**Flat per-parent folders.** When deep nesting gets unwieldy, take only the
parent's slug:

```yaml
presets:
  sprint: "sprints/{slug}/index.md"
  task: "tasks/{part_of.slug}/{slug}.md"
```

**Timeline.** Keep the date at the front and let the record kind choose the
folder:

```yaml
placement:
  record: "records/{yyyy}-{mm}/{id}.json"
presets:
  "kind:check.verdict": "records/{yyyy}-{mm}/checks/{id}.json"
```

**Area / component.** Require the field that becomes the folder, and default it
so older callers keep working:

```yaml
presets:
  task:
    required: [area]
    defaults: {area: general}
    fields: {area: [frontend, backend, general]}
```

## The rule for agents

Agents never pass a path, a parent folder or a final title. They pass `--rel`
and `--set`; the resolver derives the path:

```powershell
rail change add task "Implement checkout" --rel part_of=sprint-3 --set area=frontend
rail change update implement-checkout --set area=backend --expect <etag>
```

`{part_of.slug}` and `{part_of.title}` are segment tokens; the full `{part_of}`
is a path prefix. A slug is identity and survives a move; a title edit changes
only what the title segment renders.

## Validation loop

1. `rail ops pack create --name my-team --from scrum` — copy a working tree.
2. Edit `pack.yaml`, `templates/`, `profiles/` and `layout.yaml`.
3. `rail ops pack validate --name my-team` — a read-only report: missing
   required presets, missing templates, bad placement, engine presets without
   defaults, and every `pack.yaml` or `surface.yaml` operating/presentation
   problem with a fix hint.
4. `rail ops doctor` on a store using it — placement, metadata, duplicate slugs.
5. `rail change repath --dry-run` — every move the derived placement implies,
   plus the documents it had to skip.
6. `rail change repath` — apply them in one commit; records move but are never
   rewritten.
7. `rail --actor human ops pack use --name my-team --expect <etag> --map ...`
   — a guarded migration to the new pack.

Use `--dry-run` first every time. `repath` reports `moves` and `skipped`;
`skipped` entries carry a code and a message, so a document with no resolvable
anchor is visible rather than silently ignored.

## Pitfalls

- **Two parents.** A document with two targets for the relation its placement
  walks is ambiguous and refuses. Pick one parent, or use a relation the
  placement does not read.
- **Changing a path-affecting field.** Editing `area` moves the document and
  every descendant in one commit; records under it move too. That is intended.
  Expect the paths in old links to need a rename or repath.
- **A rename.** `rename` moves the document and retargets links, but a
  descendant tree follows only through `repath`. Run `repath --dry-run` after a
  structural rename.
- **Title as path.** Do not put `{title}` in a placement; a title edit would
  become a move. Put the stable identity in the slug and the rest in a field.
- **Records are history.** A record that loses its anchor is
  `placement_historical`: a warning, not an error, and never rewritten. A record
  that is missing required metadata is `record_metadata_missing`, also a
  warning. New records are still refused.
- **Shared record kinds.** `report.filed`, `run.completed`, `merge.completed`
  and friends are written with no single item. Keep their placement
  context-free; a `{part_of}` or `{item}` template would refuse those writes.
- **Adding a required field is a breaking change.** Existing documents fail
  `doctor` and the next `update`. Give it a default, or file fix items so the
  CTO updates the existing documents first.
- **Duplicate slug.** A slug is store-wide identity. Renaming to an existing
  slug refuses; `doctor` reports `duplicate_slug`.
- **Scoped skill copies.** `workspace/<pack>/skills/` copies are pack-shipped
  material: they are not moved and not reported as misplaced.
- **Windows names and length.** `CON`, `NUL`, `COM1` and friends are refused in
  a path; so is a segment over 80 characters or a path over 200. Keep folders
  short and derive them from short fields.
- **Migration.** `ops migrate` refuses a structural target: a legacy store has
  no work graph, so it cannot render `{part_of}` or `kind:` placements. Migrate
  into a flat layout first, then add the structure.

## Approval

Pack policy and operating changes, and `ops pack use`, are human-only
decisions. A CTO may propose a pack, but the human runs the guarded switch with
the typed phrase `USE PACK <name>`.

A semantic change (operating, transition, preset, placement, policy) changes the
semantic digest. `rail ops pack show <name>` reports `semantic_digest`,
`presentation_digest` and `full_digest` plus the approval state. When the
semantic digest moved, the store reports `approval_mismatch`; inspect and
reapprove:

```powershell
rail ops pack show my-team
rail --actor human approve pack:my-team --confirm "APPROVE pack:my-team" --confirmer <you>
```

A label, screen or card-field change touches only the presentation digest and
never needs reapproval.
