<!-- Portable Context Rail agent instructions. Copy to a project root as AGENTS.md. -->

> This file is a portable template for a project that **uses** Context Rail. In
> the Context Rail package repository itself, the working rules are the root
> `AGENTS.md`; do not apply the product protocol to engine development.

# Context Rail

This project runs through Context Rail. The store folder (`context-rail/` by
default) beside this file is the truth: every change goes through `rail`, the
SQLite index is only a cache, and its Records are immutable. The same tool table
is exposed over the CLI, MCP and a loopback UI.

If `rail` is not on `PATH`, use `py -m context_rail` on Windows or
`python -m context_rail` elsewhere, and install with
`python -m pip install context-rail`.

## Read before you act

Never parse the store's `pack.yaml`, `rail.yaml` or the charter to infer the
workflow. Ask the compiled surface:

| Question | Command |
|---|---|
| What kinds of work does this project have? | `rail types` |
| What is executable now? | `rail next` |
| What is this item's workflow, state, legal moves and proof? | `rail read show <slug>` |
| What proof is still missing, and how do I produce it? | `rail why <slug>` |
| What does this preset, workflow or action mean in full? | `rail explain <name>` |
| What is the operating model, focus, unit and hierarchy? | `rail model` |
| What should a human look at first? | `rail overview` |
| What is blocked, and why? | `rail read status` |

Use `rail next --all` when the focus is not what you need, `rail next --into
<container>` to override the focus for one call, and `rail next --focus-only` to
stay inside it. Never infer the focus from item names or from a container's
children.

## The protocol for every change

1. **Read the current state.** `rail read show <slug>` returns the document, its
   relations, its workflow card and its etag.
2. **Write with the etag.** Pass `--expect <etag>`. A changed document is
   refused with `CONFLICT`; re-read and retry. Never guess an etag.
3. **Pass an idempotency key.** Use `--key <stable-key>` on every write. The
   same key returns the first result, so a retry is safe.
4. **Derive paths, do not supply them.** Pass `--rel part_of=<slug>` and
   `--set <field>=<value>`. Never pass a path, a parent folder or a final title;
   the resolver derives the path from the work graph.
5. **Change status only through the workflow.** Use `rail change move <slug>
   <status> --expect <etag>` or a declared `rail do <action>`. A move into a
   `done` category runs the charter's done gate.
6. **Prefer the write path.** The store is plain Markdown and a human may edit
   it, but an agent changes documents through `rail`, never by rewriting a
   status or a Record. After a hand edit, run `rail ops reindex` before the next
   command; never edit or delete the SQLite index itself.

Global options go **before** the command:

```bash
rail --store path/to/context-rail --actor cto read status
```

Every command prints one JSON envelope and exits `0` on success, `1` on a
refusal and `2` on an infrastructure failure:

```json
{"ok": true, "value": {}}
{"ok": false, "error": {"code": "CONFLICT", "message": "...", "details": {}}}
```

## What each family is for

| Family | Commands | Use |
|---|---|---|
| Read | `overview`, `read status\|digest\|find\|show\|ledger`, `types`, `next`, `model`, `explain`, `why` | Understand without changing anything |
| Change | `change add\|update\|move\|link\|rename\|repath`, `check declare\|run`, `loop report\|triage\|execution` | Do the work |
| Gate | `approve`, `gate`, `ops pack use` | Human decisions and governance |
| Loop | `loop dispatch\|review\|merge\|tick` | Run and supervise agents |
| Operate | `ops doctor\|reindex\|migrate\|rollback`, `ops pack`, `ops sync`, `ops adapters`, `ops measure`, `serve mcp\|ui\|daemon` | Maintain and connect |

`rail --help` is authoritative. Flat names from older versions still work
(`status`, `dispatch`, `review`, `merge`, `check`, `verify`, `relocate`, `ci`,
`pack`, `sync`, `adapters sync`, `cto brief`, `cto tick`, `mcp serve`,
`ui serve`).

## Human-only actions

`approve`, `ops migrate` and `ops pack use` are human-only. They need an
explicitly named human (`--actor human` or `RAIL_ACTOR=human`) and the typed
phrase the command prints:

```bash
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Name"
```

Do not approve, reapprove, lower a gate or switch a pack on the human's behalf.
Propose the decision and stop. The actor name is a workflow claim, not
authentication.

## The agent loop

Work that launches an agent is gated by the charter. Once it is approved:

```bash
rail loop dispatch <item> --expect <etag> --key <key>
rail loop review   <item> --run <run-id> --key <key>
rail loop merge    <item> --run <run-id> --expect <etag> --key <key>
```

- `dispatch` creates a fresh git worktree and branch, runs one implementer and
  verifies its candidate.
- `review` is a different actor in its own worktree; the implementer cannot
  approve its own work.
- `merge` rebases onto the default branch and fast-forwards only an approved
  candidate.
- `loop tick --key <key>` is one bounded CTO session behind a single-instance
  lock. It must be the outer session: it refuses when `RAIL_ACTOR` is set.

Each step has its own key and its own gate. Do not collapse them.

## When a command refuses

Read `error.code` and `error.details`; a refusal names the reason and the fix.

| Refusal | Fix |
|---|---|
| `CONFLICT` | Re-read (`rail read show <slug>`), then retry with the new etag |
| `AMBIGUOUS_FOCUS` | Two containers match; pass `--into <id>` from `error.details.candidates` |
| `NO_FOCUS` | The pack needs a parent; pass `--rel part_of=<container>` or start the container |
| `INVALID_FOCUS` | The slug is not a declared hierarchy container; use `rail model` |
| `BUSINESS_RULE_VIOLATED` | A gate is unsatisfied; `rail why <slug>` names the missing proof and command |
| `INVALID_STATE` | The item is not executable, is immutable, or the profile has the wrong recipe |
| `NOT_FOUND` | No store, unknown slug or unknown pack; check `--store` placement |
| A check is `stale` | Run `rail check run` again; a stale pass is not a pass |
| `approval_mismatch` | The semantic digest moved; a human must reapprove `pack:<name>` then the charter |
| Reads disagree with files | `rail ops reindex`; never edit the index |
| Interrupted multi-file write | `rail ops doctor --repair` |

## Safety rules

- Reads never run store commands and never hash a source tree. The CLI transport
  may start the store's background daemon.
- The kernel is pure; do not add I/O to it.
- Every store mutation goes through `context_rail.store.write.commit`.
- Unknown manifest schemas, pack versions, keys and policy fields are refused,
  never guessed.
- A check is fresh only while its declared inputs still match.
- A record is history: it is never rewritten.
- Quality belongs to the workflow. A status may name a charter gate and the
  checks that gate selects; two workflows may share a status name and require
  different proof.

## Transports

```bash
rail --actor cto serve mcp      # the same tool table over MCP stdio
rail serve ui                   # loopback, token-gated UI
rail serve daemon --stop        # the CLI starts its warm daemon itself; this ends it
```

MCP keeps the canonical tool names (`status`, `next`, `show`, `add`, `move`,
`dispatch`, `review`, `merge`, …), not the grouped CLI aliases. The serving actor
is `--actor`, then `RAIL_ACTOR`, then `cto`; launch with `--actor human` when a
session must perform a human-only tool.

## Next

Read the harness's own instructions, then load the matching operator skill if
one is installed:

- `context-rail-v3-onboarding` — adopt the rail in a new project
- `context-rail-v3` — the map and the command families
- `context-rail-v3-workflows` — custom types, statuses, transitions, BA/PO
- `context-rail-v3-pack-authoring` — create or change a pack
- `context-rail-v3-surface` — work from the compiled surface only
- `context-rail-v3-loop` — dispatch, review, merge, supervise
- `context-rail-v3-ops` — doctor, reindex, recover, ship

If no skill is installed, `rail --help`, `rail types`, `rail model`, `rail
explain <name>` and `rail why <slug>` answer most questions without reading the
engine.
