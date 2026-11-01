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
  files (truth)             SQLite index (cache)      Runner (droid, cmd checks)
```

Fixed vocabulary: **6 primitives** (`item`, `note`, `record`, `check`,
`actor`, `policy`), **8 relations** (`part_of`, `depends_on`, `verifies`,
`about`, `because_of`, `supersedes`, `resolves`, `owned_by`), **5 categories**
(`not_started`, `active`, `blocked`, `done`, `dropped`). Packs translate their
own words into these; the engine never reads a status name or a tag.

## 60-second start (verified)

```powershell
rail init --pack basic                 # creates ./context-rail/
rail add goal "Ship v3"
rail add task "Write the first task" --rel part_of=ship-v3
rail status                            # executable: write-the-first-task
rail cto brief                         # the CTO's bounded starting page
```

Global options go **before** the command (`rail --store path status`).
`add` takes positional arguments: `rail add <preset> "<title>"`.
Exit codes: `0` success, `1` refusal, `2` infrastructure failure.

## Onboarding a project — what each step generates

| Step | Command | Writes |
|---|---|---|
| Skeleton | `rail init --pack basic` | `rail.yaml`, `.gitignore`, empty pinned folders |
| Picture | `rail init --interview` **or** `rail init --answers-file file.json --confirm "APPROVE charter"` (human) **or** `rail init --draft-file file.json` (CTO) | goal, vision, Won't, scope docs, `charter.md`, the 3 profiles, the pack's worker skills |
| Approve | `rail approve charter --confirm "APPROVE charter" --confirmer "<you>"` | an approval record with the protected-file hashes |
| Agent files | `rail adapters sync [--harness agents\|claude\|cursor\|gemini]` | `AGENTS.md` (managed block) and `.agents/skills/<name>/SKILL.md` per skill note |
| Git drivers | `rail sync --install` | `.gitattributes` and clone-local merge drivers |

- Bare `init` is a skeleton: **no charter, no profiles, no skills, no
  `AGENTS.md`**. The picture modes add them; approval stays `missing` until the
  human approves.
- The picture modes need the `human` actor. A CTO may only propose with
  `--draft-file`, and a draft records no approval.
- `adapters sync` is what makes the checkout agent-ready. Re-run it when a
  skill note changes; `--force` replaces a hand-edited block (human only,
  typed phrase `FORCE ADAPTERS adapters`).

## Read the project in three commands

| Command | Answers |
|---|---|
| `rail status` | Counts, executable items, blockers with reasons, stuck items |
| `rail cto brief` | Approval state, goal, gates, executable work, waits, quality |
| `rail digest` | Activity, human waits, reversible decisions, quality, usage |

## Five command families

| Family | Commands |
|---|---|
| Read | `status`, `digest`, `cto brief`, `find`, `show`, `ledger` |
| Change | `add`, `update`, `link`, `move`, `rename`, `relocate`, `check`, `verify`, `report` |
| Gate | `approve`, `pack use` (typed phrase: `APPROVE <target>`, `USE PACK <name>`) |
| Loop | `dispatch`, `review`, `merge`, `cto tick`, `triage` |
| Operate | `doctor`, `reindex`, `ci`, `sync`, `migrate`, `mcp serve`, `ui serve` |

Exact flags: `references/commands.md` in this skill.

## Pick the next skill

| You need | Load |
|---|---|
| Custom document types, statuses, transitions, BA/PO steps, pack switching | `context-rail-v3-workflows` |
| Run or supervise the agent loop: dispatch → verify → review → merge | `context-rail-v3-loop` |
| Doctor, reindex, recovery, git sync, budgets, test lanes | `context-rail-v3-ops` |
| Every flag for every command | `references/commands.md` |

## First-run refusals

| Message | Fix |
|---|---|
| `NOT_FOUND: no store found` | `rail init`, or put `--store` before the command |
| `VALIDATION_FAILED: unrecognized arguments` | Global options belong before the command; `add` is positional |
| “the charter is not approved; nothing executes yet” | `rail approve charter --confirm "APPROVE charter" --confirmer "<you>"` |
| `'cto tick' cannot be nested inside a running session` | Start it from the outer session, with `RAIL_ACTOR` unset |
| A check is `stale` | Re-run `rail verify`; a stale pass is not a pass |

## Invariants worth knowing before changing anything

- Store writes go through `context_rail.store.write.commit`.
- Reads never execute a command and never hash a source tree.
- `context_rail.kernel` is pure: no file, git, process or index access.
- Unknown manifest schema, pack version, key or policy field is refused.
- One write is one record, with an idempotency key and an expected revision.
