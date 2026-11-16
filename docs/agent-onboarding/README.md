# Agent onboarding kit

Any agent. Any harness. Any project.

This directory is the portable entry point to Context Rail. One `rail` binary
gives an agent a project workspace, a command surface, an MCP server and a
loopback UI over the same validated, authorized, atomic write path.

Use this kit when an agent has to adopt the rail in a project it has never seen,
or when you are wiring the rail into any harness: Claude Code, Cursor, Gemini
CLI, Codex, OpenCode, Windsurf, a custom MCP client, or plain shell.

## What is in the kit

| File | Use it for |
|---|---|
| `AGENTS.template.md` | Copy it into a project root as `AGENTS.md` (or merge its sections). Any harness that reads `AGENTS.md` gets the safe protocol. |
| `../../skills/context-rail-v3-onboarding/SKILL.md` | Install as a skill. Skill-aware harnesses discover it and load it on first contact. |
| `references/harnesses.md` | Per-harness instruction files, MCP wiring and actor choice. |
| `references/capabilities.md` | The full power map: primitives, packs, workflows, operating model, checks, loop, transports. |
| `references/commands.md` | CLI aliases, canonical MCP tool names, flags and the JSON envelope. |
| `references/playbooks.md` | Zero-to-work recipes by project shape. |
| `references/refusals.md` | Every common refusal and its exact fix. |
| `references/prompts.md` | Copy-paste CTO, onboarding, implementer, reviewer and BA/PO prompts. |

```bash
cp docs/agent-onboarding/AGENTS.template.md AGENTS.md
```

```powershell
Copy-Item docs\agent-onboarding\AGENTS.template.md AGENTS.md
```

The template is deliberately not named `AGENTS.md` here, so a checkout of this
package does not auto-load the product protocol as the repository's own agent
instructions.

## The three sentences

1. **Files are the truth.** A project is a `context-rail/` folder of Markdown
   plus `rail.yaml`, committed to git and editable by hand.
2. **The index is a cache.** SQLite mirrors those files so reads stay fast;
   delete it and the next command rebuilds it.
3. **One write path.** Every change from the CLI, MCP or UI is validated,
   authorized, locked, written atomically, recorded and indexed.

Everything else is a consequence.

## Pick your entry point

| Situation | Start here |
|---|---|
| A project has no `context-rail/` store | `rail init --pack basic`, then `references/playbooks.md` |
| A store exists but the agent is new | `rail overview`, `rail types`, `rail next`, `rail model` |
| The store exists but approval is missing | `rail --actor human approve charter --confirm "APPROVE charter" --confirmer "<you>"` |
| You are wiring a harness | `references/harnesses.md` |
| You need a specific command or flag | `references/commands.md` |
| A command refused | `references/refusals.md` |
| You want the whole model | `references/capabilities.md` |
| You need a role prompt | `references/prompts.md` |

## Zero to work, in one screen

Install once:

```bash
python -m pip install context-rail      # Windows: py -m pip install context-rail
```

From the project root:

```bash
rail init --pack basic                  # resumable; supply the picture when asked
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Your Name"
rail change add goal "Ship the first slice"
rail change add task "Write the first task" --rel part_of=ship-the-first-slice
rail overview
rail read status
rail types
rail next
```

`rail init` is resumable. It probes the project, writes the absent skeleton,
seeds the pack profiles and skills, offers the picture interview, asks for a
fresh charter approval, then writes the managed agent files and offers the git
drivers once the picture exists. Re-running it continues from the first missing
stage. Add
`--scaffold` for skeleton only, `--dry-run` to print the plan, and `--solo` to
bind one human as the project owner.

If you supply the picture with `--answers-file`, add `--confirmer "Your Name"`
with `--confirm "APPROVE charter"` to record the approval in the same call;
without it, approval stays missing and the separate `approve` above is required.

If the store already exists, skip `init` and start with `rail overview`. If
`init` reports that the picture is missing, supply it first with `--interview`,
`--answers-file` or a CTO `--draft-file`; the approval refuses an incomplete
protected picture.

### Pick a pack

| Pack | Shape | Use when |
|---|---|---|
| `basic` | one stream: `todo → doing → done` | the smallest useful store, a solo project or a quick adoption |
| `flow` | continuous kanban | work arrives continuously and there is no sprint |
| `scrum` | sprints | you plan in sprints and want a start/close rhythm |
| `waterfall` | phase gates | requirements, design, build, test and release are distinct phases |
| `video` | scenes and shots | production work with shots, render records, assets and reviews |
| `portfolio` | components and slices | cross-project coordination across child stores with `store` checks |

Every pack also declares the shared base presets (`goal`, `scope`, `task`, `bug`,
`risk`, `question`, `note`, `decision`, `check`, `charter`, `profile`, …). If
none fits, copy one with `rail ops pack create` and edit the copy — never fork
the engine.

## Roles and seats

A pack's `operating` block, version 2, may declare **roles**. A **seat** binds
an agent to one role on one anchor: the project or one container. The lead role
usually sits on the project; a level lead sits on a sprint, scene or phase;
implementers sit on one item.
Roles are compiled into the approved pack, so changing one needs re-approval.

```bash
rail ops pack create --name mine --from scrum --roles   # draft with starter roles
rail ops pack use --name mine --expect <etag> --confirm "USE PACK mine"

rail seat grant lead-1 --role lead --anchor project     # the human's first seat
rail seat grant sprint-1 --role sprint-lead --anchor sprint-3 --profile manager
rail seat list
rail seat show sprint-1
rail seat open sprint-1 --print-env                    # one-time token for RAIL_SESSION
rail sprint-1 next                                     # read as that seat
rail seat close sprint-1                               # or --all (human)
```

Run the pack switch yourself in a terminal: role mode refuses `--actor`, and a
seat session is never human. The first seat grant is the human's bootstrap step;
after that, a lead may grant inside its `can.grant` and write reach.

In role mode:

- **The session is the identity.** `RAIL_SESSION` carries the token;
  `--actor` is refused. Open a session with `rail seat open <seat> --print-env`; the
  token appears once in the JSON result. Export it as `RAIL_SESSION` for the
  agent your tool starts (or pass `--session-env NAME` before the command when
  one shell coordinates more than one role-mode store).
- **Reach is enforced on reads and writes.** A seat sees its anchor's subtree,
  the ancestors and shared notes its role names, and nothing outside it. `find`,
  `status`, `digest`, `ledger`, `show` and `cto brief` report
  `omitted_outside_reach`; a direct out-of-reach read is refused with the reach
  that would cover it.
- **A lens is read-only.** `rail <seat> <command>` views the store as that seat.
  With the seat's own session it may read and write; with another seat's session
  it is read-only and allowed only when the target anchor is in the caller's
  reach.
- **Human proof is not a name.** Approving, using a pack, granting a
  `human: true` role and the first grant when no seat exists need the human on a
  terminal or through the UI. A session caller can never satisfy a human-only
  action. A non-human seat may grant only inside its `can.grant` and write
  reach.
- **Anonymous is empty.** With no session and no terminal, the caller has read
  tools and no documents; open a session to work.
- **Revocation cascades.** Revoking a seat closes its sessions and every seat it
  granted. A re-granted seat cannot reuse an old session.
- **Compat is unchanged.** A pack without `roles` keeps the name-based
  `--actor` behaviour. `rail seat ...` and the lens refuse with "this pack
  declares no roles" and point at `rail ops pack use`.

## What "full power" means

The rail is not a task list with extra steps. The same compiled model drives:

- **The domain** — document types, fields, statuses, workflows and quality
  gates, declared by a pack.
- **The operating model** — unit, hierarchy, focus, queue and progress, also
  declared by the pack and compiled into one immutable model.
- **Execution** — briefs, `rail loop result` Markdown evidence records (both
  commitless research and commit-bound code), checks, candidates, Markdown
  reviews, diagnostic reports, triage, budgets and a ledger, all recorded from
  whatever tool did the work.
- **The projection** — `rail types|next|model|show|explain|why`, the MCP tool
  table, the loopback UI, the generated `AGENTS.md` and workspace skills.

An agent never parses `pack.yaml`, `rail.yaml` or the charter to infer the
workflow. It asks the compiled surface. That is what keeps one agent correct
across `basic`, `flow`, `scrum`, `waterfall`, `video`, `portfolio` and any
custom pack.

## Non-negotiables

- **A stale write is refused.** Read the item, then pass its etag with
  `--expect`.
- **A retry is safe.** Pass `--key` on every write; the same key returns the
  same record.
- **Paths are derived.** Pass `--rel` and `--set`; never pass a path, a parent
  folder or a final title.
- **Status is managed.** Use `rail change move` or a declared
  `rail do <action>`, never a hand edit.
- **Records are immutable.** Do not edit `records/`.
- **Human-only stays human.** `approve`, `ops migrate` and `ops pack use` need
  an explicitly named human and the typed phrase. In role mode the human must be
  on a terminal or use the UI channel; a seat session is never human.
- **A gate is not a suggestion.** `rail why` names the missing proof and the
  command that produces it.

## Verify the onboarding

```bash
rail ops doctor          # integrity issues
rail gate                # doctor plus approval and done gates
rail ops pack show --name <pack>  # digests and approval state
rail read digest         # activity, waits, quality, usage
```

A healthy first run has: `doctor` clean (or informational only), `gate`
passing, the charter approved, `rail next` returning work, and an `AGENTS.md`
whose managed block is present.

## The rest of the operator skills

This kit is the front door. The detailed operator skills live under `skills/`:

| Skill | Load when |
|---|---|
| `context-rail-v3` | You need the map and the command families |
| `context-rail-v3-workflows` | You need custom types, statuses, transitions or BA/PO |
| `context-rail-v3-pack-authoring` | You are creating or changing a pack |
| `context-rail-v3-surface` | You work from `types`, `next`, `show`, `explain`, `why` |
| `context-rail-v3-loop` | You hand out, record, verify or review agent work |
| `context-rail-v3-ops` | You doctor, reindex, recover or ship |
| `context-rail-v3-onboarding` | You are adopting the rail in a new project |

Do not confuse these with the skills a store carries. `rail init` seeds the
engine skills and the pack skills into the store's workspace (`lean-testing`,
`review-checklist` and the others), and `rail ops upgrade` refreshes them. Those
teach the agent doing the work how to do its job, and every session reads them
with its live brief. These public skills are optional setup reference for an
operator: they teach an operator how to drive the product.
