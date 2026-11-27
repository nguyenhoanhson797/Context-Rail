# Playbooks

Each playbook is a complete path from a starting state to a verifiable working
state. Commands are the documented surface; flat aliases also work.

Approval commands name their signer: run them as the named lead, or add
`--actor human` for a human signature. A charter with `approval.agents: false`
accepts only the human form. For `ops upgrade`, `ops adapters render` and
`approve`, an agent may instead pass the human's exact chat words as
`--user-approval` (and `--approved-by <human>` for the two operational
actions); the record marks the claim unverified.

## 1. Greenfield code project

```bash
rail init --pack basic
rail approve charter --confirm "APPROVE charter" --confirmer "Ada"
rail change add goal "Ship the first usable slice"
rail change add scope "The first slice" --rel part_of=ship-the-first-usable-slice --set priority=must
rail change add task "Prove the walking skeleton" --rel part_of=ship-the-first-usable-slice
rail change add task "Add the first vertical slice"
rail read status
rail overview
```

Done when `rail read status` lists an executable task and `rail next` returns
it.

Next: `context-rail-loop` for brief → result → candidate (when code changed) → review → report.

## 2. Existing codebase, no store

```bash
rail init --pack scrum
rail approve charter --confirm "APPROVE charter" --confirmer "Ada"
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
rail approve charter --confirm "APPROVE charter" --confirmer "Ada"
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
rail approve charter --confirm "APPROVE charter" --confirmer "Ada"
rail change add sprint "Sprint 1" --rel part_of=<goal>
rail do start-sprint --item sprint-1 --etag <etag>
rail change add story "Checkout BA sign-off" --rel part_of=sprint-1
rail change add story "Checkout story" --rel depends_on=checkout-ba-sign-off
rail read status
# checkout-ba-sign-off is executable; checkout-story is blocked on depends_on:checkout-ba-sign-off
```

When the sign-off item reaches done, the delivery story becomes executable. For
a full tested three-workflow pack with a dedicated `ba_pack` preset, see
`skills/context-rail-workflows/references/ba-po-pack.yaml`.

## 5. Parallel agents with review separation

One store, several sub-agents of your own tool, separate reviewer actors. Each
sub-agent works in its own git worktree.

```bash
rail loop brief task-a --role implementer        # give this to sub-agent A
rail loop brief task-b --role implementer        # and this to sub-agent B
rail --actor dev loop candidate task-a --commit <sha-a> --key cand-a
rail --actor qa  loop review task-a --commit <sha-a> --verdict approve --key review-a
rail --actor cto change move task-a done --expect <etag-a>
```

Rules:

- The implementer cannot review its own work; use a distinct reviewer profile
  and actor.
- The rail does not retry or merge. A failed candidate is a new commit and a
  new `loop candidate`; you merge in git.
- Candidate, review and move are individually gated and can overlap across
  items.

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
rail ops repair
rail ops repair --fix
```

Then read `rail read status` and `rail why <slug>`. Never delete the store to
fix a view; the index is the disposable part. If approval drifted, inspect the
semantic digest and reapprove deliberately:

```bash
rail ops pack show --name <pack>
rail approve pack:<pack> --confirm "APPROVE pack:<pack>" --confirmer "Ada"
rail approve charter --confirm "APPROVE charter" --confirmer "Ada"
```

## 10. Upgrading a built-in pack

Shipped packs are version 2 and carry an operating block, so their semantic
digest changed. A store on a package built-in reports `approval_mismatch` after
an upgrade.

```bash
rail ops pack show --name scrum
rail approve pack:scrum --confirm "APPROVE pack:scrum" --confirmer "Ada"
rail approve charter --confirm "APPROVE charter" --confirmer "Ada"
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

`video` declares scenes, shots, assets and reviews; render attempts are recorded
as execution records on the shot rather than separate items.

```bash
rail init --pack video
rail change add scene "Opening" --set sequence=1
rail do start-scene --item opening --etag <etag>
rail change add shot "Establishing shot" --rel part_of=opening --set model=hero
rail loop execution establishing-shot --action render --state running
rail loop execution establishing-shot --action render --state succeeded --output renders/opening-01.mp4
rail read show establishing-shot
```

The operating model's focus is the active scene; `next` is focus-first and the
execution screen counts render records separately from workflow completion.

## 14. Multi-project portfolio

Coordinate independent repositories from one `portfolio` store while each child
repository keeps its own store and pack (`scrum`, `flow`, `basic`, ...). For one
local lead, `agent enable` replaces the grant/open/export steps. Use
`--session-env` when several agents or remote environments hold separate tokens.

```bash
rail init --dir portfolio --pack portfolio
rail --store portfolio/context-rail agent enable lead-1 --role portfolio-lead --anchor project
rail --store portfolio/context-rail change add component "Billing API" --set store=../billing/context-rail
rail --store portfolio/context-rail do start-component --item billing-api --etag <etag>
rail --store portfolio/context-rail change add slice "Invoice webhook" --rel part_of=billing-api
rail --store portfolio/context-rail seat grant billing-cto --role component-cto --anchor billing-api
rail --store portfolio/context-rail seat open billing-cto --print-env   # export as RAIL_SESSION_PORTFOLIO
rail --store portfolio/context-rail --session-env RAIL_SESSION_PORTFOLIO next
```

Each slice uses a `store` Check (`mode: "store"`) against the child store's
committed item category; `portfolio-lead` verifies slices and ships releases.
Read `rail --store portfolio/context-rail read digest` for waits and usage,
and `rail --store portfolio/context-rail ops measure usage --group-by item` for
effort per slice or component.

## 15. Tracking effort and steering a change

Record what the tool reported; the rail never measures it.

```bash
rail loop candidate <item> --commit <sha> --model <name> --harness <tool> \
  --duration 5400 --usage tokens=152000 --usage requests=42
rail ops measure usage --group-by item|model|harness|role|profile|skill
rail ops measure versions --target-class skill --target workspace/skills/<name>.md \
  --before 1 --after 2
```

Unknown counters stay `unknown`, money is not a counter, and a budget only warns.
To steer, put the measurement or comparison in `rail loop report` and let the
human decide. `rail ops rollback` restores an earlier version as a new one when
the target and delegation allow; a protected target needs the human. Nothing is
applied or rolled back automatically.

## 16. Critical work that needs a human's consent

Some packs gate one action on the human's explicit go-ahead, in their own
words. Ask first; never infer or paraphrase the answer.

```bash
rail read show <item>                                  # the version the human will see
rail consent <item> --on-behalf-of "Ada" \
  --text "Yes, rotate the key after the backup lands." --expect <etag>
rail do approve-critical --item <item> --etag <etag>
```

The consent record names the agent that recorded it and the human it came
from, and is bound to that item version: an edit needs a new answer. The Item
sheet in `rail serve ui` records the same consent and can run the action; the
Health page signs the charter. A charter with `approval.agents: false` keeps
consent human-only.

## 17. Upgrading an existing store

`rail ops upgrade` rewrites an older store in place, then refreshes shipped
profiles and skills the store never edited. Check the plan first; the dry run
writes nothing and needs no identity.

```bash
rail ops upgrade --dry-run
rail ops upgrade --confirm "UPGRADE store"
rail approve charter --confirm "APPROVE charter" --confirmer "<you>"
rail ops adapters render
```

A name-mode store runs the write as `rail --actor human ops upgrade ...`. A
role-mode store acts by seat. The human can run `rail ops upgrade` in a terminal
or the local UI with no `--actor`; an approved charter can delegate `ops upgrade`
to the live lead; or the agent can carry the human's chat approval:

```bash
rail ops upgrade --confirm "UPGRADE store" \
  --approved-by "<you>" --user-approval "<your exact words from chat>"
```

To upgrade every store under the current project, or under an explicit root,
plan the batch first:

```bash
rail ops upgrade --all --dry-run
rail ops upgrade --all --root <folder> --confirm "UPGRADE store"
```

The table has one row per store. A store whose plan needs a charter approval or
a conflict repair is reported and left untouched; the other stores are upgraded
and their adapters rendered in the same run. The same `--confirm` phrase and
chat words apply to the whole batch. `rail ops upgrade ... --text` keeps the
JSON on stdout and prints the plain summary on stderr.

Ask the human in chat first and quote their answer. The upgrade record names the
human, the words and the agent that relayed them, and marks the claim
unverified: it is evidence, not proof that the human typed the command. The same
path works for `rail approve charter ... --user-approval "<words>"` and
`rail ops adapters render --approved-by "<you>" --user-approval "<words>"`. A
charter with `approval.agents: false` still requires a human signature for
`approve`. `rail ops pack show --name <pack>` tells the two modes apart: a
non-empty `operating.roles` means role mode.

The upgrade may rewrite a local `packs/<name>/pack.yaml`, which is part of the
protected picture. That makes the old approval a mismatch even when the charter
text itself did not change; `rail overview` names the changed paths under
"changed since approval". Reapprove the charter, render the agent files, and
only then run the stale `rail check run` commands the doctor reports.
