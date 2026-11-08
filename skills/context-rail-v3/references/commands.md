# Command reference

Global options, before the command: `--store PATH`, `--actor NAME`.
Human-only tools (`approve`, `ops migrate`, `ops pack use`) need an explicitly
named human: `--actor human` or `RAIL_ACTOR=human`.
Every command accepts `--key` for idempotency where it writes.
Exit codes: `0` success, `1` refusal, `2` infrastructure failure.

## Daily

```text
rail init [--dir DIR] [--pack NAME] [--root DIR] [--scaffold] [--dry-run] [--code-graph PROVIDER]
          [--no-approve] [--no-git-drivers] [--harness ID]...
          [--interview | --answers-file FILE | --draft-file FILE]
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
rail change add <preset> "<title>" [--rel REL=SLUG]... [--tag TAG]...
                 [--set KEY=VALUE]... [--body-file FILE] [--confirm PHRASE]
rail change update <slug> --expect ETAG [--set KEY=VALUE]... [--tag TAG]...
                    [--body-file FILE] [--confirm PHRASE]
rail change move <slug> <status> --expect ETAG [--resolution TEXT]
                 [--confirm PHRASE]
rail change link <src> <rel> <dst> --expect ETAG [--remove] [--reason TEXT]
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

## Checks, reports and the loop

```text
rail check declare <slug> --spec-file FILE --expect ETAG|absent [--target ITEM]
                    [--confirm "APPROVE <slug>"]
rail check run <target> [--worktree PATH] [--verdict pass|fail]
                 [--evidence RECORD] [--confirm PHRASE]
rail loop report [item] --run RUN_ID --report-file FILE
rail loop triage [--limit N]
rail loop execution <target> --action NAME --state running|succeeded|failed|cancelled
                    [--output FILE]... [--error WHY]
rail loop dispatch <item> --expect ETAG --key KEY [--profile NAME]
rail loop review   <item> --run RUN_ID --key KEY [--profile NAME]
rail loop merge    <item> --run RUN_ID --expect ETAG --key KEY [--push]
rail loop tick --key KEY [--timeout SECONDS]
rail gate [--warnings-as-errors]
```

## Gates and approvals

```text
rail --actor human approve <target> --confirm "APPROVE <target>" --confirmer NAME [--expect DIGEST]
```

`target` is `charter`, a protected slug, or `pack:<name>`.

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
rail ops sync [--remote NAME] [--branch NAME]
rail ops sync install
rail ops adapters render [--harness agents|claude|cursor|gemini]... [--dry-run]
                         [--force --confirm "FORCE ADAPTERS adapters"]
rail ops pack list
rail ops pack show --name NAME
rail ops pack validate --name NAME
rail ops pack create --name NAME [--from SOURCE]
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
`rename`, `relocate`, `check`, `verify`, `dispatch`, `review`, `merge`,
`report`, `triage`, `cto tick`, `ci`, `doctor`, `reindex`, `migrate`,
`rollback`, `pack`, `measure`, `sync`, `adapters sync`, `mcp serve` and
`ui serve`. The grouped names above are the documented surface.

## Error codes seen most

| Code | Meaning |
|---|---|
| `NOT_FOUND` | No store, unknown slug, unknown pack |
| `VALIDATION_FAILED` | Bad flags, illegal transition, unapproved confirmation phrase |
| `CONFLICT` | Etag changed since read; re-read and retry |
| `INVALID_STATE` | Dispatch of a non-executable item, immutable record, wrong profile recipe |
| `BUSINESS_RULE_VIOLATED` | A charter gate is not satisfied |
| `NO_FOCUS` | The pack needs a parent container and none matches; pass `--into` or `--rel part_of=...` |
| `AMBIGUOUS_FOCUS` | Several containers match; `error.details.candidates` names them, pass `--into <id>` |
| `INVALID_FOCUS` | The `--into` slug is not a declared hierarchy container |

A refusal always names the reason and the fix in `error.details`.
