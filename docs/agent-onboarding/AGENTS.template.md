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
| Loop | `loop brief\|result\|candidate\|review\|report` | Hand out work and record what any tool produced |
| Operate | `ops doctor\|reindex\|migrate\|rollback`, `ops pack`, `ops sync`, `ops adapters`, `ops measure usage\|versions\|fix`, `serve mcp\|ui\|daemon` | Maintain and connect |

`rail --help` is authoritative. Flat names from older versions still work
(`status`, `brief`, `candidate`, `review`, `check`, `verify`, `relocate`, `ci`,
`pack`, `sync`, `adapters sync`, `cto brief`, `mcp serve`, `ui serve`).

## Approval and human-only actions

`ops migrate`, `ops pack use`, `ops delegate`, `init`, `pack create` and a
`--force` render are always human-only. They need the human on a terminal or
through the UI (in a name-based store, `--actor human` or `RAIL_ACTOR=human`
plus the typed phrase). `ops upgrade`, `ops adapters render` and `ops rollback`
are available to the live project lead while the charter approval is current; a
`delegation` block may narrow or pin them to one named lead. Run only what the
approved charter allows and report the `delegation` facts.

`approve` records who signed it. By default a named lead may sign; the record
carries `signer`, `signer_kind`, `channel` and the human it names (`--confirmer`).
A charter that sets `approval.agents: false` accepts only a human signature:

```bash
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Name"
```

Never claim a human signature you did not receive. Name the human in
`--confirmer`, and do not lower a gate or switch a pack on the human's behalf.
The actor name is a workflow claim, not authentication.

## The agent loop

The rail does not run agents. Your tool does, with its own sub-agents. Once the
charter is approved (register one item per assignment; individual shell reads
are not separate assignments, and the rail never claims to detect unregistered
work):

```bash
# Research / commitless path:
rail loop brief <item> --role implementer          # the work order + compiled result shape
rail loop result <item> --body-file result.md --outcome completed --key <key>
rail check run <item>                              # when declared (incl. mode: result)
rail loop review <item> --verdict approve --notes-file review.md --key <key>   # a different actor
rail change move <item> done --expect <etag>

# Code path (commit before candidate, then commit-bound result):
rail loop candidate <item> --commit <sha> --key <key>
rail loop result <item> --body-file result.md --commit <sha> --key <key>
rail check run <item>
rail loop review <item> --commit <sha> --verdict approve --notes-file review.md --key <key>
rail change move <item> done --expect <etag>
rail loop report <item> --report-file report.json  # diagnostic channel
```

- `brief` is bounded and role-scoped (`4000`-char selected-body contribution,
  `5` history entries, `20` lead summaries). It names no tool.
- `result` writes one immutable workflow-shaped Markdown record (up to `256 KiB`
  / `262,144` bytes, ordered nonempty `##` headings, `--key` idempotency, nullable
  commit — never fabricate a commit for commitless work).
- `candidate` records the commit your tool made, with its changed files and,
  when you pass them, model, harness, usage and duration.
- `review` is a different actor's verdict (Markdown `--notes-file` when
  contract-enabled) on the current result / candidate commit; the author cannot
  approve its own work.
- You merge in git yourself. The rail records and gates; it never runs git
  writes for agent work. Existing stores adopt shipped result contracts via
  `rail ops upgrade` as the live lead while the charter approval is current;
  otherwise ask the human to run it on a terminal or through the UI, then
  `approve`.

Each step has its own key and its own gate. Do not collapse them.

## Effort and cost

The rail records effort; it never measures it. `loop candidate` accepts
`--model`, `--harness`, `--usage NAME=NUMBER|unknown` and `--duration SECONDS`.
A result body may open with a leading `---` block carrying `duration_s`,
`usage: {tokens: ..., requests: ...}`, `model` and `harness`; a counter the
harness did not report stays `unknown`, never zero, and money is not a counter.

`rail ops measure usage --group-by item|model|harness|role|profile|skill` rolls
the recorded effort up over a window. Budgets read the same joined effort and
warn; the rail never refuses work for being over budget. There is no live timer,
token meter or price table.

To steer a change, measure first, then compare with
`rail ops measure versions --target-class skill|profile --target <path> --before <n> --after <n>`,
and propose the change with `rail loop report`. A human decides.
`rail ops rollback` restores an earlier version as a new one when the target
and delegation allow; a protected target needs the human. Measurement never
applies or rolls back a change by itself.

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
| `approval_mismatch` | The semantic digest moved; reapprove `pack:<name>` then the charter (a named lead may sign when the charter allows it) |
| Reads disagree with files | `rail ops reindex`; never edit the index |
| Interrupted multi-file write | `rail ops repair` |

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
`candidate`, `result`, `review`, `brief`, `measure usage`, …), not the grouped CLI aliases. The serving actor
is `--actor`, then `RAIL_ACTOR`, then `cto`; a name-based store can pass
`--actor human` for a human-only tool, while a role-mode store needs the human
on a terminal or through the UI.

## Next

Read the harness's own instructions, then load the matching operator skill if
one is installed:

- `context-rail-v3-onboarding` — adopt the rail in a new project
- `context-rail-v3` — the map and the command families
- `context-rail-v3-workflows` — custom types, statuses, transitions, BA/PO
- `context-rail-v3-pack-authoring` — create or change a pack
- `context-rail-v3-surface` — work from the compiled surface only
- `context-rail-v3-loop` — brief, candidate, result, review, report
- `context-rail-v3-ops` — doctor, reindex, recover, ship

If no skill is installed, `rail --help`, `rail types`, `rail model`, `rail
explain <name>` and `rail why <slug>` answer most questions without reading the
engine.
