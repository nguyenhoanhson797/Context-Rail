# Command and tool reference

Two names exist for most operations:

- **CLI surface names** are the documented, grouped names (`rail read status`,
  `rail change add`).
- **Canonical tool names** are what the gateway, MCP server, stored profiles and
  recorded history use (`status`, `add`). MCP clients call these.

Both work on the CLI: every flat name is accepted and rewritten to its grouped
surface. MCP keeps the canonical names; do not send grouped aliases to an MCP
server.

## Global contract

```text
rail [--store PATH] [--actor NAME] [--session-env NAME] [--no-daemon] <command> [flags]
rail --version
```

- Global options go **before** the command.
- `rail --version` prints the version of the code it actually ran and exits.
  `pip show context-rail` reports the metadata for the interpreter pip runs
  in; if the two differ, two installs exist. Compare `py -m pip show
  context-rail` with the same launcher you use for `rail`, and remove the stale
  one rather than guessing which number is real.
- Without `--store`, the nearest `context-rail` folder above the current
  directory is used.
- `--actor` defaults to `RAIL_ACTOR`, then `human`; human-only tools require an
  explicitly named human (`--actor human` or `RAIL_ACTOR=human`). A store whose
  pack declares roles refuses `--actor`: the session token is the identity
  there.
- An approved charter authorizes its live project lead to run `ops upgrade`,
  `ops adapters render` and `ops rollback`. A `delegation` block may pin that to
  one named lead or narrow it to a list (`delegation: {to: <seat>, actions:
  [...]}`; omit `actions` for every operational action). Every use writes a
  `delegation.used` record naming the delegate and the human. `ops pack use`,
  `ops migrate`, `ops delegate`, `init`, `pack create` and a `--force` render
  stay human.
  Set or narrow it as the human (name mode: prefix `--actor human`; role mode:
  run on a terminal with no session), then approve the edited charter:

  ```bash
  rail ops delegate <seat> --expect <charter-etag> \
    --confirm "DELEGATE <seat>"
  rail approve charter --confirm "APPROVE charter" --confirmer "<you>"
  ```

  Repeat `--action` to narrow; remove the block with `--remove` to restore the
  live lead's default.
- `ops upgrade`, `ops adapters render` and `approve` also accept a chat
  attestation: the agent passes the human's exact words as
  `--user-approval "<their words>"` (plus `--approved-by <human>` for the two
  operational actions). The record names the human, the verbatim text and the
  agent, and marks the claim `verified: false`. It is evidence, not a
  signature; a charter with `approval.agents: false` still requires a human
  signature for `approve`.
- `--session-env NAME` reads the call's session token from environment variable
  `NAME` instead of `RAIL_SESSION`; invalid variable names or unset/empty
  values are refused without falling back.
- `--no-daemon` (or `RAIL_NO_DAEMON=1`) bypasses the loopback RPC daemon.
  `RAIL_DAEMON_TIMEOUT_MS` tunes the wait (default 2000 ms) and
  `RAIL_DAEMON_IDLE_S` the idle time before it exits (default 600 s).
- Every command prints one JSON envelope and exits `0` success, `1` refusal,
  `2` infrastructure failure.

```json
{"ok": true, "value": {}}
{"ok": false, "error": {"code": "CONFLICT", "message": "...", "details": {}}}
```

Writes accept `--key` for idempotency: the same key returns the same record.
Writes that change a document accept `--expect <etag>`; a stale etag is refused.

## Daily surface

```text
rail init [--dir DIR] [--pack NAME] [--root DIR] [--scaffold] [--dry-run] [--code-graph PROVIDER]
          [--no-approve] [--no-git-drivers] [--harness ID]... [--confirmer NAME]
          [--interview | --answers-file FILE | --draft-file FILE]
          [--solo] [--key KEY]
rail approve <target> --confirm "APPROVE <target>" --confirmer NAME [--user-approval "WORDS"] [--expect DIGEST]
rail consent <item> --on-behalf-of NAME --text "WORDS" --expect ETAG
rail overview [--max-chars N] [--item-limit N]
rail read status
rail read map [<item>] [--depth N] [--find WORDS] [--since ISO] [--max-chars N]
rail read digest [--since ISO] [--limit N] [--max-chars N]
rail read find [--primitive P] [--preset P] [--tag T] [--status S]
               [--category C] [--text Q] [--rel REL=SLUG] [--limit N]
rail read show <slug> [--path PATH] [--body-chars N]
rail read ledger [--item SLUG] [--profile NAME] [--since ISO] [--limit N]
rail model
rail types [--item SLUG] [--limit N]
rail next [--item SLUG] [--all] [--into CONTAINER] [--focus-only]
rail explain <name> [--limit N]
rail why <slug>
rail gate [--warnings-as-errors]
```

`approve` target is `charter`, a protected slug, or `pack:<name>`. Approval
binds to the semantic digest. A named lead signs by default; add `--actor human`
for a human signature. The record carries `signer`, `signer_kind`, `channel` and
`on_behalf_of`; `--confirmer` names the human the approval is for. A charter with
`approval.agents: false` accepts only a human signature. When an agent records
the approval from the human's chat answer, add `--user-approval "<their exact
words>"`: the record keeps that text and `verified: false`, and still refuses
when `approval.agents` is false.

`consent` records a human's explicit go-ahead for one item version, in their
own words: `--text` is what they said, `--on-behalf-of` names them, and
`--expect` is the item etag they saw. A named lead records it; the record names
the agent that called as `signer` and the human as `on_behalf_of`. An edit to
the item makes the consent stale, so a pack action that requires consent (such
as `approve-critical`) waits for a new one. `approval.agents: false` keeps
consent human-only too.

`init --answers-file` runs the same stages as the resumable pipeline: it writes
the picture and workspace, renders the selected agent harnesses, installs the
git drivers, and returns every stage plus the next steps. Add `--confirmer NAME`
with the `--confirm` phrase to record the charter approval in the same call;
without it, approval stays missing and `rail approve ...` does it later.

`overview` is the bounded daily page. `read status` is the machine view.
`read map` is the project map: the focus path, progress, next and the work
tree with markers and rollups; `<item>` reads one branch with its etag,
`--find` locates words with their branch, `--since` marks what changed.
`read show --path PATH` selects one document when several share a slug, such as
a skill copied into each profile. `types` is role-aware with `--item`. `next` is
focus-aware: `--all` ignores the queue scope but never a gate, claim or
permission; `--into` overrides the focus for one call; `--focus-only` filters.
`explain` accepts a preset, a workflow or an action.

`report` files the one structured report a run owes. `triage` clusters the
unclaimed reports by fingerprint; `--report ID --acknowledge` records a
deliberate acknowledgement for one report that has not reached the recurrence
threshold, writes a `report.triaged` record and clears the doctor warning.

## Roles and seats

```text
rail agent enable <seat> --role ROLE --anchor SLUG|project [--profile NAME]
rail agent show
rail agent disable

rail seat list [--all]
rail seat grant <seat> --role ROLE --anchor SLUG|project [--profile NAME] [--key KEY]
rail seat revoke <seat> [--key KEY]
rail seat open <seat> [--ttl-hours N] [--print-env]
rail seat close [<seat> | --all]
rail seat show [<seat>]
rail <seat> [command ...]
```

- A **role** comes from the pack's `operating.roles` (version 2). A **seat** is
  one role on one anchor: `project` or one container slug.
- `rail agent enable` is the one-step path for one local agent: it grants the
  seat if needed, resets its sessions and writes a local credential. The CLI and
  MCP server use that credential for non-terminal callers; the agent then runs
  commands directly, with no `RAIL_SESSION` and no `seat open`. It is human-only
  because it binds the store's authority. `rail agent show` reports the seat;
  `rail agent disable` closes and removes the credential. The credential is a
  local bearer token: any process that can read the store's local directory can
  act as the seat, so use explicit sessions for a shared machine or remote store.
- `rail seat grant` is human-only for a `human: true` role and for the first
  grant when no granting seat exists. After that a seat may grant only a role its
  own `can.grant` lists and only inside its write reach. Revoking a seat closes
  its sessions and every seat it granted.
- `rail seat open` writes a session. `--print-env` includes its one-time token
  in the JSON result; export it as `RAIL_SESSION` in the environment that runs
  the agent, never by pasting it into a conversation. Sessions expire after
  `--ttl-hours` (default 12).
- `rail <seat> ...` is a lens. With that seat's own session it reads and writes
  as the seat; with another seat's session it is read-only and allowed only when
  the target anchor is in the caller's read reach; with no session on a terminal
  it is a read-only view. A lens never widens the caller's reach.
- In role mode a caller with no session, no local agent credential and no
  terminal has read tools and an empty reach; enable the local agent or open a
  session to read or write. `--actor` is refused.
- Human-only actions need the human on a terminal or the UI channel; a session
  caller is never human. This covers `ops migrate`, `ops pack use`, `init`,
  `pack create`, a `human: true` grant and the first bootstrap grant. `ops
  upgrade`, `ops adapters render` and `ops rollback` are available to the
  caller's live lead seat while the charter approval is current; a `delegation`
  block may narrow or pin them. The lead runs them and a `delegation.used`
  record names the delegate and the human. `approve` is different: a
  project-anchored lead seat may sign it with its own session when the charter
  allows it (`approval.agents: true`, the default). For `ops upgrade`, `ops
  adapters render` and `approve`, an agent may instead pass the human's chat
  words as `--user-approval` (and `--approved-by`); the record marks the claim
  unverified and still refuses `approve` when `approval.agents: false`.
- A pack without `roles` stays in compat mode: the name-based `--actor`
  behaviour is unchanged and `rail seat ...` / the lens refuse with "this pack
  declares no roles", pointing at `rail ops pack use`.
- `rail ops pack create --roles` writes a draft with operating version 2 and
  starter roles, and does not select it. It refuses a version-1 source and an
  extending pack that has no own `operating` block. Select the draft with the
  human-only `ops pack use`.
- In a role-mode store, run `ops pack use` yourself in a terminal (or through
  the UI); `--actor` is refused and a session is never human. A project-anchored
  lead seat may sign `approve` with its own session; other seats cannot.

## Change surface

```text
rail change add <preset> "<title>" [--rel REL=SLUG]... [--tag TAG]...
                 [--set KEY=VALUE]... [--body-file FILE] [--confirm PHRASE]
                 [--key KEY]
rail change update <slug> --expect ETAG [--set KEY=VALUE]... [--tag TAG]...
                    [--body-file FILE] [--confirm PHRASE] [--key KEY]
rail change move <slug> <status> --expect ETAG [--resolution TEXT]
                 [--confirm PHRASE] [--key KEY]
rail change link <src> <rel> <dst> --expect ETAG [--remove | --replace] [--reason TEXT]
                 [--confirm PHRASE] [--key KEY]
rail change rename <old> <new> [--confirm "APPROVE <slug>"] [--key KEY]
rail change repath [--dry-run] [--confirm "APPROVE <slug>"] [--key KEY]
rail do <action> [--item SLUG] [--etag ETAG] [--set NAME=VALUE]... [--key KEY]
```

- `change add` is positional: preset, then title.
- `change update` tag edits use `+TAG` / `-TAG`.
- `change move` enforces the item's workflow; a move into a `done` category
  runs the charter's done gate.
- `change link` adds or removes one relation; `--reason` records a reversible
  decision. `--replace` makes `dst` the only target: with `part_of` it moves the
  whole branch, descendants included, to a new parent in one write.
- `change repath` lists and applies every move the current placement implies.
- `do` runs one declarative pack verb through the gateway as the caller.

## Checks, reports and the loop

```text
rail check declare <slug> --spec-file FILE --expect ETAG|absent [--target ITEM]
                    [--confirm "APPROVE <slug>"] [--key KEY]
rail check run <target> [--worktree PATH] [--verdict pass|fail]
                 [--evidence RECORD]... [--confirm PHRASE] [--key KEY]
rail loop brief <item> [--role implementer|reviewer] [--commit SHA] [--max-chars N]
rail loop result <item> --body-file FILE [--commit SHA] [--outcome completed|partial|blocked|failed]
                 [--set KEY=VALUE]... [--model TEXT] [--harness TEXT] [--key KEY]
rail loop candidate <item> --commit SHA [--base SHA] [--profile NAME] [--model TEXT]
                    [--harness TEXT] [--usage NAME=NUMBER|unknown]... [--duration SECONDS]
                    [--key KEY]
rail loop review <item> [--commit SHA] [--result RECORD_ID] --verdict approve|reject
                 [--notes-file FILE] [--set KEY=VALUE]... [--model TEXT] [--harness TEXT]
                 [--key KEY]
rail loop report [item] [--candidate SHA] --report-file FILE [--key KEY]
rail loop triage [--limit N] [--report ID] [--acknowledge] [--key KEY]
rail loop execution <target> --action NAME --state running|succeeded|failed|cancelled
                    [--output FILE]... [--error WHY] [--key KEY]
```

`check run` on an item runs every executable check it declares, including
`result` checks (file-only verification of the current completed producer
Markdown result) and `store` checks verified against committed items in child
stores. A `human` check needs `--verdict` and the typed phrase. The rail never
launches an agent: your tool does the work with its own sub-agents, then records
its Markdown result with `loop result` (preceded by `loop candidate` when code
changed; discovery/commitless work leaves `candidate_commit` null). Bodies are
capped at 256 KiB (`262,144` bytes) with ordered nonempty `##` headings. The
body may open with a leading `---` effort block (`duration_s`,
`usage: {tokens: ..., requests: ...}`, `model`, `harness`); `unknown` is
allowed for an unreported counter, money is not a counter, and
`--model`/`--harness` may not name a fact the block already names. You
merge in git yourself.

## Operations

```text
rail ops doctor [--repair] [--fix] [--dry-run]
rail ops reindex [--full]
rail ops upgrade [--dry-run] [--text] [--confirm "UPGRADE store"]
                 [--approved-by NAME --user-approval "WORDS"]
rail ops upgrade --all [--root DIR] [--dry-run] [--text]
                 [--confirm "UPGRADE store"]
                 [--approved-by NAME --user-approval "WORDS"]
rail --actor human ops migrate --source LEGACY --dir DIR [--root DIR] [--pack NAME] [--dry-run]
rail ops rollback --target-class skill|profile --target PATH --version N
                  --expect ETAG [--confirm "APPROVE <slug>"]
rail ops measure versions --target-class skill|profile --target NAME
                          --before N --after N [--since ISO] [--until ISO]
rail ops measure fix --item SLUG [--until ISO]
rail ops measure usage --group-by item|model|harness|role|profile|skill
                       [--item SLUG] [--since ISO] [--until ISO] [--limit N]
rail ops codegraph setup|refresh|status
rail --actor human ops codegraph adopt [--dry-run]
rail ops sync [--remote NAME] [--branch NAME] [--key KEY]
rail ops sync install
rail ops adapters render [--harness agents|claude|cursor|gemini]... [--dry-run]
                         [--approved-by NAME --user-approval "WORDS"]
                         [--force --confirm "FORCE ADAPTERS adapters"]
rail ops pack list
rail ops pack show --name NAME
rail ops pack validate --name NAME
rail ops pack create --name NAME [--from SOURCE] [--roles] [--key KEY]
rail --actor human ops pack use --name NAME --expect RAIL_YAML_ETAG [--map OLD=NEW]...
                  [--dry-run] --confirm "USE PACK <name>"
```

`doctor` is read-only unless `--repair`/`--fix`. `reindex` is a read-permission
tool that may write the index. `migrate` and `pack use` are human-only.
`upgrade --text` keeps the JSON on stdout and prints the plain summary on
stderr. `upgrade --all` scans one root (the current project by default) for
`rail.yaml`, prints a table with one row per store, and upgrades only the stores
that need no human decision; it also renders each safe store's adapters. A store
whose plan needs a charter approval or a conflict repair is reported and left
untouched. `--dry-run` plans the whole batch without writing.
`adapters render` writes the managed `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` and
`.cursor/rules/context-rail.mdc` blocks plus one `.agents/skills/<name>/SKILL.md`
per workspace skill. The `AGENTS.md` block holds a read-first table, the write
protocol, a "Start here" pointer and a skills index without bodies; the bodies
come with each session's brief.

## Transports

```text
rail serve mcp
rail serve ui [--port N]
rail serve daemon [--port N] [--idle SECONDS] [--stop]
rail sync driver <kind> <base> <ours> <theirs> <path>   # internal Git driver
```

A role-mode store's MCP server uses the same local agent credential as the CLI,
so start it from the non-terminal process that has the store; it needs no
`RAIL_SESSION`. A name-based store may pass `--actor cto`.

The CLI starts `serve daemon` on its own after the first eligible command, so
running it by hand is rarely needed. It serves the everyday reads (`overview`,
`read`, `next`, `types`, `model`, `explain`, `why`, `gate`) and `change
add|update|move|link`; any other command, and any call that names a file or
folder, runs in your own process. It exits after `--idle` seconds (default 600) without a request, and
`--stop` ends the one running for this store. If a command seems to act on old
data, run it with `--no-daemon` and report any difference.

`serve mcp` pins its actor from `--actor`, then `RAIL_ACTOR`, then `cto`.
Launch with `--actor human` only when that session must perform a human-only
tool.

## Canonical MCP tool names

MCP clients see the canonical table, generated from the same source as the CLI:

| Group | Canonical names |
|---|---|
| Setup | `init`, `migrate`, `approve` |
| Read | `cto brief`, `status`, `digest`, `find`, `show`, `ledger`, `types`, `next`, `model`, `explain`, `why` |
| Change | `add`, `update`, `move`, `link`, `rename`, `relocate`, `do` |
| Proof | `check`, `verify` |
| Loop | `brief`, `result`, `candidate`, `review`, `report`, `triage` |
| Ops | `doctor`, `ci`, `reindex`, `rollback`, `pack list`, `pack show`, `pack validate`, `pack create`, `pack use`, `measure versions`, `measure fix`, `measure usage`, `codegraph setup`, `codegraph refresh`, `codegraph status`, `codegraph adopt`, `sync`, `sync driver`, `adapters sync` |
| Agent | `agent enable`, `agent show`, `agent disable` |
| Seats | `seat list`, `seat grant`, `seat revoke`, `seat open`, `seat close`, `seat show` |
| Transports | `mcp serve`, `ui serve`, `serve daemon` |

Each tool declares a closed object schema, a `permission` (`read`, `write`,
`human`) and a `decision` (`cto-alone`, `cto-reversible`, `human-only`) in its
metadata. The envelope is the same as the CLI's. In role mode the MCP tool list
is scoped to the seat's role: read tools plus the write tools its role holds;
a legacy store and the human see every tool.

## Compatibility aliases

Every former flat name works: `status`, `digest`, `find`, `show`, `ledger`,
`add`, `update`, `move`, `link`, `rename`, `relocate`, `check`, `verify`,
`brief`, `candidate`, `review`, `report`, `triage`, `ci`, `doctor`, `reindex`,
`migrate`, `rollback`, `pack ...`, `measure ...`, `sync`, `adapters sync`,
`cto brief`, `mcp serve`, `ui serve`. The old launcher commands are removed and
name their replacement when called.

Hidden compatibility spellings: `inbox`, `read brief`, `repath`,
`sync install`, `adapters render`, `ops gate`, `ops sync driver`,
`ops adapters sync`.

## Error codes

| Code | Meaning |
|---|---|
| `NOT_FOUND` | no store, unknown slug, unknown pack |
| `VALIDATION_FAILED` | bad flags, illegal transition, wrong typed phrase |
| `CONFLICT` | etag changed, adapter conflict, duplicate identity |
| `INVALID_STATE` | non-executable item, immutable record, wrong recipe |
| `BUSINESS_RULE_VIOLATED` | a charter gate is not satisfied |
| `PERMISSION_DENIED` | the caller's role, reach or identity does not allow it |
| `SESSION_REQUIRED` | a role-mode caller has no session; enable the local agent or open a seat session |
| `NO_FOCUS` | the pack needs a parent container and none matches |
| `AMBIGUOUS_FOCUS` | several containers match; `details.candidates` names them |
| `INVALID_FOCUS` | `--into` is not a declared hierarchy container |
| `INFRA_FAILURE` | a process, git or filesystem failure |

A refusal always names the reason and the fix in `error.details`.
