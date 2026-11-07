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
rail [--store PATH] [--actor NAME] [--no-daemon] <command> [flags]
```

- Global options go **before** the command.
- Without `--store`, the nearest `context-rail` folder above the current
  directory is used.
- `--actor` defaults to `RAIL_ACTOR`, then `human`; human-only tools require an
  explicitly named human (`--actor human` or `RAIL_ACTOR=human`).
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
rail init [--dir DIR] [--pack NAME] [--root DIR] [--scaffold] [--dry-run]
          [--no-approve] [--no-git-drivers] [--harness ID]...
          [--interview | --answers-file FILE | --draft-file FILE]
          [--solo] [--key KEY]
rail --actor human approve <target> --confirm "APPROVE <target>" --confirmer NAME [--expect DIGEST]
rail overview [--max-chars N] [--item-limit N]
rail read status
rail read digest [--since ISO] [--limit N] [--max-chars N]
rail read find [--primitive P] [--preset P] [--tag T] [--status S]
               [--category C] [--text Q] [--rel REL=SLUG] [--limit N]
rail read show <slug> [--body-chars N]
rail read ledger [--item SLUG] [--profile NAME] [--since ISO] [--limit N]
rail model
rail types [--item SLUG] [--limit N]
rail next [--item SLUG] [--all] [--into CONTAINER] [--focus-only]
rail explain <name> [--limit N]
rail why <slug>
rail gate [--warnings-as-errors]
```

`approve` target is `charter`, a protected slug, or `pack:<name>`. Approval
binds to the semantic digest.

`overview` is the bounded daily page. `read status` is the machine view.
`types` is role-aware with `--item`. `next` is focus-aware: `--all` ignores the
queue scope but never a gate, claim or permission; `--into` overrides the focus
for one call; `--focus-only` filters. `explain` accepts a preset, a workflow or
an action.

## Change surface

```text
rail change add <preset> "<title>" [--rel REL=SLUG]... [--tag TAG]...
                 [--set KEY=VALUE]... [--body-file FILE] [--confirm PHRASE]
                 [--key KEY]
rail change update <slug> --expect ETAG [--set KEY=VALUE]... [--tag TAG]...
                    [--body-file FILE] [--confirm PHRASE] [--key KEY]
rail change move <slug> <status> --expect ETAG [--resolution TEXT]
                 [--confirm PHRASE] [--key KEY]
rail change link <src> <rel> <dst> --expect ETAG [--remove] [--reason TEXT]
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
  decision.
- `change repath` lists and applies every move the current placement implies.
- `do` runs one declarative pack verb through the gateway as the caller.

## Checks, reports and the loop

```text
rail check declare <slug> --spec-file FILE --expect ETAG|absent [--target ITEM]
                    [--confirm "APPROVE <slug>"] [--key KEY]
rail check run <target> [--worktree PATH] [--verdict pass|fail]
                 [--evidence RECORD]... [--confirm PHRASE] [--key KEY]
rail loop report [item] --run RUN_ID --report-file FILE [--key KEY]
rail loop triage [--limit N] [--report ID] [--key KEY]
rail loop execution <target> --action NAME --state running|succeeded|failed|cancelled
                    [--output FILE]... [--error WHY] [--runner-digest DIGEST] [--key KEY]
rail loop dispatch <item> --expect ETAG --key KEY [--profile NAME] [--all]
rail loop review   <item> --run RUN_ID --key KEY [--profile NAME]
rail loop merge    <item> --run RUN_ID --expect ETAG --key KEY [--push]
rail loop tick --key KEY [--timeout SECONDS]
```

`check run` on an item runs every executable check it declares. A `human` check
needs `--verdict` and the typed phrase. `loop tick` must be the outer session and
refuses when `RAIL_ACTOR` is set.

## Operations

```text
rail ops doctor [--repair] [--fix] [--dry-run]
rail ops reindex [--full]
rail --actor human ops migrate --source LEGACY --dir DIR [--root DIR] [--pack NAME] [--dry-run]
rail ops rollback --target-class skill|profile --target PATH --version N
                  --expect ETAG [--confirm "APPROVE <slug>"]
rail ops measure versions --target-class skill|profile --target NAME
                          --before N --after N [--since ISO] [--until ISO]
rail ops measure fix --item SLUG [--until ISO]
rail ops codegraph setup|refresh|status
rail --actor human ops codegraph adopt [--dry-run]
rail ops sync [--remote NAME] [--branch NAME] [--key KEY]
rail ops sync install
rail ops adapters render [--harness agents|claude|cursor|gemini]... [--dry-run]
                         [--force --confirm "FORCE ADAPTERS adapters"]
rail ops pack list
rail ops pack show --name NAME
rail ops pack validate --name NAME
rail ops pack create --name NAME [--from SOURCE] [--key KEY]
rail --actor human ops pack use --name NAME --expect RAIL_YAML_ETAG [--map OLD=NEW]...
                  [--dry-run] --confirm "USE PACK <name>"
```

`doctor` is read-only unless `--repair`/`--fix`. `reindex` is a read-permission
tool that may write the index. `migrate` and `pack use` are human-only.
`adapters render` writes the managed `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` and
`.cursor/rules/context-rail.mdc` blocks plus one `.agents/skills/<name>/SKILL.md`
per workspace skill.

## Transports

```text
rail serve mcp
rail serve ui [--port N]
rail serve daemon [--port N] [--idle SECONDS] [--stop]
rail sync driver <kind> <base> <ours> <theirs> <path>   # internal Git driver
```

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
| Loop | `dispatch`, `review`, `merge`, `report`, `triage`, `cto tick` |
| Ops | `doctor`, `ci`, `reindex`, `rollback`, `pack list`, `pack show`, `pack validate`, `pack create`, `pack use`, `measure versions`, `measure fix`, `codegraph setup`, `codegraph refresh`, `codegraph status`, `codegraph adopt`, `sync`, `sync driver`, `adapters sync` |
| Transports | `mcp serve`, `ui serve`, `serve daemon` |

Each tool declares a closed object schema, a `permission` (`read`, `write`,
`human`) and a `decision` (`cto-alone`, `cto-reversible`, `human-only`) in its
metadata. The envelope is the same as the CLI's.

## Compatibility aliases

Every former flat name works: `status`, `digest`, `find`, `show`, `ledger`,
`add`, `update`, `move`, `link`, `rename`, `relocate`, `check`, `verify`,
`dispatch`, `review`, `merge`, `report`, `triage`, `ci`, `doctor`, `reindex`,
`migrate`, `rollback`, `pack ...`, `measure ...`, `sync`, `adapters sync`,
`cto brief`, `cto tick`, `mcp serve`, `ui serve`.

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
| `NO_FOCUS` | the pack needs a parent container and none matches |
| `AMBIGUOUS_FOCUS` | several containers match; `details.candidates` names them |
| `INVALID_FOCUS` | `--into` is not a declared hierarchy container |
| `INFRA_FAILURE` | a process, git or filesystem failure |

A refusal always names the reason and the fix in `error.details`.
