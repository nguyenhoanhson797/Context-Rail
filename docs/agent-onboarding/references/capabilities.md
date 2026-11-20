# Capabilities — the full power map

This is the model an advanced agent needs. It is descriptive: the compiled
surface (`rail model`, `rail types`, `rail explain`) is always authoritative.

## Layers

| Layer | Owns | Where |
|---|---|---|
| Domain model | presets, fields, workflows, quality gates, actions | `pack.yaml` |
| Operating model | unit, hierarchy, focus, queue, progress, execution relevance | `pack.yaml` `operating` |
| Execution engine | commands, gateway, checks, runs, policies | this package |
| Projection | CLI read models, MCP, UI, briefs, generated agent files | one compiled model per call |

The compiler validates the pack and its optional presentation, then produces one
immutable model. Commands, agents and the UI read that model. None of them parse
`operating` or `surface.yaml` themselves.

## Fixed vocabulary

**6 primitives**

| Primitive | Meaning |
|---|---|
| `item` | work that moves through a workflow |
| `note` | durable knowledge, including skills, vision and Won't |
| `record` | immutable history: runs, verdicts, approvals, events, decisions |
| `check` | declared proof with a spec and a freshness rule |
| `actor` | a profile: prompt, recipe, methods, budgets, skills |
| `policy` | the charter: gates, decision rights, budgets |

**8 relations**

`part_of`, `depends_on`, `verifies`, `about`, `because_of`, `supersedes`,
`resolves`, `owned_by`.

**5 categories**

`not_started`, `active`, `blocked`, `done`, `dropped`.

A pack translates its own words (`todo`, `storyboard`, `po_review`) into these
categories. The engine never branches on a status name, a tag, a preset name or
a pack name.

## Documents and Records

- A document is a Markdown file with frontmatter: slug, title, preset, status,
  tags, relations, timestamps, digest.
- A Record is append-only history under `records/`. It is never rewritten.
- A document's identity is its slug, store-wide. A title edit never changes the
  slug.
- A document's status is managed: use `rail change move` or a declared action.
- An etag is the current content hash. Every write names the etag it read.

## Pack anatomy

```text
packs/<name>/
  pack.yaml        # name, version, statuses, transitions, presets, policy, operating
  layout.yaml      # optional: seed placement + preset placements
  surface.yaml     # optional presentation: labels, screens, fields
  profiles/        # cto, implementer, reviewer, ...
  skills/          # worker skills copied into the store's workspace
  templates/       # one body template per primitive or preset
```

`pack.yaml` is read live on every command. `profiles/`, `skills/`, `templates/`
and `layout.yaml` are a seed: `init` copies them into the store, and editing the
pack later only affects new stores. `rail.yaml` is authoritative once a store
exists.

Unknown keys are refused. A pack that cannot compile does not run.

Every pack must declare the presets the engine reads by name: `charter`, `cto`,
`decision`, `goal`, `note`, `profile`, `scope`, `skill`, `vision`, `wont`.
Every preset's `template` must exist.

## Workflows and quality

A pack is one project type with many named workflows over one status dictionary.
A preset selects one workflow with `workflow: <name>`.

```yaml
workflows:
  sprint:
    initial: todo
    transitions: {todo: [doing], doing: [review, done], review: [done], done: []}
    execution: {review: {queue: false}}
    quality:
      done: {gate: item_done}
  bugfix:
    initial: reported
    transitions: {reported: [fixing], fixing: [verifying], verifying: [closed], closed: []}
    quality:
      closed: {gate: bug_done, checks: [repro-check]}
```

- `statuses` is pack-wide: one status name, one category.
- The top-level `initial`/`transitions`/`execution` are the `default` workflow.
- `container: true` marks a phase, sprint, epic, goal or scope: work that is not
  executable. `container: false` marks a unit. `null` keeps the old inference.
- A quality rule names a charter gate and optionally the checks it selects.
  Moving into that status evaluates the gate; missing or stale proof refuses and
  names the command that produces it.
- `execution: {queue: false}` removes a status from executable work and from
  `rail next`. The old `dispatch`, `merge` and `policy.retries` keys are
  refused with a hint to run `rail ops upgrade`.

## The operating model (pack version 2)

One optional semantic block. Version 1 packs load with a neutral model and may
not declare it.

```yaml
operating:
  version: 1
  unit: shot
  hierarchy:
    - {preset: scene, order: sequence, required: true}
  focus: {preset: scene, category: active, order: sequence, resolve: unique}
  queue: {presets: [shot], require: transition, focus: priority}
  progress: {group: scene, of: [shot], complete: {states: [approved]}}
  execution: {actions: [render]}
```

| Key | Contract |
|---|---|
| `unit` | the canonical work item and default board |
| `hierarchy` | container presets, outermost first; supplies the plan order and `add`'s default parent |
| `focus` | the container the project is working in; `resolve` is `unique`, `first`, `explicit` or `none` |
| `queue` | item presets, `require: transition` or `action`, `focus: priority` or `only` |
| `progress` | declared done-category states, grouped along the hierarchy |
| `execution` | declared actions whose runs count as execution progress |

Focus is the container's **own** status, not an inference from its children. A
`unique` focus refuses an ambiguity with candidates; `--into <id>` and `--all`
are the explicit escapes. Neither bypasses a gate, a claim or actor permissions.

Workflow completion counts declared `done`-category states. Execution progress
comes from execution records only and is never merged with workflow completion.

## Roles, seats and sessions

A pack's version-2 `operating` block may declare `roles`. A role is a name
mapped to fixed keys: `anchor` (`project`, `{level: CONTAINER}`,
`{item: [PRESET, ...]}`), `reach` (read/write store terms and code patterns),
`can` (canonical methods), `sees` (brief recipe), `workspace`
(`none`/`read`/`write`), `independent_of`, `human` and `skills`. Roles are semantic: changing one changes the digest and
needs re-approval. The engine never branches on a role name.

A **seat** binds one role to one anchor. Seats are engine records folded into
the index; grant and revoke records are immutable. The live table cascades a
revoke through every seat the target granted. A run seat is implicit
(`<role>@<run-id>`) and lives only while the run is open.

Identity resolution in role mode:

1. A `RAIL_SESSION` token (or the variable named by `--session-env NAME`, with
   no fallback to `RAIL_SESSION`) resolves to its seat. The token is stored only
   as a SHA-256 hash under the local directory (never in the store tree).
2. A terminal human with no token is the human. `ui serve` is human-only; the
   UI's per-run channel is the other human proof.
3. Anything else is anonymous: read tools only, with an empty reach.

`--actor` is refused. Human-only actions (`ops migrate`, `ops pack use`, `ops
delegate`, `init`, `pack create`, a `human: true` grant, the first grant) need
the terminal/UI proof, never a session token. `ops upgrade`, `ops adapters render` and `ops
rollback` are available to the caller's live lead seat while the charter approval
is current; a `delegation` block may narrow or pin them. The seat runs them and
the use is recorded.
`approve` is different: a project-anchored lead seat may sign it with its own
session when the charter allows it (`approval.agents: true`, the default). The
first grant when no seat exists is human-only; after that a seat grants only
inside its `can.grant` and write reach.

Commands: `seat list|grant|revoke|open|close|show` and the `rail <seat>` lens.
The lens is read-only unless the session belongs to that seat, and it never
widens the caller's reach. MCP lists read tools plus the role's write tools; a
legacy store and the human see the whole table.

A pack without roles is compat mode: the existing name-based `--actor`
behaviour, no session requirement. `seat` commands and the lens refuse with
"this pack declares no roles" and point at `rail ops pack use`. `pack create
--roles` writes a starter role block and operating version 2 into a draft.

## Placement and metadata

`rail.yaml` maps primitives and presets to path templates:

```yaml
placement:
  item: "items/{preset}/{slug}.md"
  record: "records/{yyyy}-{mm}/{id}.json"
presets:
  sprint: "{part_of}/{slug}.md"
  task: "{part_of}/{field:area}/{slug}.md"
  result: "records/{about}/{id}.json"
  "kind:report.filed": "records/reports/{id}.json"
```

Tokens: `{slug}`, `{preset}`, `{id}`, `{yyyy}`, `{yy}`, `{mm}`, `{dd}`,
`{field:name}`, `{kind}`, `{part_of}`, `{about}`, `{verifies}`,
`{part_of.slug}`, `{nearest:preset}`, `{root:preset}`, `{item}`, `{item.slug}`.

Rules:

- A primitive placement starts with a literal segment; a preset placement may
  start with a path token and inherit the parent's folder.
- Every token-bearing template needs `{slug}` or `{id}`.
- A record stays under `records/`; a document may not enter it.
- `{item.*}` is only for a `kind:<kind>` record placement; `{kind}` is
  record-only.
- `skill`, `profile`, `cto` and `actor` are fixed to their `workspace/` shapes.
- Safe segments: no `..`, no separators, no NUL, no Windows device names, 80
  characters per segment and 200 per path.
- A path-affecting edit moves the document and every descendant in one commit;
  records move with them and are never rewritten.
- Presets may declare `required` fields and `defaults`; `add` merges defaults and
  refuses a missing value; `update` revalidates.

Agents never pass a path. They pass `--rel` and `--set`; the resolver derives the
path from the work graph.

## Checks and evidence

A Check is a document with a strict JSON spec and a freshness rule. Its eight
modes are `cmd` (command exit), `exists` (relative files), `environment` (a
command probe whose verdict is informational and never a gate proof),
`evidence` (declared
proof), `review` (separated candidate review), `result` (completed producer
result freshness, checked from files/index without git), `human` (human
sign-off) and `store` (committed child-store item category, checked against
child `HEAD` and item etag without running git). Every Check also sets
`acceptance: true|false`. `rail check declare` creates or edits it;
`rail check run` executes it and records a verdict. A stale verdict is not a
pass.

The charter has four gates (`item_done`, `scope_done`, `merge`, `release`) and
four booleans per gate (`require_checks`, `require_acceptance`,
`require_review`, `require_human`). A workflow names a gate; the gate selects
the proof. Workflow result contracts also enforce a current completed producer
Markdown result (`256 KiB` / `262,144` bytes max, ordered nonempty `##`
headings, `4000`-char brief contribution cap, `5` history entries, `20` lead
summaries, nullable commit, role-scoped reach, and explicit adoption via `ops
upgrade` + `approve`; the charter may delegate that upgrade to the lead).

## The agent loop

```text
Research: Brief → Work → Result → Verify (if declared) → Review → Move → Report → Digest
Code:     Brief → Work/commit → Candidate → Result --commit → Verify → Review → Move → Report → Digest
```

| Step | Guarantee |
|---|---|
| `loop brief` | a bounded, role-scoped work order for one item (4000-char selected result body, 5 history entries, 20 summaries); names no tool and starts nothing |
| `loop candidate` | records the commit your tool made for code work: changed files, optional model, harness, usage and duration |
| `ops measure usage` | rolls recorded effort up by item, model, harness, role, profile or skill over a window; unknown counters stay apart and no price is derived |
| `ops measure versions` | compares two versions of a skill or profile from their recorded attempts; observational only |
| `loop result` | records one immutable workflow-shaped Markdown result (`256 KiB` / `262,144` bytes max, `--key` idempotency, nullable commit); one registered item per assignment |
| `check run --worktree` | runs the item's checks in any git worktree and binds the result to the commit (or current result record for `mode: result`) |
| `loop review` | a different actor's verdict (structured Markdown `--notes-file` when contract-enabled) on the current result / candidate; the author cannot approve its own work |
| `move ... done` | the charter's gates read the result, candidate, check and review records; a stale commit or attempt refuses |
| `loop report` | one diagnostic report per item/candidate: target, expectation, observation, impact, evidence |
| `loop execution` | record one attempt (`running`, then `succeeded`, `failed` or `cancelled`) of a pack-declared execution action, such as a video render; the engine records it and never runs it |
| `read digest` | counts, waits, reversible decisions, quality, usage |

Effort is what the tool reported on `candidate` or in a result's leading `---`
block, never a live measurement. Unknown counters stay apart from known sums,
money is not a counter, and a budget warns rather than refuses.

Steering is human-gated. `ops measure usage` shows where effort went,
`ops measure versions` compares two versions of a skill or profile, and
`ops rollback` restores an earlier version as a new one. A protected target
needs the human, and measurement never applies or rolls back a change by itself.

Every write uses an idempotency key. A retry returns the same record. The rail
never launches an agent and never runs git writes for agent work: your tool
runs its own sub-agents and merges in git, and the rail records and gates.

## Profiles, skills and actions

- A profile is a store document at `workspace/profiles/<name>/profile.md`.
- `recipe` is `cto`, `implementer` or `reviewer`. A candidate records only an
  implementer; a review only a reviewer. A profile names no model, effort,
  autonomy, runner or tools.
- A BA role is a profile with `recipe: implementer` and a BA prompt.
- Skills are versioned Markdown notes. `rollback` restores a retained version.
- Actions are declarative verbs: one allowlisted gateway tool, run as the
  caller. `rail do <action>` records one `action.executed`. A verb cannot change
  the actor, a path, an etag or a profile.

## Transports

| Transport | Command | Notes |
|---|---|---|
| CLI | `rail ...` | grouped surface; flat aliases still accepted |
| MCP stdio | `rail --actor cto serve mcp` | canonical tool names, one JSON envelope per call |
| UI | `rail serve ui` | loopback, token-gated, reads one compiled snapshot |
| Daemon | `rail serve daemon` | one warm process per store; the CLI starts it and connects over 127.0.0.1 |
| Git | `rail ops sync` | integrates and publishes store-only clone changes |

The CLI starts a daemon on demand, sends it the everyday reads and `change
add|update|move|link`, and falls back to a direct call on any miss. The daemon
acts as the caller, never as the process that started it, and exits after 10
idle minutes. `--no-daemon` or `RAIL_NO_DAEMON=1` bypasses it;
`rail serve daemon --stop` ends it.

## Digests and approval

`rail ops pack show --name <pack>` reports:

| Digest | Covers | Use |
|---|---|---|
| `semantic_digest` | statuses, workflows, quality, presets, fields, actions, operating, placement, policy | approval and index compatibility |
| `presentation_digest` | labels, screens, card/filter/column fields | UI and API cache |
| `full_digest` | canonical bytes of both | diagnostics |

A label, screen or card change touches only the presentation digest. An
operating, transition, preset or placement change is semantic and reports
`approval_mismatch` until it is reapproved (a named lead may sign when the
charter allows it).

## State and lifecycle

| State | Location | Durable? |
|---|---|---|
| Store | `<project>/context-rail/` | yes, commit it |
| Index | worktree git dir or `<store>/.rail-local/` | no, rebuildable |
| Control files | same local dir | machine-local, atomic |
| Generated agent files | project root | managed block, safe to regenerate |
| Records | `<store>/records/` | immutable history |

## Speed budgets (fixed, tested)

| Operation | 1,000 docs | 10,000 docs |
|---|---|---|
| CLI startup | < 150 ms | < 150 ms |
| Warm read | < 300 ms | < 1 s |
| Cold rebuild | < 10 s | < 60 s |
| Single write | < 200 ms | < 200 ms |

A change that needs more time is a bug in the change. Never raise the budget.

## Extension points, in order of safety

1. **A pack** — new presets, fields, workflows, quality rules and verbs.
2. **A layout** — new placement and required metadata.
3. **A surface** — labels, screens and fields, no semantic change.
4. **A profile or skill** — agent behaviour and reusable methods.
5. **An action** — a declarative verb over allowlisted tools.
6. **Engine code** — only for a new automatic rule; it must keep the kernel pure
   and the write path single.
