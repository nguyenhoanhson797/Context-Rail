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
rail init --pack basic            # writes ./context-rail/ and offers the picture
rail change add task "Write the first task"
rail read status
rail overview
```

`rail init` is resumable: re-running it keeps what already exists and continues
from the next missing stage. On a terminal it offers the onboarding interview;
without one it scaffolds the store and returns the command to resume.

Every command prints one JSON envelope (`{"ok": true, "value": ...}` or
`{"ok": false, "error": {...}}`) and exits 0 on success, 1 on a refusal, 2 on an
infrastructure failure.

Notes that are easy to trip over:

- `--store` and `--actor` are global options: they go before the command,
  e.g. `rail --store path/to/context-rail read status`.
- `change add` takes positional arguments: `rail change add <preset> "<title>"`.
- Without `--store`, the nearest `context-rail` folder above the current
  directory is used.

### Onboarding a whole project

Bare `rail init` is the onboarding pipeline. It probes the project, writes the
absent skeleton, seeds the pack profiles and skills, offers the picture
interview, asks for a fresh charter approval, writes the managed agent files,
and offers the git drivers. Each stage reports `written`, `unchanged`,
`skipped` or `refused`; re-running the same command resumes.

```bash
rail init --pack basic
```

If approval was skipped, the receipt prints the exact command:

```bash
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Ada"
```

Useful flags:

- `--scaffold` writes only `rail.yaml`, `.gitignore` and the pinned folders,
  preserving the old scripting behavior.
- `--dry-run` prints the stage plan and writes nothing.
- `--no-approve` leaves approval for a later run; `--no-git-drivers` skips the
  merge-driver prompt.
- `--harness agents|claude|cursor|gemini` limits the generated agent files.
- `--interview`, `--answers-file FILE --confirm "APPROVE charter"` and
  `--draft-file FILE` remain picture-source modes. They write the picture and
  leave approval `missing`; run `rail --actor human init` again to resume
  approval, agent files and git drivers. `--interview` and `--answers-file` need
  the `human` actor; a CTO may only propose with `--draft-file`, and a draft
  records no approval.

`ops adapters render` and `ops sync install` remain available on their own.
`ops adapters render` mirrors the workspace profiles and skills into `AGENTS.md`
and `.agents/skills/` (or `CLAUDE.md`, `.cursor/`, `GEMINI.md`); re-run it when a
skill changes.

Useful read commands:

```bash
rail overview          # the daily starting page
rail read status       # counts, executable items, blockers, stuck items
rail read digest       # activity, waits, decisions, quality, usage
rail read find --rel part_of=ship-v3
rail read show <slug>
rail ops doctor        # store integrity issues
rail gate              # doctor issues plus the approval and done gates
```

## Skills

Six operator skills under `skills/` in the repository checkout teach an agent
the rail itself, so it does not have to read the engine to answer "what can this
do?" or "how do we change the workflow?":

- `context-rail-v3` — mental model, quick start, command map
- `context-rail-v3-workflows` — packs, statuses, transitions, BA/PO steps
- `context-rail-v3-pack-authoring` — pack creation, required metadata, defaults,
  declarative placement, workflows, quality and verbs
- `context-rail-v3-surface` — `types`, `next`, `show`, `explain`, `why`, and
  recovering from a refusal
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

`approve`, `ops migrate` and `ops pack use` are human-only tools. They need an
explicitly named human: pass `--actor human` or set `RAIL_ACTOR=human`. A bare
command still defaults to `human` for ordinary reads and writes, but not for
these tools. The actor name is a workflow claim, not authentication; anyone
with shell access as your OS user can act as you.

Once the charter is approved, the loop uses isolated git worktrees:

```bash
rail loop dispatch <item> --expect <etag> --key <idempotency-key>
rail loop review   <item> --run <run-id> --key <idempotency-key>
rail loop merge    <item> --run <run-id> --expect <etag> --key <idempotency-key>
```

`rail loop tick --key <idempotency-key>` starts one bounded CTO session behind a
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

## Placement and metadata

A pack names the document types; `rail.yaml` says **where** each one lives.
Agents never pass a path, a parent folder or a final title: they pass `--rel`
and `--set`, and the resolver derives the path from the work graph.

```powershell
rail change add sprint "Sprint 3" --rel part_of=phase-1
rail change add task "Implement checkout" --rel part_of=sprint-3 --set area=frontend
# items/phase/phase-1/sprint-3/frontend/implement-checkout.md
```

`placement` is keyed by primitive, `presets` by preset and wins over it, and
`kind:<kind>` places a record by its kind. A pack may ship `layout.yaml` to seed
these; `rail.yaml` is authoritative after `init`.

| Token | Resolves to | Scope |
|---|---|---|
| `{slug}`, `{preset}` | the document's slug or preset | segment |
| `{id}`, `{yyyy}`, `{yy}`, `{mm}`, `{dd}` | a record id or a timestamp | segment |
| `{field:name}` | a required or defaulted field, slugified | segment |
| `{kind}` | the record kind | record only |
| `{part_of}`, `{about}`, `{verifies}` | the related document's path | graph |
| `{part_of.slug}`, `{about.title}`, … | just the related slug or title slug | segment |
| `{nearest:preset}`, `{root:preset}` | the nearest or outermost ancestor preset | prefix |
| `{item}`, `{item.slug}`, `{item.title}` | the dispatch or run item | `kind:` placement only |

- A primitive placement must start with a literal segment; a preset placement
  may start with a path token such as `{part_of}` and inherit its parent's
  folder. Every token-bearing template needs `{slug}` or `{id}`.
- A record stays under `records/`; a document may not enter it. `{item.*}` is
  only for a `kind:<kind>` record placement, and `{kind}` is record-only.
- `skill`, `profile`, `cto` and `actor` are fixed to their `workspace/` shapes.
- A path segment may not be `..`, a Windows device name, a control character or
  over 80 characters; the whole path is capped at 200. A field value containing
  a separator is slugified into one safe segment.
- Presets may declare `required` fields and `defaults`. `add` merges defaults and
  refuses a missing value; `update` revalidates the merged document. A default
  used by `{field:...}` must be a non-empty string.

`rail change repath --dry-run` lists every move the current placement implies
and every document it had to skip; `rail change repath` applies them in one
commit. `update` and `link` move an edited document and its descendants in the
same commit; records move with them but are never rewritten.

| Doctor code | Severity | Meaning |
|---|---|---|
| `placement_mismatch` | warning | the path is not what the placement renders |
| `placement_unresolved` | error | no target for the relation the placement walks |
| `placement_ambiguous` | error | more than one target for that relation |
| `placement_cycle` | error | `part_of`/`about`/`verifies` loops |
| `placement_invalid` | error | an unsafe or unresolvable rendered path |
| `required_field_missing` / `required_field_invalid` | error | preset metadata is absent or outside its enum |
| `placement_historical` | warning | an existing record lost its anchor; it is never rewritten |
| `record_metadata_missing` / `record_metadata_invalid` | warning | an existing record's metadata is stale |

A scoped skill copy under `workspace/<pack>/skills/` is pack-shipped material,
so it is never moved and never reported as misplaced.

## Workflows and the agent surface

A pack is one project type with many named workflows over a shared status
dictionary. `scrum` ships `sprint`, `bugfix` and `scope`: a task starts at
`todo`, a bug at `reported`, and neither can use the other's moves. Every
preset selects one workflow; `container: true` marks a phase, sprint, epic, goal
or scope as work that is not executable.

A workflow also carries the quality bar. A status may name a charter gate and
the checks the gate selects, so two workflows can share a status name and still
require different proof. A missing or stale check refuses the move and names the
command that produces it.

Agents never read `pack.yaml`. The pack is compiled into five read-only views:

| Command | Answers |
|---|---|
| `rail types` | every document type, its workflow and use line |
| `rail next` | the executable queue, or one worker's card |
| `rail show <slug>` | the item plus its workflow card |
| `rail why <slug>` | the missing proof and the command |
| `rail explain <name>` | a preset, workflow or action in full |

`rail do <action> --item <slug> --etag <etag>` runs a pack-declared verb (for
example `start-sprint`, `close-sprint`, `file-bug`) through the gateway as the
caller. A verb cannot change the actor, a path, an etag or a profile, and every
write leaves one `action.executed` record.

## Command surface

Start with `rail overview`; it is the bounded daily page. The rest is grouped by
what you are doing:

| Group | Commands |
|---|---|
| **Daily** | `init`, `approve`, `overview`, `read status\|digest\|find\|show\|ledger`, `types`, `next`, `explain`, `why`, `change add\|update\|move\|link\|rename\|repath`, `check declare\|run`, `loop dispatch\|review\|merge\|report\|triage\|tick`, `do <action>`, `gate` |
| **Operations** | `ops doctor\|reindex\|migrate\|rollback`, `ops pack list\|show\|validate\|create\|use`, `ops sync`, `ops sync install`, `ops adapters render`, `ops measure versions\|fix` |
| **Transports** | `serve mcp`, `serve ui` |

The loop gates stay separate on purpose: `dispatch` launches an implementer,
`review` is a different actor, and `merge` fast-forwards only an approved
candidate. `gate` is a read-only governance check; it never runs a Check.

Every former flat command name remains accepted, including `status`, `dispatch`,
`review`, `merge`, `check`, `verify`, `relocate`, `ci`, `pack`, `sync`,
`adapters sync`, `cto brief`, `cto tick`, `mcp serve` and `ui serve`. The
grouped names are the documented surface; the flat names are compatibility
aliases.

`rail <command> --help` is the authoritative list. The CLI, MCP server and UI
are generated from one canonical tool table
(`src/context_rail/gateway/tools.py`); the grouped names live in
`src/context_rail/core/tool_names.py`.

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
  core/       result, util, fixed primitive and grouped command names
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
