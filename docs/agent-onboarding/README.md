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
| `video` | scenes and shots | production work with renders, assets and reviews |

Every pack also declares the shared base presets (`goal`, `scope`, `task`, `bug`,
`risk`, `question`, `note`, `decision`, `check`, `charter`, `profile`, …). If
none fits, copy one with `rail ops pack create` and edit the copy — never fork
the engine.

## What "full power" means

The rail is not a task list with extra steps. The same compiled model drives:

- **The domain** — document types, fields, statuses, workflows and quality
  gates, declared by a pack.
- **The operating model** — unit, hierarchy, focus, queue and progress, also
  declared by the pack and compiled into one immutable model.
- **Execution** — checks, isolated worktrees, dispatch, review, merge, reports,
  triage, budgets and a ledger.
- **The projection** — `rail types|next|model|show|explain|why`, the MCP tool
  table, the loopback UI, the generated `AGENTS.md` and workspace skills.

An agent never parses `pack.yaml`, `rail.yaml` or the charter to infer the
workflow. It asks the compiled surface. That is what keeps one agent correct
across `basic`, `flow`, `scrum`, `waterfall`, `video` and any custom pack.

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
  an explicitly named human and the typed phrase.
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
| `context-rail-v3-loop` | You dispatch, review, merge or supervise agents |
| `context-rail-v3-ops` | You doctor, reindex, recover or ship |
| `context-rail-v3-onboarding` | You are adopting the rail in a new project |

Do not confuse these with the pack skills copied into a store's workspace
(`test-first`, `review-checklist`). Those teach a worker how to do
its job. These teach an operator how to drive the product.
