---
name: context-rail-v3-onboarding
description: 'Adopt Context Rail in any project, with any agent harness: probe, install, init, approve, seed first work, verify and hand off. Load when a project has no context-rail/ store, when asked to set up or onboard Context Rail, or when an agent needs a safe zero-to-work path.'
---

# Context Rail v3 — onboarding any project

This skill is the adoption path. It assumes no prior rail knowledge and no
specific harness. The product itself is one binary, one JSON envelope and one
write path; everything below is a sequence of reads and gated writes.

## First, probe — change nothing

```powershell
rail --help                             # is the tool installed and what is the surface?
rail --store <project>/context-rail read status   # is there a store?
rail ops pack list                      # which pack is active?
rail ops doctor                         # is the store healthy?
```

If `rail` is missing:

```powershell
py -m pip install context-rail          # Windows
python -m pip install context-rail      # macOS / Linux
```

Then use one of three entry points:

| What you found | Do |
|---|---|
| No store | `rail init --pack basic` |
| A store, no approval | `rail --actor human approve charter --confirm "APPROVE charter" --confirmer "<you>"` |
| A store, approved | `rail overview`, then `rail types` and `rail next` |
| A store you do not understand | `rail model`, `rail types`, `rail ops pack show --name <pack>` |

Bare `rail` prints a one-value JSON envelope pointing at `rail init`; it does not
create anything.

## The onboarding pipeline

Bare `rail init` is a resumable reconcile, not a one-shot scaffold. It probes,
then runs the stages that are missing:

| Stage | Writes | Resume behavior |
|---|---|---|
| Skeleton | `rail.yaml`, `.gitignore`, pinned folders | kept if present |
| Workspace | the pack's profiles and skills | absent-only |
| Picture | goal, vision, scope, Won't, `charter.md` | skipped when present |
| Approval | an approval record with protected-file hashes | human-only, fresh phrase |
| Agent files | `AGENTS.md` and `.agents/skills/<name>/SKILL.md` | runs after the picture exists; regenerated when a skill changes |
| Git drivers | `.gitattributes` and clone-local merge drivers | offered only in a git checkout |

```powershell
rail init --pack basic                  # full pipeline
rail init --pack scrum --dry-run        # print the stage plan, write nothing
rail init --pack basic --scaffold       # skeleton only, for scripts
rail init --pack basic --solo --interview  # one human owns the project
```

Useful flags: `--dir DIR`, `--root NAME`, `--no-approve`, `--no-git-drivers`,
`--harness agents|claude|cursor|gemini`, `--key KEY`. `--solo` with a picture
source writes a charter whose `mode: solo` binds one human as the project owner:
the approval gates are skipped, but the workflow quality gates (checks, review,
merge) still apply.

Re-running the same command continues from the first missing stage. A stage
reports `written`, `unchanged`, `skipped` or `refused`; a refusal writes nothing
and prints the reason.

## The picture and the approval

The picture is the project's protected meaning: why it exists, what is in scope,
what is explicitly out, and the charter that sets gates, rights and budgets.
Three ways to supply it:

```powershell
rail --actor human init --interview                                  # terminal only
rail --actor human init --answers-file answers.json --confirm "APPROVE charter" --confirmer "Ada"
rail --actor cto init --draft-file draft.json                        # propose only
```

An answers-file init runs the full onboarding pipeline: it writes the picture
and workspace, renders the selected agent harnesses, installs the git drivers,
and reports every stage plus the next steps. `--confirmer` with the confirm
phrase records the charter approval in the same call; without it, approval stays
missing and the separate approval below is required.

A draft records no approval. The interview records no approval either: the
approval is a separate, human-only decision with its own fresh phrase. (An
answers-file init records it in the same call only when you pass `--confirmer`.)

```powershell
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Ada"
```

The charter binds the protected files' hashes. Editing them later reports
`approval_mismatch`; inspect the new state and reapprove deliberately.

If you only need a working store for a script or a quick tour, `--scaffold` is
the old behavior. It is not a complete onboarding: it writes no profiles, no
skills, no picture and no agent files. With an external `--code-graph` it only
probes readiness; run `rail ops codegraph setup` to build the first snapshot.

## Seed the first work

Agents never pass a path or a parent folder. They pass a relation and fields;
the resolver derives the path from the work graph.

```powershell
rail change add goal "Ship the first slice"
rail change add task "Write the first task" --rel part_of=ship-the-first-slice
rail change add task "Review the first task" --rel part_of=ship-the-first-slice --tag review
rail read status
rail overview
```

Read the compiled surface, never `pack.yaml`:

```powershell
rail types                  # what kinds of work exist, their workflows and use lines
rail next                   # the operating queue, or one worker's card
rail read show <slug>       # the item, its workflow and its proof
rail why <slug>             # the missing proof and the command that produces it
rail explain <name>         # one preset, workflow or action in full
rail model                  # unit, hierarchy, focus, queue, progress, digests
```

## Make the harness agent-ready

The pipeline already renders the managed agent files. Re-run the renderer when
a workspace skill changes:

```powershell
rail ops adapters render                              # AGENTS.md + .agents/skills/
rail ops adapters render --harness claude --harness cursor --harness gemini
rail ops adapters render --dry-run
```

The generated `AGENTS.md` is a managed block: prose outside the markers is
preserved, and a hand-edited owned block refuses until a human runs
`--force --confirm "FORCE ADAPTERS adapters"`. The same render writes
`CLAUDE.md`, `.cursor/rules/context-rail.mdc`, `GEMINI.md` and the Claude role
files for the requested harnesses.

For any harness that does not read those files, copy
`docs/agent-onboarding/AGENTS.template.md` into the project root as `AGENTS.md`,
or paste `docs/agent-onboarding/references/prompts.md` into the session. The
transport is the same: `rail --actor cto serve mcp` for MCP, or the CLI.

## Prove the onboarding worked

```powershell
rail ops doctor                 # integrity: clean or informational only
rail gate                       # doctor + approval + open done gates
rail ops pack show --name <pack>  # operating model, digests, approval state
rail read status                # counts, executable items, blockers, focus
rail read digest                # activity, waits, quality, usage
```

The store is ready when:

- `rail.yaml` names the intended pack and the folder layout resolves;
- `doctor` has no error-severity issue;
- the charter is approved and `gate` passes;
- `rail next` returns at least one executable item, or explains why none is;
- `AGENTS.md` has exactly one managed block, or the harness was wired another
  way.

## Hand off to the right skill

| You need | Load |
|---|---|
| The map and command families | `context-rail-v3` |
| Custom types, statuses, transitions, BA/PO | `context-rail-v3-workflows` |
| Create or change a pack | `context-rail-v3-pack-authoring` |
| Work from `types`, `next`, `show`, `explain`, `why` | `context-rail-v3-surface` |
| Dispatch, review, merge, supervise | `context-rail-v3-loop` |
| Doctor, reindex, recovery, budgets | `context-rail-v3-ops` |

## Onboarding an existing project (no picture rewrite)

If the project already has a store and an approved picture, do not run `init`
again. Do this instead:

1. `rail overview` and `rail read status` — what is executable and what waits.
2. `rail model`, `rail types`, `rail ops pack show --name <pack>` — the operating model
   and approval state.
3. `rail ops doctor` — placement, metadata and index integrity.
4. `rail ops adapters render` — refresh the agent files if a skill moved.
5. `rail next` — take work, or `rail why <slug>` — explain the block.

## Onboarding a project with no code

The rail does not require a codebase. Use it for docs, research, operations or a
content pipeline:

```powershell
rail init --pack flow
rail change add goal "Publish the handbook"
rail change add note "Audience notes" --rel part_of=publish-the-handbook
rail change add task "Draft chapter one" --rel part_of=publish-the-handbook
```

`flow` is continuous kanban; `basic` is one stream; `video` models scenes and
shots. If none fits, copy a pack and edit it — never fork the engine.

## Refusals during onboarding

| Message | Fix |
|---|---|
| `NOT_FOUND: no store found` | `rail init`, or put `--store` before the command |
| `CONFLICT: a store already exists here` | You used `--scaffold` on an existing store; run bare `rail init` to resume instead |
| The charter is not approved | `rail --actor human approve charter --confirm "APPROVE charter" --confirmer "<you>"` |
| `approval_mismatch` after a pack upgrade | `rail ops pack show --name <pack>`, then reapprove `pack:<pack>` and the charter |
| `NO_FOCUS` on `add` | The pack needs a parent: start the container or pass `--rel part_of=<container>` |
| `AMBIGUOUS_FOCUS` | Two containers match: pass `--into <id>` from the candidates |
| `VALIDATION_FAILED: unrecognized arguments` | Global options belong before the command; `add` is positional |
| A check is `stale` | `rail check run <target>` again |
| `agents` stage is `skipped (picture_deferred)` | Supply the picture, then re-run `rail init`; agent files need the picture to exist |
| `doctor` reports an interrupted write | `rail ops doctor --repair`, then `rail ops reindex` |
| Two CTOs at once | Let the running `loop tick` finish; it holds the single-instance lock |

## What not to do

- Do not lower a gate, approve as an agent, or switch packs to make a refusal
  disappear.
- Do not pass a path, a parent folder or a final title to `change add`.
- Do not edit `records/`; they are history.
- Do not hand-edit the SQLite index or delete the store to fix a view.
- Do not copy `pack.yaml` into a prompt to "understand the project"; read
  `rail model`, `rail types` and `rail explain`.
- Do not create a second store for a team that needs a different workflow; one
  active pack per store, or a superset pack.

## The one-screen checklist

```text
[ ] rail installed and on PATH
[ ] rail init run without refusal
[ ] picture present, charter approved by a named human
[ ] rail.yaml pack and placement resolve
[ ] profiles and skills seeded
[ ] AGENTS.md / harness files rendered
[ ] rail ops doctor clean
[ ] rail gate passes
[ ] rail next returns work, or explains why it does not
[ ] the next operator skill is named
```
