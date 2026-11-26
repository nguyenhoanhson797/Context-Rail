---
name: context-rail-v3
description: 'Context Rail v3 in one page: the store/index model, quick start, command families and which follow-up skill to load for workflows, the agent loop or operations. Load this first when a project has a context-rail/ store or when asked what the rail can do.'
---

# Context Rail v3 — start here

Three sentences explain most of the product:

1. **Files are the truth.** A project is a `context-rail/` folder of Markdown
   documents plus `rail.yaml`, committed to git and editable by hand.
2. **The index is a cache.** SQLite mirrors those files so the views are fast
   at ten thousand documents; delete it and the next command rebuilds it.
3. **One write path.** Every change — CLI, MCP or web UI — is validated,
   authorized, locked, written atomically, recorded and indexed. Nothing else
   mutates the store.

## The map

```text
CLI / MCP / UI
      ↓
  Gateway  (validate → authorize → one JSON Result)
      ↓
  Kernel (pure rules)        Recipes (brief, status, digest)
      ↓                            ↓
  Write pipeline            Query layer (SQL only)
      ↓                            ↓
  files (truth)             SQLite index (cache)      Checks (cmd checks, git reads)
```

Fixed vocabulary: **6 primitives** (`item`, `note`, `record`, `check`,
`actor`, `policy`), **8 relations** (`part_of`, `depends_on`, `verifies`,
`about`, `because_of`, `supersedes`, `resolves`, `owned_by`), **5 categories**
(`not_started`, `active`, `blocked`, `done`, `dropped`). Packs translate their
own words into these; the engine never reads a status name or a tag.

## 60-second start (verified)

```powershell
rail init --pack basic --scaffold      # skeleton only for this quick tour
rail change add goal "Ship v3"
rail change add task "Write the first task" --rel part_of=ship-v3
rail read status                       # executable: write-the-first-task
rail overview                          # the CTO's bounded starting page
```

Drop `--scaffold` to run the resumable onboarding pipeline instead: it offers
the picture, approval, agent files and optional git drivers.

Global options go **before** the command (`rail --store path read status`).
`change add` takes positional arguments: `rail change add <preset> "<title>"`.
Exit codes: `0` success, `1` refusal, `2` infrastructure failure.

## Role mode and seats

A pack's version-2 `operating` block may declare roles: a lead on the project,
a level lead per sprint/scene/phase, an implementer and a reviewer. A seat binds
one agent to one role on one anchor. Roles live in the approved pack, so
changing one needs re-approval.

```powershell
rail seat list
rail seat grant sprint-1 --role sprint-lead --anchor sprint-3
rail seat open sprint-1 --print-env    # prints RAIL_SESSION for that seat's agent
rail sprint-1 next                      # read the store as that seat
rail seat close sprint-1
```

- In role mode the session token is the identity and `--actor` is refused.
  `rail seat open <seat> --print-env` prints the one-time token; export it as
  `RAIL_SESSION` in the agent's environment.
- A seat sees its anchor's subtree, the ancestors and shared notes its role
  declares. Reads and writes outside that reach are refused; lists and digests
  report `omitted_outside_reach`.
- A lens (`rail <seat> ...`) is read-only unless the session belongs to that
  seat, and never widens the caller's reach.
- Human-only actions need the human on a terminal or the UI channel, never an
  agent session — unless the approved charter names the caller's lead seat in
  `delegation` and lists that exact action. Then the lead may run it, and a
  `delegation.used` record names the delegate and the human. The first seat grant
  when no seat exists is human-only; after that a seat grants only inside its
  `can.grant` and write reach. The human sets the block with `rail ops delegate
  <seat> --action <id> --expect <charter-etag> --confirm "DELEGATE <seat>"`.
  `--force`, `ops pack use`, `ops migrate` and `init` stay human: they replace
  or create the boundary itself.
- A pack without roles is compat mode: the name-based `--actor` behaviour is
  unchanged and the seat commands refuse with the `ops pack use` hint.

## Onboarding a project — what each step generates

| Step | Command | Writes |
|---|---|---|
| Onboarding pipeline | `rail init --pack basic` | skeleton, pack profiles and skills, the offered picture, the fresh approval, agent files and optional git drivers |
| Skeleton only | `rail init --pack basic --scaffold` | `rail.yaml`, `.gitignore`, empty pinned folders |
| Picture source | `rail init --interview` **or** `rail init --answers-file file.json --confirm "APPROVE charter" [--confirmer "<you>"]` (human) **or** `rail init --draft-file file.json` (CTO) | goal, vision, Won't, scope docs, `charter.md`, the 3 profiles, the engine skills and the pack's skills (the store's own copies); the answers-file path also renders agent files and git drivers |
| Approve | `rail approve charter --confirm "APPROVE charter" --confirmer "<you>"` (a named lead; `--actor human` for a human signature) | an approval record with the protected-file hashes and its signer |
| Agent files | `rail ops adapters render [--harness agents\|claude\|cursor\|gemini]` | `AGENTS.md` (managed block: read-first table, write protocol, a "Start here" pointer and a skills index without bodies) and `.agents/skills/<name>/SKILL.md` per skill |
| Git drivers | `rail ops sync install` | `.gitattributes` and clone-local merge drivers |

- Bare `init` is resumable: it keeps what exists and continues from the next
  missing stage. `--dry-run` prints the plan and writes nothing.
- `--scaffold` preserves the old skeleton-only behavior. The picture modes
  write the picture. An answers-file init also renders the agent files and
  installs the git drivers, and records the charter approval when `--confirmer`
  is given. The interview leaves approval `missing`; run `rail approve` or
  `rail --actor human init` again to resume the remaining stages.
- The picture modes need the `human` actor. A CTO may only propose with
  `--draft-file`, and a draft records no approval.
- `ops adapters render` is what makes the checkout agent-ready. Re-run it when a
  skill changes; it also deletes the unedited copy of a skill the store has
  retired. `--force` replaces a hand-edited block (human only, typed phrase
  `FORCE ADAPTERS adapters`).
- Every agent session starts with `rail overview` (coordinator) or
  `rail loop brief <item> --role <role>`. The brief's role section names the role
  ("You are acting as: <role>"), its profile rules and its skills.
- An existing store keeps its copies of the shipped profiles and skills until a
  human runs `rail ops upgrade`: unedited copies are refreshed, new skills are
  seeded, retired copies are removed, and customised copies are reported, not
  overwritten.

## Read the project in three commands

| Command | Answers |
|---|---|
| `rail overview` | The daily starting page: approval, goal, gates, where we are, executable work, waits |
| `rail read map` | The whole picture: focus path, progress, next, and the work tree with markers and rollups |
| `rail read status` | Counts, executable items, blockers with reasons, stuck items, focus, groups, progress |
| `rail read digest` | Activity, human waits, reversible decisions, quality, usage |
| `rail ops measure usage --group-by item` | Recorded effort by item, model, harness, role, profile or skill; unknown stays apart |
| `rail model` | The compiled operating model, focus, unit, hierarchy and digests |
| `rail types` | Pack document types, workflows and the operating role of each |

## The project map: where am I in the whole picture

`rail read map` is the tree a person sees on the Map screen, as bounded text
plus JSON. Nodes are items placed with `part_of`; notes, checks and titled
records attached to an item are counted on it. Each node carries its kind,
status, a done/total rollup and markers: `FOCUS`, `PATH` (leads to the focus),
`NEXT`, `BLOCKED`, `CHANGED`. The pack decides the levels and the progress
rule; the map only reads the compiled model.

| Question | Command |
|---|---|
| Where are we and what is next? | `rail read map` (also the "Where we are" section of `rail overview`) |
| What is inside this branch? | `rail read map <slug>`: its path, `Etag:`, needs and needed-by, attachments, siblings and subtree |
| Where is X? | `rail read map --find "<words>"`: every hit, with the branch it sits in |
| What moved since I last looked? | `rail read map --since <utc>`: changed nodes marked and listed |
| More or less detail | `--depth N` (0-12) and `--max-chars N` |

Daily ritual:

1. `rail overview`: approval, waits and where we are.
2. `rail read map`: the whole tree; pick the branch that holds your work.
3. `rail read map <branch>`: read the branch before you change anything in it.
4. Do the work through the loop (`context-rail-v3-loop`).
5. Next session: `rail read map --since <the UTC time you last read it>`.

Update the roadmap through the normal write path:

- Add under a branch: `rail change add <preset> "<title>" --rel part_of=<parent>`.
- Change a status: `rail change move <slug> <status> --expect <etag>`.
- Record an order: `rail change link <slug> depends_on <other> --expect <etag>`.
- Move a whole branch to a new parent:
  1. `rail read map`: read the top of the tree.
  2. `rail read map <candidate>`: descend into the branch that should hold it,
     and repeat on its children until you reach the right parent.
  3. `rail read map <branch>`: read the branch you will move; note its `Etag:`
     line and what sits below it.
  4. `rail change link <branch> part_of <new-parent> --replace --expect <etag> --reason "<why>"`.
  5. `rail read map <new-parent>`: confirm it landed.

`--replace` makes the new parent the only parent. Every descendant moves with
the branch in one write, with one `doc.relocated` record. The engine refuses a
cycle (`CYCLE_DETECTED`), a parent the pack does not accept, a stale etag
(`CONFLICT`) and, in role mode, a parent you cannot write
(`write_out_of_reach`). Never move files by hand to restructure work.

## Track and steer

`rail read digest` and `rail ops measure usage` show recorded effort;
`rail ops measure versions` compares two versions of a skill or profile.
`rail ops rollback` restores an earlier version as a new one when the target and
delegation allow. Measurement never applies or rolls back a change by itself:
put the evidence in `rail loop report` and let the human decide.

## Five command families

| Family | Commands |
|---|---|
| Read | `overview`, `read status\|map\|digest\|find\|show\|ledger` |
| Change | `change add\|update\|link\|move\|rename\|repath`, `check declare\|run`, `loop report\|triage\|execution` |
| Gate | `approve`, `consent`, `ops pack use`, `gate` (typed phrase: `APPROVE <target>`, `USE PACK <name>`) |
| Loop | `loop brief\|result\|candidate\|review\|report\|triage\|execution` |
| Operate | `ops doctor\|reindex\|migrate\|rollback`, `ops pack`, `ops sync`, `ops adapters`, `ops measure`, `serve mcp\|ui\|daemon` |

`consent` records a human's explicit go-ahead for one item, in their own words,
bound to the version they saw. Ask first; never infer it. A pack action may
declare `requires: {consent: true}` and refuses without a matching record; an
edit needs a new answer. The Item sheet in `rail serve ui` records it, and
Health signs the charter.

Exact flags: `references/commands.md` in this skill.

## Pick the next skill

| You need | Load |
|---|---|
| Adopt the rail in a new project, or wire a harness | `context-rail-v3-onboarding` |
| Custom document types, statuses, transitions, BA/PO steps, pack switching | `context-rail-v3-workflows` |
| Create a pack, add document types, require metadata, change placement | `context-rail-v3-pack-authoring` |
| Work from the compiled surface: types, next, show, explain, why | `context-rail-v3-surface` |
| Hand out and record agent work: brief → result → candidate (code) → verify → review → report | `context-rail-v3-loop` |
| Doctor, reindex, recovery, git sync, budgets, cost and steering | `context-rail-v3-ops` |
| Every flag for every command | `references/commands.md` |

## First-run refusals

| Message | Fix |
|---|---|
| `NOT_FOUND: no store found` | `rail init`, or put `--store` before the command |
| `VALIDATION_FAILED: unrecognized arguments` | Global options belong before the command; `add` is positional |
| “the charter is not approved; nothing executes yet” | `rail approve charter --confirm "APPROVE charter" --confirmer "<you>"`; add `--actor human` when the charter requires a human signature |
| “the item has no consent for its current version” | Ask the human, then record their exact words: `rail consent <item> --on-behalf-of "<you>" --text "<their words>" --expect <etag>`; an edit needs a new answer |
| `actor_required` on a write | Name yourself: `--actor <name>` or `RAIL_ACTOR`; an unnamed caller can only read |
| `profile key 'model' was removed; run rail ops upgrade` | The store predates the runtime-neutral engine; a human runs it (`--actor human ops upgrade --confirm "UPGRADE store"` in a name-mode store; `ops upgrade --confirm "UPGRADE store"` with no `--actor`, in a terminal or the local UI, in a role-mode store) |
| `AMBIGUOUS_FOCUS` | Two containers match the focus; pass `--into <id>` or finish one |
| `NO_FOCUS` on `add` | The pack needs a parent; start the container or pass `--rel part_of=<container>` |
| `approval_mismatch` after a pack upgrade | The semantic digest changed; reapprove `pack:<name>` then the charter |
| `SESSION_REQUIRED` in a role-mode store | Open a session: `rail seat open <seat> --print-env`, then export `RAIL_SESSION` |
| `--actor` is refused in a role-mode store | The seat is the identity; use `RAIL_SESSION` or a terminal |
| A check is `stale` | Re-run `rail check run`; a stale pass is not a pass |

## Invariants worth knowing before changing anything

- Store writes go through `context_rail.store.write.commit`.
- Reads never execute a command and never hash a source tree.
- `context_rail.kernel` is pure: no file, git, process or index access.
- Unknown manifest schema, pack version, key or policy field is refused.
- One write is one record, with an idempotency key and an expected revision.
