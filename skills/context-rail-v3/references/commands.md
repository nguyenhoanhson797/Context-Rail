# Command reference

Global options, before the command: `[--store PATH] [--actor NAME] [--session-env NAME] [--no-daemon]`.
`rail --version` prints the version of the code it ran. `pip show context-rail`
reports the metadata for pip's interpreter; if the two differ, two installs
exist, so compare `py -m pip show context-rail` with the launcher you use for
`rail`.
Human-only tools (`ops migrate`, `ops upgrade`, `ops pack use`, `ops delegate`)
need an explicitly named human: `--actor human` or `RAIL_ACTOR=human`.
`approve` records its signer: a named lead may sign when the charter allows it
(`approval.agents: true`, the default); `approval.agents: false` requires a human
signature. A charter may instead delegate named operational actions to one lead;
the delegate then runs only those actions, and each use is recorded. A role-mode
store refuses `--actor`: open a seat session instead.
`--session-env NAME` (`^[A-Z_][A-Z0-9_]*$`) reads the session token for that call
(and pins `rail --session-env NAME serve mcp`) from `NAME` instead of
`RAIL_SESSION`; if the name is malformed (`session_env_invalid`) or that variable
is unset or empty (`session_env_unset`), the call refuses with `VALIDATION_FAILED`
rather than falling back to `RAIL_SESSION`.
Every command accepts `--key` for idempotency where it writes.
Exit codes: `0` success, `1` refusal, `2` infrastructure failure.

## Daily

```text
rail init [--dir DIR] [--pack NAME] [--root DIR] [--scaffold] [--dry-run] [--code-graph PROVIDER]
          [--no-approve] [--no-git-drivers] [--harness ID]... [--confirmer NAME]
          [--interview | --answers-file FILE | --draft-file FILE]
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
rail change add <preset> "<title>" [--rel REL=SLUG]... [--tag TAG]...
                 [--set KEY=VALUE]... [--body-file FILE] [--confirm PHRASE]
rail change update <slug> --expect ETAG [--set KEY=VALUE]... [--tag TAG]...
                    [--body-file FILE] [--confirm PHRASE]
rail change move <slug> <status> --expect ETAG [--resolution TEXT]
                 [--confirm PHRASE]
rail change link <src> <rel> <dst> --expect ETAG [--remove | --replace] [--reason TEXT]
rail change rename <old> <new> [--confirm "APPROVE <slug>"]
rail change repath [--dry-run] [--confirm "APPROVE <slug>"]
```

`status` is managed: change it only with `change move`. `change move` enforces
the pack's transitions, and a move into a `done` category runs the charter's
done gate.

`next` is focus-aware: it defaults to the operating queue, `--all` reaches every
item preset (still respecting transitions, gates and actor rights), `--into`
overrides the focus for one call, and `--focus-only` filters. `add` gives a unit
its focus container as `part_of` unless `--rel part_of=...` is explicit.
`model` prints the compiled operating model and its three digests.
`read map` is the project map: bare, the focus path, progress, next and the work
tree; with `<item>`, that branch with its path, etag, dependencies, attachments,
siblings and subtree; `--find` lists every hit with its branch; `--since` marks
and lists what changed. `change link --replace` makes `dst` the only target;
with `part_of` it moves the whole branch and its files in one write.
`read show --path PATH` selects one document when several share a slug, such as
a skill copied into each profile.

`init --answers-file` runs the full onboarding pipeline and reports each stage
plus the next steps. Add `--confirmer NAME` with the `--confirm` phrase to
record the charter approval in the same call; otherwise approve separately.

## Roles and seats

```text
rail seat list [--all]
rail seat grant <seat> --role ROLE --anchor SLUG|project [--profile NAME] [--key KEY]
rail seat revoke <seat> [--key KEY]
rail seat open <seat> [--ttl-hours N] [--print-env]
rail seat close [<seat> | --all]
rail seat show [<seat>]
rail <seat> [command ...]
```

A role is declared in a pack's version-2 `operating.roles`; a seat binds one
role to one anchor (`project` or a container).

- `seat grant` is human-only for a `human: true` role and for the first grant
  when no granting seat exists. A live seat may grant only inside its
  `can.grant` and write reach. `seat revoke` closes the target's sessions and
  every seat it granted.
- `seat open` writes a session. `--print-env` includes its one-time token in the
  JSON result; export it as `RAIL_SESSION` in the environment that runs the
  agent, never by pasting it into a conversation. Sessions expire after
  `--ttl-hours` (default 12).
- `rail <seat> ...` is a lens. The seat's own session may read and write; another
  seat's session gives a read-only view only when the target anchor is inside
  the caller's read reach; a terminal with no session gets a read-only view.
- A tokenless caller with no terminal has read tools and an empty reach. In role
  mode `--actor` is refused.
- Human-only actions (`ops migrate`, `ops upgrade`, `ops pack use`, `ops
  delegate`, a `human: true` grant, the first grant) need a terminal human or the
  UI channel; a session caller is never human. Two exceptions are recorded with
  their signer: `approve` lets a project-anchored lead seat sign when the charter
  allows it, and a charter `delegation` block lets that seat run the named
  operational actions while the approval is current.
- A pack without roles stays in compat mode: name-based `--actor` behaviour is
  unchanged, and `seat` commands plus the lens refuse with "this pack declares
  no roles" and point at `ops pack use`.
- `rail ops pack create --roles` writes a draft with operating version 2 and
  starter roles, without selecting it; it refuses a version-1 source and an
  extending pack without its own `operating` block.
- In a role-mode store, run `ops pack use` yourself in a terminal (or through
  the UI); `--actor` is refused and a session is never human. A project-anchored
  lead seat may sign `approve` with its own session; other seats cannot.
- MCP in role mode lists read tools plus the write tools the seat's role holds;
  a legacy store and the human still see the whole table.

## Checks, reports and the loop

```text
rail check declare <slug> --spec-file FILE --expect ETAG|absent [--target ITEM]
                    [--confirm "APPROVE <slug>"]
rail check run <target> [--worktree PATH] [--verdict pass|fail]
                 [--evidence RECORD] [--confirm PHRASE]
rail loop brief <item> [--role implementer|reviewer] [--commit SHA] [--max-chars N]
rail loop result <item> --body-file FILE [--commit SHA] [--outcome completed|partial|blocked|failed]
                 [--set KEY=VALUE]... [--model TEXT] [--harness TEXT] [--key KEY]
rail loop candidate <item> --commit SHA [--base SHA] [--profile NAME] [--model TEXT]
                    [--harness TEXT] [--usage NAME=NUMBER|unknown]... [--duration SECONDS]
                    [--key KEY]
rail loop review <item> [--commit SHA] [--result RECORD_ID] --verdict approve|reject
                 [--notes-file FILE] [--set KEY=VALUE]... [--model TEXT] [--harness TEXT]
                 [--key KEY]
rail loop report [item] [--candidate SHA] --report-file FILE
rail loop triage [--limit N] [--report ID] [--acknowledge]
rail loop execution <target> --action NAME --state running|succeeded|failed|cancelled
                    [--output FILE]... [--error WHY]
rail gate [--warnings-as-errors]
```

`rail check declare` accepts JSON specs with `mode`: `cmd`, `exists`,
`environment`, `evidence`, `review`, `human`, `result`, or `store`. An
`environment` check runs a command probe but its verdict is informational and
never a gate proof. A `result` check verifies
the current completed producer Markdown result without running git. A `store`
check proves a committed item's category in another store (a submodule or
sibling repo):

```json
{"schema": 1, "mode": "store", "acceptance": false, "verifies": ["<slice>"],
 "store": "<rel-path>", "item": "<child-slug>", "category": "done"}
```

`loop result` writes the workflow-shaped Markdown record; its body may open
with a leading `---` effort block (`duration_s`,
`usage: {tokens: ..., requests: ...}`, `model`, `harness`). `unknown` is
allowed for an unreported counter, money is not a counter, and
`--model`/`--harness` may not name a fact the block already names.

## Gates and approvals

```text
rail approve <target> --confirm "APPROVE <target>" --confirmer NAME [--expect DIGEST]
rail consent <item> --on-behalf-of NAME --text "WORDS" --expect ETAG
```

`target` is `charter`, a protected slug, or `pack:<name>`. A named lead signs by
default; add `--actor human` for a human signature. The record carries `signer`,
`signer_kind`, `channel` and `on_behalf_of`, and `--confirmer` names the human
the approval is for. A charter with `approval.agents: false` accepts only a
human signature.

`consent` records a human's explicit go-ahead for one item version, in their
own words, bound to the etag they saw. A named lead may record it and the
record names both the agent (`signer`) and the human (`on_behalf_of`); an edit
to the item makes it stale. A pack action may declare `requires: {consent:
true}`, and `approval.agents: false` keeps consent human-only too.

`rail ops pack show` reports `operating` (or `"neutral"`), `semantic_digest`,
`presentation_digest`, `full_digest` and the approval state. Approval binds to
the semantic digest; `surface.yaml` changes only the presentation digest.

## Operations

```text
rail ops doctor [--repair]
rail ops reindex [--full]
rail ops codegraph setup|refresh|status
rail --actor human ops codegraph adopt [--dry-run]
rail ops migrate --source LEGACY --dir DIR [--root DIR] [--pack NAME] [--dry-run]
rail ops rollback --target-class skill|profile --target PATH --version N --expect ETAG
                  [--confirm "APPROVE <slug>"]
rail ops measure versions --target-class skill|profile --target NAME
                          --before N --after N [--since ISO] [--until ISO]
rail ops measure fix --item SLUG [--until ISO]
rail ops measure usage --group-by item|model|harness|role|profile|skill
                       [--item SLUG] [--since ISO] [--until ISO] [--limit N]
rail ops sync [--remote NAME] [--branch NAME]
rail ops sync install
rail ops adapters render [--harness agents|claude|cursor|gemini]... [--dry-run]
                         [--force --confirm "FORCE ADAPTERS adapters"]
rail ops pack list
rail ops pack show --name NAME
rail ops pack validate --name NAME
rail ops pack create --name NAME [--from SOURCE] [--roles]
rail --actor human ops pack use --name NAME --expect RAIL_YAML_ETAG
                    [--map OLD=NEW]... [--dry-run] --confirm "USE PACK <name>"
```

## Transports

```text
rail serve mcp
rail serve ui [--port N]
```

## Compatibility

Every former flat command name is still accepted: `status`, `digest`,
`cto brief`, `find`, `show`, `ledger`, `add`, `update`, `move`, `link`,
`rename`, `relocate`, `check`, `verify`, `brief`, `result`, `candidate`, `review`,
`report`, `triage`, `ci`, `doctor`, `reindex`, `migrate`, `upgrade`,
`rollback`, `pack`, `measure`, `sync`, `adapters sync`, `mcp serve` and
`ui serve`. The grouped names above are the documented surface. The `seat`
commands and the `rail <seat>` lens exist only in a store whose pack declares
roles; a legacy store keeps the name-based behaviour and refuses them with the
`pack use` hint.

## Error codes seen most

| Code | Meaning |
|---|---|
| `NOT_FOUND` | No store, unknown slug, unknown pack |
| `VALIDATION_FAILED` | Bad flags, illegal transition, unapproved confirmation phrase |
| `CONFLICT` | Etag changed since read; re-read and retry |
| `INVALID_STATE` | A candidate or brief for a non-leaf item, immutable record, unapproved charter |
| `BUSINESS_RULE_VIOLATED` | A charter gate is not satisfied |
| `PERMISSION_DENIED` | The role, reach or identity does not allow the call |
| `SESSION_REQUIRED` | A tokenless role-mode caller tried to write |
| `NO_FOCUS` | The pack needs a parent container and none matches; pass `--into` or `--rel part_of=...` |
| `AMBIGUOUS_FOCUS` | Several containers match; `error.details.candidates` names them, pass `--into <id>` |
| `INVALID_FOCUS` | The `--into` slug is not a declared hierarchy container |

A refusal always names the reason and the fix in `error.details`.
