# How Context Rail works

This page is the engineering view of Context Rail v3: what the engine
guarantees, how a project's shape is declared, and which commands agents and
humans use. For the short pitch and the five-minute start, see the
[README](../README.md).

A CTO agent runs the project; sub-agents get narrow, role-specific context; a
human is accountable for the boundary, the quality bar and irreversible
actions, and every approval records who signed it and the human it names. The
store is plain files in your git repo: Markdown documents, JSON records and
`rail.yaml`.

- **No service to run.** Git in, files out: no database server, no proprietary format.
- **One write path.** CLI, MCP and UI all pass through validation → authorize → lock → atomic write → record → index.
- **Fail closed.** Unknown pack key, manifest schema or policy field is refused, never guessed.

## Requirements

Python 3.11+ · git · Node.js 22+ (only to rebuild the UI from source). Any coding tool
(Claude Code, Codex, opencode, Droid, Cursor, Gemini CLI) can do the work; the
rail does not run one.

## How it works in one picture

```text
  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────┐
  │ pack.yaml    │   │ rail.yaml    │   │ your git repo       │
  │ domain +     ├──▶│ layout       ├──▶│ context-rail/       │
  │ operating    │   │ (placement)  │   │  Markdown + records │
  └──────┬───────┘   └──────┬───────┘   └─────────┬───────────┘
         │                  │                      │
         ▼                  ▼                      ▼
  ┌─────────────────────────────────────────────────────┐
  │ COMPILER — immutable ProjectModel                    │
  │ pack + placement + surface.yaml → digest, refusing   │
  └──────────────────────┬──────────────────────────────┘
                         │ one compiled model
                         ▼
  ┌──────────────────────────────────────────────────────┐
  │ GATEWAY — the only door                              │
  │ permissions → etag freshness → lock → atomic write   │
  └───┬─────────┬───────────┬──────────┬─────────────────┘
      ▼         ▼           ▼          ▼
  rail/ctx-rail   serve mcp  serve ui   briefs & agents

  reads fan out the same way:  model → SQLite cache → go fast;
  delete the cache, next command rebuilds it from the files.
```

**No command parses `pack.yaml`.** The compiler does, once, and everything else
reads what it produced, so the CLI, MCP, UI and every agent see one answer.

## Why the engine holds up

| Property | What it means in practice |
|---|---|
| **Files are the truth** | SQLite is a cache under the git dir (or `.rail-local/` outside git); deleting it only costs a rebuild. Records are append-only and never rewritten. |
| **Paths are derived, never chosen** | You pass `--rel part_of=sprint-3`; the placement template renders the path. `rail change repath --dry-run` shows every implied move before you apply it. |
| **Quality belongs to the workflow** | A status can name a charter gate and the checks it selects. A missing or stale check refuses the move and names the command that produces it. Check modes cover commands (`cmd`), files (`exists`), supplied proof (`evidence`), candidate `review`, `human` sign-off, and `store` checks that prove a child store's committed item category without writing the child store or running git on reads (freshness checks the child's committed `HEAD` and item etag). |
| **Approval is data** | `approve` records who signed it, its channel and the human it names. A charter may set `approval.agents: false` to require a human signature. Change the semantics (transition, placement, operating block) and the store reports `approval_mismatch` until it is reapproved. |
| **Agents get a narrow surface** | They never parse `pack.yaml`; they ask `rail types`, `rail next`, `rail read show`, `rail why`, `rail explain`, and run declared verbs with `rail do`. |
| **Your tool does the work** | The rail hands out a brief and records the candidate commit, the workflow-shaped Markdown `rail loop result` record, the check and the review. Your tool runs its own sub-agents in its own git worktrees and you merge in git; `gate` never runs Checks. |
| **Collisions are impossible to miss** | Every write carries the etag it read; a stale write is refused. Every retry carries a `--key` and gets the same record back. |

## The loop in one line

```text
research: rail overview → approve → rail loop brief → work → rail loop result → checks → rail loop review → done
code:     rail overview → approve → rail loop brief → work/commit → rail loop candidate → rail loop result --commit → checks → rail loop review → done
```

`rail gate` is a read-only governance check. Nothing executes until the charter
is approved, or the charter binds `mode: solo` (one human owns the project;
approval ceremony is skipped, the quality gates still apply). The approval
record names its signer and the human it was made for.
Every registered assignment leaves one workflow-shaped Markdown result record
(up to 256 KiB / 262,144 bytes, ordered nonempty `##` sections, 4000-char brief
contribution cap, `--key` idempotency, nullable `candidate_commit` so discovery
never fabricates a commit); `rail --actor human ops upgrade` + `approve` adopts
shipped result contracts on older stores without rewriting historical done items.

## Shape any kind of project

The engine doesn't know what a sprint is. A **pack** declares the
vocabulary; the engine enforces the shape. Every project is five declared
parts:

| Part | The question it answers | Declared in |
|---|---|---|
| **Container** | what groups work? | `operating.hierarchy` |
| **Unit** | what is executable? | `operating.unit` |
| **Focus** | what are we working right now? | `operating.focus` |
| **Queue** | what can start next? | `operating.queue` |
| **Progress** | what counts as finished? | `operating.progress` |

### Scrum: goal → scope → sprint → story

```text
  goal ──▶ scope ──▶ sprint ──▶ story ──▶ task
   why     what      when      unit      detail
```

- `goal` states the outcome; `scope` holds the prioritized `must/should/could/won't`;
- the `sprint` is the **required** hierarchy: stories hang off it and the
  plan groups by it;
- focus = the active sprint; an ambiguous focus is refused with candidates,
  never guessed;
- a `story` is the board column unit; a story to `done` needs `item_done`;
  a bug to `closed` additionally needs `repro-check` and `acceptance`;
- progress = done-category stories, grouped by sprint;
- queue = stories, tasks and bugs with a legal transition available.

### The same five parts, other domains

```text
video      scene ──▶ shot ──▶ storyboard ──▶ generating ──▶ review ──▶ approved ──▶ published
waterfall  phase ──▶ deliverable   (planned → designing → building → testing → done)
flow       epic ──▶ story           (backlog → ready → building → review → shipped)
basic      goal ──▶ task            (todo → doing → done)
portfolio  component ──▶ slice     (intake → planned → delegated → integrating → verified → released)
```

| | scrum | waterfall | video | flow | basic | portfolio |
|---|---|---|---|---|---|---|
| Container | sprint (required) | phase (required) | scene (required) | epic (optional) | goal (optional) | component (required) |
| Unit | story | deliverable | shot | story | task | slice |
| Focus | active sprint | active phase | active scene | continuous | continuous | none (concurrent) |
| Queue | story·task·bug | deliverable·task·bug | shot | story·task·bug | task·bug | slice·release |
| Progress | done / sprint | done / phase | `approved` / scene | done / epic | done / goal | done / component |
| Gate | `item_done` (bugfix adds `repro-check`+`acceptance`) | `item_done` | `item_done` | `item_done` (default) | `item_done` (default) | `item_done` + `release` (`store` checks) |

Every move into a done-category status needs the charter's `item_done` gate
(or `scope_done` for a scope entry), whether or not the workflow names it.
Ideas stay off the queue in `basic` and `flow`; they wait in the Ideas
catalog until someone turns one into a task or story. In `scrum`, a story
that is not planned yet waits in a `backlog` container
(`--rel part_of=<backlog>`) and joins a sprint with `rail change link`.

In video, `render` runs are **execution records** on the approved shot, not a
status or an item of their own: they show up in the progress view and the
ledger, and are never merged with workflow completion. Context Rail does not
run the render. Whoever does reports each attempt with
`rail loop execution <shot> --action render --state running`, then
`--state succeeded --output <file>` or `--state failed --error "<why>"`. A new
attempt can start only from the action's declared status (`approved`), and only
the CTO and the human may record one.
Same engine, same five parts; only the pack's data changed.

### Why this costs zero engine code

The spine is fixed: six primitives (`item`, `note`, `record`, `check`,
`actor`, `policy`), eight relations, five categories (`not_started`,
`active`, `blocked`, `done`, `dropped`), and the compiler refuses anything
it doesn't recognize. Everything project-specific is *data* in the pack: the
words, the order, the gates, the labels. So:

- relabelling or retitling screens is a `surface.yaml` change, no reapproval;
- changing semantics (a transition, placement, or the `operating` block)
  changes the digest and forces reapproval (`approval_mismatch`);
- a new shape is a copied pack with different data: no engine code, no
  special cases, no UI changes.

Don't like the word "story"? Your pack gets to call it anything; the engine
only knows "the unit".

New pack: `rail ops pack create --name <name> --from <pack>`, edit the draft,
switch with `rail ops pack use` (human-only).

## The mental model in 60 seconds

- **Presentation is presentation**: it is never part of what you approve.
  Relabel a pack, retitle screens in `surface.yaml` → the human's approval
  stands. Change a transition, placement, preset or the `operating` block →
  `approval_mismatch`, and a human re-approves with
  `rail --actor human approve pack:<name>` (and `approve charter`).
- **Pack v2** adds an `operating` block (`unit`, `hierarchy`, `focus`, `queue`,
  `progress`), which is semantic and approved. A pack v1 loads with the
  neutral model and keeps its approval; nothing guesses what the pack meant.
- **Quality is workflow-owned.** Two workflows can share a status name and
  still demand different proof; the engine never branches on a gate name.
- **Agents never read `pack.yaml`.** The compiled views are the whole surface:

| Command | Answers |
|---|---|
| `rail types` | every document type, its workflow and use line |
| `rail next` | the operating queue (or one worker's card) |
| `rail model` | the compiled operating model, its focus and digests |
| `rail read show <slug>` | the item plus its workflow and operating card |
| `rail why <slug>` | the missing proof and the command that produces it |
| `rail explain <name>` | a preset, workflow or action in full |

## Commands you will actually use

Every command answers with one JSON envelope on stdout (progress on stderr)
and exits `0` / `1` / `2`. Global flags: `rail --store <path> --actor <who> --session-env <name> <command>`.
`rail` and `ctx-rail` are the same entry point; `rail` examples also run as
`python -m context_rail`.

| I want to… | Command |
|---|---|
| See where things stand | `rail overview` |
| Get my next card | `rail next` |
| Add work | `rail change add <preset> "<title>"` |
| Move work | `rail change move <slug> <status>` |
| Inspect one item | `rail read show <slug>` |
| Find broken references | `rail ops doctor` |
| Check gates | `rail gate` |
| Hand out work | `rail loop brief <item> --role implementer` |
| See what work cost | `rail ops measure usage --group-by model` |
| Approve a pack | `rail --actor human approve pack:<name> ...` |

Former flat names (`status`, `brief`, `review`, `ci`, …) still work;
`rail <group> --help` is the grouped surface.

## What work cost

The rail records effort; it never measures it. A candidate carries what the
tool reported:

```bash
rail loop candidate <item> --commit HEAD --model <name> --harness <tool> \
  --duration 5400 --usage tokens=152000 --usage requests=42
```

A result body may open with the same facts in a leading `---` block:

```yaml
---
duration_s: 5400
usage: {tokens: 152000, requests: 42}
model: <name>
harness: <tool>
---
```

The block stays in the stored body, so the body hash covers the numbers and
the review binds them. `usage` names physical counters only — `tokens`,
`input_tokens`, `output_tokens`, `requests`, `tool_calls` — never money. An
unknown key or an invalid counter refuses the write; `unknown` is allowed for
a counter the harness does not report. `--model`/`--harness` may not name the
same fact the block names.

`rail ops measure usage --group-by item|model|harness|role|profile|skill` rolls
that effort up over a window (`--item`, `--since`, `--until`, `--limit`).
Candidate and result join on the commit, once per attempt: a result bound to a
candidate commit supplies that attempt's effort field by field, and a result
with no commit is its own pending attempt. `attempts` still counts candidates;
seconds and usage counters sum the joined effort.
Each row reports candidates, pending, pass rate, mean attempts, mean duration
and usage totals; `total` covers the whole window. Rows are not additive: a
candidate pinned to two skills appears in both skill rows, and only `total`
counts it once.

`rail read digest` and `rail read ledger` show the same usage with unknown
counters kept apart, and the charter's `budgets` turn it into an
ok / near / exceeded state. A budget warns; the rail never refuses work for
being over budget. `rail ops measure versions` compares two versions of a skill
or profile, and `rail ops measure fix` asks whether a resolved fix's reports
came back.

Record proportionately: navigation is not recorded; a conclusion that will be
reused or will change a decision is a `task` plus a commitless result naming
the model and harness that produced it.

What this is not: no live timer, no token meter and no price table. Duration,
tokens and requests exist only because a tool reported them; anything unreported
stays `unknown` and is never counted as zero. Measurement is observational: it
never applies a change and never rolls one back.

## Surfaces

| Surface | Command | For |
|---|---|---|
| CLI | `rail` / `ctx-rail` | agents + humans |
| MCP | `rail serve mcp` | any MCP client (one stdio tool table) |
| Loopback UI | `rail serve ui` | humans: Flow/Plan/Records, token-gated, loopback-only |
| Daemon | `rail serve daemon` | one warm process per store; the CLI starts it, uses it and stops it after 10 idle minutes |

The daemon serves the everyday reads (`overview`, `read`, `next`, `types`,
`model`, `explain`, `why`, `gate`) and `change add|update|move|link`. A served
command skips loading the engine, which keeps it within 150 ms of bare Python
startup. Anything else, and any call that names a file or folder
(`--body-file`, `--spec-file`, ...), runs in your own process. Every call reads
the files afresh and acts as its caller (`--actor`, `RAIL_ACTOR`), never as the
process that started the daemon, so a result, a refusal and the actor on a
record match a direct call. Opt out with `RAIL_NO_DAEMON=1` or `--no-daemon`;
end it with `rail serve daemon --stop`; `RAIL_DAEMON_IDLE_S` changes the idle
time.

The UI carries the pack's labels and titles, renders the pinned populations,
and an empty screen says so instead of showing a blank page. No React module
branches on a pack name.

## Code graph

`rail ops codegraph setup|refresh|status` manage a derived cache of files,
symbols and call edges under the store's local directory, one snapshot per git
commit. Nothing is written to the store, reads never start a process, and a
missing snapshot degrades to file-level answers with one line saying so.
`rail.yaml` `code_graph` (`builtin`, `graphify`, `gitnexus` or `none`) wins
over the pack's `policy.code_graph`, which wins over the default `builtin`; the
video pack ships `none`.

`builtin` is the zero-install provider. `graphify` uses Graphify's code-only
graph (`uv tool install "graphifyy>=0.9.100,<0.10"`, Apache-2.0) and `gitnexus`
uses GitNexus (`npm install -g gitnexus`, Node 22.18+ or 24.11+); both are
optional upgrades. Pass
`--code-graph graphify` (or `gitnexus`, `builtin`, `none`) to `rail init` to
write the choice into `rail.yaml`; for an external provider init runs a
readiness probe once and reports the line at the end, without building a
snapshot or failing the init. The rail never installs either tool.
`rail ops codegraph setup` checks the binary, the Node runtime for GitNexus, the
supported version and any tool-owned leftovers, then builds the first snapshot.
The result is cached in
`<local_dir>/codegraph/readiness.json`; `status`, the overview, an approval
summary and `rail ops doctor` read that cache and never spawn a probe.

GitNexus writes `.gitnexus`, `.gitignore` entries and agent skills into the
repository it indexes, so the adapter gives it a detached **mirror** worktree
under the local cache and points `GITNEXUS_HOME` at the same cache. The real
checkout stays clean, and the global `~/.gitnexus/registry.json` is not used.
GitNexus is PolyForm Noncommercial 1.0.0; commercial use needs a separate
license, which `rail ops doctor` reports once when it is configured. Graphify
is Apache-2.0 and is run with only `--code-only`, with hooks and auto-refresh
disabled.

When a configured external tool is missing, out of date or broken, the brief
**falls back to `builtin`** and says why in the work order. A provider failure
never blocks a brief, a candidate or a review. Override a binary with
`RAIL_GRAPHIFY_BIN` or `RAIL_GITNEXUS_BIN`.

- **`loop brief`** writes a `code area` section into the work order and the
  candidate records `symbols_changed` with the files it changed.
- **Review** gets the changed symbols, their impact, anything outside the
  predicted area and tests that reach the change without being declared checks.
- **`rail why` / `rail read show`** accept `path` and `path:Symbol`.
- **Implementers never write the store.** They state decisions in
  `what_happened` and the CTO records them.

### Upgrading a store that has a code map

Stores made with the earlier packs keep working: while the implementer profile
still pins the `code-map` skill, a candidate keeps allowing `code` notes in a
candidate. `rail ops doctor` reports `code_map_not_adopted` (info). Run
`rail --actor human ops codegraph adopt` (`--dry-run` first if you like) to
rewrite the cto, implementer and reviewer profiles and the `review-checklist`
skill and to delete the `code-map` skill copies. A copy you edited is left
alone and listed under `customized` with its difference from the pack. Existing
`code` notes stay as hand-written intent notes.

## Upgrading an existing video store

The video pack no longer has a `render` item type, and the `queued`,
`running`, `succeeded` and `failed` statuses are gone: a render attempt is an
execution record on the approved shot. A store made before that change keeps
reading, but `rail ops doctor` reports `unknown_preset`, `unknown_status` and
`placement_unresolved` for every old render item and every item left on one of
those statuses, and `rail change move` refuses them. The pack change also
shows as `approval_mismatch` until a human re-approves it. The same reapproval
covers the pack's new `code_graph: none` policy. A team that later wants the
code graph turns it on with a `code_graph:` line in `rail.yaml`, runs
`rail ops codegraph setup`, and reapproves the manifest; the rail never
installs the external tool.

Repair it once, as the human:

1. In each `items/render/<slug>.md`, set `preset: task`, `tags: [task]` and a
   status the pack knows (`dropped` for a render you no longer need).
2. In any other item left on `queued`, `running`, `succeeded` or `failed`, set
   `draft`, `doing`, `done` or `blocked`.
3. `rail ops reindex`, then `rail change repath` to move the old render files
   into `items/task/`.
4. `rail ops doctor` should show no errors. Then
   `rail --actor human approve pack:video` and `rail --actor human approve charter`.

## Upgrading a v1/v2 store

```bash
rail --actor human ops migrate --source <old> --dir <project> --dry-run
rail --actor human ops migrate --source <old> --dir <project>
```

## Roles and seats

A pack's `operating` block, version 2, can declare roles: a lead, one lead per hierarchy level
(sprint lead, scene lead), an implementer and a reviewer, or any names the pack
chooses. A **seat** binds one agent to one role on one anchor - the whole
project or one container. Roles live in the approved pack, so widening a role
changes the semantic digest and needs re-approval.

```bash
rail seat grant impl-1 --role implementer --anchor send-invoice
rail seat list
rail seat open impl-1 -- rail next      # the child gets RAIL_SESSION
rail impl-1 next                        # read the store as that seat
rail seat close impl-1
```

In role mode the session token is the identity: `--actor` is refused. A
tokenless caller that is not on a terminal has no seat and an empty reach, so
open a session before reading or writing. `rail seat open <seat> --print-env`
prints the one-time token in its JSON result; export it as `RAIL_SESSION` for
the agent your tool starts. A seat sees only its anchor's
subtree, the ancestors and shared notes its role declares, and every write is
checked against the same reach. `rail <seat> ...` is a read-only lens when the
session belongs to another seat.

Human-only actions - using a pack, granting a `human: true` role, and the first
grant when no seat exists yet - need the human on a terminal or through the UI,
never an agent session. Approval is different: a project-anchored lead seat may
sign it with its own session when the charter allows it, and the record names
the signer and the human it was made for. A store whose pack
declares no roles keeps the name-based `--actor` behaviour unchanged; there
`rail seat ...` and the lens refuse with "this pack declares no roles" and point
at `rail ops pack use`.

To opt a pack into role mode, write a draft with starter roles and select it:

```bash
rail ops pack create --name mine --from scrum --roles
rail ops pack use --name mine --expect <rail-yaml-etag> \
  --confirm "USE PACK mine"
```

Run the pack switch yourself in a terminal: role mode refuses `--actor`, and a
session is never human. The generated roles are a starting point; edit them in
the draft before the new pack is approved. The human's first project seat
can then be granted; after that, a lead may grant only the roles its own
`can.grant` lists and only inside its write reach.

## Several projects

When several repositories need to land together, run one master store with the
`portfolio` pack (`rail init --pack portfolio`) and keep each child project in
its own repo and `context-rail/` store. The master store tracks cross-project
`component`, `slice`, `release`, and `contract` notes, while each child keeps
its own pack, workflow, and history — whether the child repos are git submodules
inside the master checkout or sibling folders next to it, and whether or not a
child uses roles.

The human grants each project's CTO a `component-cto` seat anchored on its
`component`. That seat plans and moves its own slices up to `integrating`,
declares and runs `store` checks bound to the child's committed items, and files
reports; `portfolio-lead` verifies slices through `item_done` and ships each
`release` through `release`.

Give each store its own session variable with `--session-env NAME` so a child
CTO keeps its child token in `RAIL_SESSION` and its master token in a named
variable such as `RAIL_SESSION_PORTFOLIO`:

```powershell
$env:RAIL_SESSION = (rail --store child/context-rail seat open cto-1 --print-env | ConvertFrom-Json).value.token
$env:RAIL_SESSION_PORTFOLIO = (rail --store master/context-rail seat open auth-cto --print-env | ConvertFrom-Json).value.token
rail --store ../master/context-rail --session-env RAIL_SESSION_PORTFOLIO next
```

## Layout

| Path | Owns |
|---|---|
| `src/context_rail/kernel/` | pure rules, no I/O |
| `src/context_rail/store/` | the only write path + SQLite cache |
| `src/context_rail/gateway/` | tool table, permissions, envelope |
| `src/context_rail/model/` | compiler, placement, operating model |
| `src/context_rail/packs/` | `basic`, `flow`, `scrum`, `waterfall`, `video`, `portfolio` |
| `web/` | React shell; built into `ui/static` and served by `rail serve ui` |
| `docs/agent-onboarding/` | portable onboarding kit for any harness |
| `skills/` | operator skills (`context-rail-v3*`) for agent skill directories |

## Engine invariants

Every mutation goes through `store.write.commit`; reads never run store commands
or hash source trees (the CLI transport may start the store's background
daemon); `kernel` is pure; unknown pack version, schema, policy
field → refused, never guessed. The full working rules are in
[AGENTS.md](../AGENTS.md).

## Development

```bash
python -m pip install -e .[test]
python -m pytest -q -m "not slow and not perf"          # functional lane
python -m pytest -q -m "perf and not slow" -p no:xdist  # speed budgets (serial)
python -m pytest -q -m slow -p no:xdist                 # 10k-document stores
```

Speed budgets are fixed; a change that needs more time is a bug in the change.
On Windows use `py` for `python`.
