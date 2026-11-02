# Context Rail v3

An agentic project workspace: the files are the truth, a rebuildable SQLite
index is only a cache, and every change goes through one write path.

A CTO agent runs a project through the rail, sub-agents get narrow,
role-specific context, and a human approves the boundary, the quality bar and
irreversible actions. The store it manages is plain Markdown you commit to git;
the package ships the CLI, the MCP server and a loopback UI over one tool table.

## Requirements

- Python 3.11 or newer (CI covers 3.11 and 3.13 on Windows and Ubuntu).
- Git, for anything that launches or integrates work.
- For the agent loop: an approved charter and the Factory `droid` CLI on
  `PATH`. Reading and editing the store needs neither.

## Install

```bash
python -m pip install context-rail
```

On Windows use `py` instead of `python` (`py -m pip install context-rail`); that
is the only difference, every command below works the same in both shells.
This installs two equivalent entry points, `rail` and `ctx-rail`; every example
below also works as `python -m context_rail`.

`python -m pip` installs into the interpreter that will run the tool. Bare
`pip install context-rail` is the same command whenever `pip` on your `PATH`
belongs to that interpreter; on a machine with several Pythons, the module form
is the one that cannot pick the wrong one.

To keep the tool out of your project environments, `pipx install context-rail`
works too.

## Quick start

From the project you want to manage:

```bash
rail init --pack basic            # writes ./context-rail/
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

`rail init --pack basic` writes only the skeleton (`rail.yaml`, `.gitignore`,
empty folders). For a picture, the profiles and the agent files:

```bash
rail init --interview                                   # or --answers-file FILE --confirm "APPROVE charter"
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Ada"
rail adapters sync                                      # AGENTS.md + .agents/skills/
rail sync --install                                     # optional: git merge drivers
```

- `--interview` and `--answers-file` need the `human` actor; a CTO may only
  propose with `--draft-file`, and a draft records no approval.
- `init --answers-file` writes the picture but leaves approval `missing`; the
  `approve` step is what records it, and it needs an explicit `--actor human`
  or `RAIL_ACTOR=human`.
- `adapters sync` mirrors the workspace profiles and skills into `AGENTS.md`
  and `.agents/skills/` (or `CLAUDE.md`, `.cursor/`, `GEMINI.md`). It is a
  separate step today; re-run it when a skill changes.

Useful read commands:

```bash
rail status            # counts, executable items, blockers, stuck items
rail digest            # activity, waits, decisions, quality, usage
rail cto brief         # the bounded brief a CTO session starts from
rail find --rel part_of=ship-v3
rail show <slug>
rail doctor            # store integrity issues
rail ci                # doctor issues plus the approval and done gates
```

## Skills

Four operator skills under `skills/` in the repository checkout teach an agent
the rail itself, so it does not have to read the engine to answer "what can this
do?" or "how do we change the workflow?":

- `context-rail-v3` — mental model, quick start, command map
- `context-rail-v3-workflows` — packs, statuses, transitions, BA/PO steps
- `context-rail-v3-loop` — dispatch → verify → review → merge, refusals
- `context-rail-v3-ops` — doctor, reindex, recovery, budgets, test lanes

Install them into your agent's skill directory from a checkout:

```bash
cp -r skills/context-rail-v3* ~/.agents/skills/          # macOS, Linux
```

```powershell
Copy-Item -Recurse skills\context-rail-v3* "$HOME\.agents\skills\"   # Windows
```

See `skills/README.md` for the difference from the pack skills that are
installed into a store's workspace.

## Human gates and the agent loop

Work that launches an agent, or changes the picture's meaning, is gated. The
brief reports the current state; nothing executes until a human approves the
charter:

```bash
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Ada"
```

`approve`, `migrate` and `pack use` are human-only tools. They need an
explicitly named human: pass `--actor human` or set `RAIL_ACTOR=human`. A bare
command still defaults to `human` for ordinary reads and writes, but not for
these tools. The actor name is a workflow claim, not authentication; anyone
with shell access as your OS user can act as you.

Once the charter is approved, the loop uses isolated git worktrees:

```bash
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
  committed to git. A pack may ship `layout.yaml` placement defaults; `init`
  seeds `rail.yaml` from them, and `rail.yaml` is authoritative afterwards.
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
- **Packs, profiles and adapters**: `pack list|use|create`, `rollback`,
  `adapters sync` (AGENTS.md, `.agents/skills/`, `CLAUDE.md`, `.cursor/`)
- **Measurement**: `measure fix|versions`
- **Interop**: `migrate` (v1/v2 import), `sync` (clones and git drivers),
  `mcp serve` (MCP over stdio), `ui serve` (loopback HTTP)

`rail <command> --help` is the authoritative list; the CLI, MCP server and UI
are all generated from one tool table (`src/context_rail/gateway/tools.py`).

## Testing

From a checkout, install it in editable mode with the test dependencies:

```bash
python -m pip install -e .[test]
```

Then run one lane at a time (on Windows use `py -m pytest`):

```bash
python -m pytest -q -m "not slow and not perf"          # functional lane
python -m pytest -q -m "perf and not slow" -p no:xdist  # speed budgets, serial
python -m pytest -q -m slow -p no:xdist                 # 10k-document stores
```

The speed budgets are fixed: a change that needs more time is a bug in the
change, never a reason to raise the budget. A bare `pytest -q` on its own also
collects the perf tests, so use the lanes above when the machine is busy.

## Layout

```text
src/context_rail/
  kernel/     pure rules: structure, budgets, gates, timestamps (no I/O)
  model/      manifest, pack, policy and field validation
  core/       result, util and the fixed primitive names
  store/      the only write path, the SQLite cache, queries, sync
  runner/     dispatch, review, merge, droid protocol, control files
  gateway/    tool table, handlers, permissions, JSON envelope
  context/    the computed CTO views (brief, status, digest)
  doctor/     integrity checks
  packs/      the built-in pack with its profiles, skills and templates
  ui/         the loopback HTTP UI
  cli.py      thin argparse surface: stdlib plus the tool table at import
```

## Design

- Visual guide (diagrams and a from-zero-to-daily walkthrough):
  <https://github.com/nguyenhoanhson797/Context-Rail/blob/master/docs/context-rail-v3-guide.html>
- Interactive engine graph (primitives, relations, one write path and the agent
  loop):
  <https://github.com/nguyenhoanhson797/Context-Rail/blob/master/docs/context-rail-v3-engine-graph.html>
- Working rules and engine invariants:
  <https://github.com/nguyenhoanhson797/Context-Rail/blob/master/AGENTS.md>

The design spec, the stage plans and the architecture records are working
material and stay out of the published repository.

Engine invariants worth knowing before changing code:

- Every store mutation goes through `context_rail.store.write.commit`.
- Reads never execute commands and never hash source trees.
- `context_rail.kernel` is pure: no file, git, process or index access.
- Unknown manifest schema, pack version, key or policy field is refused, never
  guessed.

## License

MIT. See the `LICENSE` file, or
<https://github.com/nguyenhoanhson797/Context-Rail/blob/master/LICENSE>.
