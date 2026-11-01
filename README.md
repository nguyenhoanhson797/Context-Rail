# Context Rail v3

An agentic project workspace: the files are the truth, a rebuildable SQLite
index is only a cache, and every change goes through one write path.

A CTO agent runs a project through the rail, sub-agents get narrow,
role-specific context, and a human approves the boundary, the quality bar and
irreversible actions. This repository is a clean rebuild; the legacy v1/v2 code
lives in the sibling `context-rail-link` repository and is frozen.

## Requirements

- Python 3.11 or newer (CI covers 3.11 and 3.13 on Windows and Ubuntu).
- Git, for anything that launches or integrates work.
- For the agent loop: an approved charter and the Factory `droid` CLI on
  `PATH`. Reading and editing the store needs neither.

## Install

```powershell
py -m pip install -e .[test]
```

On macOS or Linux use `python` instead of `py`. This installs two equivalent
entry points, `rail` and `ctx-rail`; every example below also works as
`py -m context_rail`.

## Quick start

From the project you want to manage:

```powershell
rail init --pack small            # writes ./context-rail/
rail add goal "Ship v3"
rail add task "Write the first task" --rel part_of=ship-v3
rail status
rail cto brief
```

Every command prints one JSON envelope (`{"ok": true, "value": ...}` or
`{"ok": false, "error": {...}}`) and exits 0 on success, 1 on a refusal, 2 on an
infrastructure failure.

Notes that are easy to trip over:

- `--store` and `--actor` are global options: they go before the command,
  e.g. `rail --store path/to/context-rail status`.
- `add` takes positional arguments: `rail add <preset> "<title>"`.
- Without `--store`, the nearest `context-rail` folder above the current
  directory is used.

### Onboarding a whole project

`rail init --pack small` writes only the skeleton (`rail.yaml`, `.gitignore`,
empty folders). For a picture, the profiles and the agent files:

```powershell
rail init --interview                                   # or --answers-file FILE --confirm "APPROVE charter"
rail approve charter --confirm "APPROVE charter" --confirmer "Ada"
rail adapters sync                                      # AGENTS.md + .agents/skills/
rail sync --install                                     # optional: git merge drivers
```

- `--interview` and `--answers-file` need the `human` actor; a CTO may only
  propose with `--draft-file`, and a draft records no approval.
- `init --answers-file` writes the picture but leaves approval `missing`; the
  `approve` step is what records it.
- `adapters sync` mirrors the workspace profiles and skills into `AGENTS.md`
  and `.agents/skills/` (or `CLAUDE.md`, `.cursor/`, `GEMINI.md`). It is a
  separate step today; re-run it when a skill changes.

Useful read commands:

```powershell
rail status            # counts, executable items, blockers, stuck items
rail digest            # activity, waits, decisions, quality, usage
rail cto brief         # the bounded brief a CTO session starts from
rail find --rel part_of=ship-v3
rail show <slug>
rail doctor            # store integrity issues
rail ci                # doctor issues plus the approval and done gates
```

## Skills

Four operator skills under `skills/` teach an agent the rail itself, so it does
not have to read the engine to answer "what can this do?" or "how do we change
the workflow?":

- `context-rail-v3` — mental model, quick start, command map
- `context-rail-v3-workflows` — packs, statuses, transitions, BA/PO steps
- `context-rail-v3-loop` — dispatch → verify → review → merge, refusals
- `context-rail-v3-ops` — doctor, reindex, recovery, budgets, test lanes

Install them into your agent's skill directory:

```powershell
Copy-Item -Recurse skills\context-rail-v3* "$HOME\.agents\skills\"
```

See `skills/README.md` for the difference from the pack skills that are
installed into a store's workspace.

## Human gates and the agent loop

Work that launches an agent, or changes the picture's meaning, is gated. The
brief reports the current state; nothing executes until a human approves the
charter:

```powershell
rail approve charter --confirm "APPROVE charter" --confirmer "Ada"
```

Once the charter is approved, the loop uses isolated git worktrees:

```powershell
rail dispatch <item> --expect <etag> --key <idempotency-key>
rail review   <item> --run <run-id> --key <idempotency-key>
rail merge    <item> --run <run-id> --expect <etag> --key <idempotency-key>
```

`rail cto tick --key <idempotency-key>` starts one bounded CTO session behind a
single-instance lock. It must be the outer session, so it refuses to start when
`RAIL_ACTOR` is already set.

## Where state lives

- **Store**: `<project>/context-rail/`, a plain directory of Markdown documents
  plus `rail.yaml`. It is the product's only source of truth and is meant to be
  committed to git.
- **Index**: a rebuildable SQLite cache under the worktree's git dir, or
  `<store>/.rail-local/` outside git. Never in the repository tree; deleting it
  only costs a rebuild.
- **Control files**: dispatch/tick/merge progress and key bindings live in the
  same local dir, written atomically, so a crash leaves either the old file or
  the new one and never a partial document.

## Command surface

- **Store**: `init`, `add`, `update`, `link`, `move`, `rename`, `relocate`,
  `show`, `find`, `reindex`, `doctor`, `ci`
- **Views**: `status`, `digest`, `cto brief`, `ledger`
- **Checks and quality**: `check`, `verify`, `report`, `triage`, `review`
- **Agent loop**: `dispatch`, `merge`, `cto tick`, `approve`
- **Packs and profiles**: `pack list|use|create`, `rollback`
- **Interop**: `migrate` (v1/v2 import), `sync` (clones and git drivers),
  `mcp serve` (MCP over stdio), `ui serve` (loopback HTTP)

`rail <command> --help` is the authoritative list; the CLI, MCP server and UI
are all generated from one tool table (`src/context_rail/gateway/tools.py`).

## Testing

```powershell
py -m pytest -q -m "not slow and not perf"          # functional lane
py -m pytest -q -m "perf and not slow" -p no:xdist  # speed budgets, serial
py -m pytest -q -m slow -p no:xdist                 # 10k-document stores
```

The speed budgets are fixed: a change that needs more time is a bug in the
change, never a reason to raise the budget. `py -m pytest -q` on its own also
collects the perf tests, so use the lanes above when the machine is busy.

## Layout

```text
src/context_rail/
  kernel/     pure rules: structure, budgets, gates, timestamps (no I/O)
  model/      manifest, pack, policy and field validation
  store/      the only write path, the SQLite cache, queries, sync
  runner/     dispatch, review, merge, droid protocol, control files
  gateway/    tool table, handlers, permissions, JSON envelope
  context/    the computed CTO views (brief, status, digest)
  doctor/     integrity checks
  cli.py      thin argparse surface: stdlib plus the tool table at import
```

## Design

- Visual guide (diagrams and from-zero-to-daily walkthrough):
  `docs/context-rail-v3-guide.html`
- Spec: `docs/superpowers/specs/2026-10-28-context-rail-v3-design.md`
- Architecture: `library/arch-stage-3.md`
- Working rules and engine invariants: `AGENTS.md`

Engine invariants worth knowing before changing code:

- Every store mutation goes through `context_rail.store.write.commit`.
- Reads never execute commands and never hash source trees.
- `context_rail.kernel` is pure: no file, git, process or index access.
- Unknown manifest schema, pack version, key or policy field is refused, never
  guessed.
