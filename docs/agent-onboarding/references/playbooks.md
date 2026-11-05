# Playbooks

Each playbook is a complete path from a starting state to a verifiable working
state. Commands are the documented surface; flat aliases also work.

## 1. Greenfield code project

```bash
rail init --pack basic
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Ada"
rail change add goal "Ship the first usable slice"
rail change add scope "The first slice" --rel part_of=ship-the-first-usable-slice --set priority=must
rail change add task "Prove the walking skeleton" --rel part_of=ship-the-first-usable-slice
rail change add task "Add the first vertical slice"
rail read status
rail overview
```

Done when `rail read status` lists an executable task and `rail next` returns
it.

Next: `context-rail-v3-loop` for dispatch → review → merge.

## 2. Existing codebase, no store

```bash
rail init --pack scrum
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Ada"
rail change add goal "Stabilise the existing product"
rail change add task "Map the current entry points" --rel part_of=stabilise-the-existing-product
rail change add task "Add a smoke check" --rel part_of=stabilise-the-existing-product
rail check declare smoke --target add-a-smoke-check --spec-file smoke.json --expect absent
rail check run add-a-smoke-check
rail ops doctor
```

Start with a map and a proof, not a rewrite. The `code` note preset records
files, modules, classes, functions and methods; `depends_on` on a code note is
its editing impact.

## 3. Docs-only, research or operations project

The rail does not require code. Use `flow` for a continuous stream or `basic`
for one stream.

```bash
rail init --pack flow
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Ada"
rail change add goal "Publish the handbook"
rail change add epic "Part one" --rel part_of=publish-the-handbook
rail change add story "Draft chapter one" --rel part_of=part-one
rail change add note "Audience and tone" --rel part_of=publish-the-handbook
rail next
```

The same gates apply: a story can require a review and an acceptance check
before it closes.

## 4. Sprint team with BA/PO sign-off

Use `scrum` and model sign-off as a dependency item; the engine then excludes
the story until the sign-off is done. The built-in `scrum` pack has no
`ba_pack` preset, so a sign-off is a `story` that the delivery story depends on.

```bash
rail init --pack scrum
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Ada"
rail change add sprint "Sprint 1" --rel part_of=<goal>
rail do start-sprint --item sprint-1 --etag <etag>
rail change add story "Checkout BA sign-off" --rel part_of=sprint-1
rail change add story "Checkout story" --rel depends_on=checkout-ba-sign-off
rail read status
# checkout-ba-sign-off is executable; checkout-story is blocked on depends_on:checkout-ba-sign-off
```

When the sign-off item reaches done, the delivery story becomes executable. For
a full tested three-workflow pack with a dedicated `ba_pack` preset, see
`skills/context-rail-v3-workflows/references/ba-po-pack.yaml`.

## 5. Parallel agents with review separation

One store, one dispatcher, separate reviewer actors.

```bash
rail --actor cto loop dispatch task-a --expect <etag-a> --key run-a
rail --actor cto loop dispatch task-b --expect <etag-b> --key run-b
rail --actor reviewer loop review task-a --run <run-a> --key review-a
rail --actor cto loop merge task-a --run <run-a> --expect <etag-a> --key merge-a
```

Rules:

- The implementer cannot review its own work; use a distinct reviewer profile
  and actor.
- A failed run gets one retry by default; then it escalates with evidence.
- A merge conflict gets one rebase retry; then it escalates.
- Only `loop tick` is serialized behind the single-instance lock; dispatch,
  review and merge are individually gated but can overlap across items.

## 6. Agency or client workspace

One store per client, one pack per delivery shape.

```bash
rail init --dir clients/acme --pack flow
rail init --dir clients/globex --pack waterfall
```

Do not put two clients in one store: a slug is store-wide identity and one pack
is active per store. If a team needs a shared method and different lifecycles,
make one superset pack with named workflows.

## 7. Migrating a legacy v1/v2 store

`ops migrate` is human-only. It imports into a **fresh** store and refuses a
structural target: a legacy store has no work graph, so it cannot render
`{part_of}` or a `kind:` placement.

```bash
rail --actor human ops migrate --source old/store --dir . --pack basic --dry-run
rail --actor human ops migrate --source old/store --dir . --pack basic
rail ops doctor
rail change repath --dry-run
```

Migrate into a flat layout first, verify, then add structure.

## 8. CI gate

`rail gate` is read-only and never runs a Check. It reports doctor issues,
approval drift and open done gates.

```bash
rail ops doctor
rail gate --warnings-as-errors
rail ops reindex --full       # if reads disagree with files
```

A check is fresh only while its declared inputs match. Do not make CI run
`check run` implicitly unless the check is deterministic and has no side
effects.

## 9. Recovering a broken or confusing store

In order:

```bash
rail ops doctor
rail ops reindex
rail ops doctor --repair
rail ops doctor --fix
```

Then read `rail read status` and `rail why <slug>`. Never delete the store to
fix a view; the index is the disposable part. If approval drifted, inspect the
semantic digest and reapprove deliberately:

```bash
rail ops pack show --name <pack>
rail --actor human approve pack:<pack> --confirm "APPROVE pack:<pack>" --confirmer "Ada"
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Ada"
```

## 10. Upgrading a built-in pack

Shipped packs are version 2 and carry an operating block, so their semantic
digest changed. A store on a package built-in reports `approval_mismatch` after
an upgrade.

```bash
rail ops pack show --name scrum
rail --actor human approve pack:scrum --confirm "APPROVE pack:scrum" --confirmer "Ada"
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Ada"
```

A v1 local pack needs no change: it loads with the neutral model and keeps its
approval. No documents or paths migrate.

## 11. Changing the workflow without forking the engine

```bash
rail ops pack create --name my-team --from scrum
# edit context-rail/packs/my-team/pack.yaml, templates/, profiles/, layout.yaml, surface.yaml
rail ops pack validate --name my-team
rail ops doctor
rail change repath --dry-run
rail --actor human ops pack use --name my-team --expect <rail.yaml etag> \
     --map todo=intake --confirm "USE PACK my-team"
```

A label or screen change is presentation and keeps approval. An operating,
transition, preset or placement change is semantic and needs reapproval.

## 12. Personal knowledge base

Use `basic`, keep the hierarchy flat, and use `note` for durable knowledge.

```bash
rail init --pack basic
rail change add goal "Keep the reading notes useful"
rail change add note "Source list" --rel part_of=keep-the-reading-notes-useful
rail change add note "Chapter 1" --rel part_of=keep-the-reading-notes-useful
rail read find --preset note --text chapter
```

Records preserve the history; notes preserve the meaning.

## 13. Content or video pipeline

`video` declares scenes, shots, renders, assets and reviews.

```bash
rail init --pack video
rail change add scene "Opening" --set sequence=1
rail do start-scene --item opening --etag <etag>
rail change add shot "Establishing shot" --rel part_of=opening --set model=hero
rail change add render "Establishing render" --rel about=establishing-shot
rail read show establishing-shot
```

The operating model's focus is the active scene; `next` is focus-first and the
execution screen counts render records separately from workflow completion.
