# Command reference

Global options, before the command: `--store PATH`, `--actor NAME`.
Human-only tools (`approve`, `migrate`, `pack use`) need an explicitly named
human: `--actor human` or `RAIL_ACTOR=human`.
Every command accepts `--key` for idempotency where it writes.
Exit codes: `0` success, `1` refusal, `2` infrastructure failure.

## Store and documents

```text
rail init [--dir DIR] [--pack NAME] [--root DIR]
          [--interview | --answers-file FILE | --draft-file FILE]
rail add <preset> "<title>" [--rel REL=SLUG]... [--tag TAG]...
         [--set KEY=VALUE]... [--body-file FILE] [--confirm PHRASE]
rail update <slug> --expect ETAG [--set KEY=VALUE]... [--tag TAG]...
            [--body-file FILE] [--confirm PHRASE]
rail move <slug> <status> --expect ETAG [--resolution TEXT] [--confirm PHRASE]
rail link <src> <rel> <dst> --expect ETAG [--remove] [--reason TEXT]
rail rename <old> <new> [--confirm "APPROVE <slug>"]
rail relocate [--dry-run] [--confirm "APPROVE <slug>"]
rail show <slug> [--body-chars N]
rail find [--primitive P] [--preset P] [--tag T] [--status S]
          [--category C] [--text Q] [--rel REL=SLUG] [--limit N]
```

`status` is managed: change it only with `move`. `move` enforces the pack's
transitions, and a move into a `done` category runs the charter's done gate.

## Checks, reports, reviews

```text
rail check <slug> --spec-file FILE --expect ETAG|absent [--target ITEM]
           [--confirm "APPROVE <slug>"]
rail verify <target> [--worktree PATH] [--verdict pass|fail]
            [--evidence RECORD] [--confirm PHRASE]
rail report [item] --run RUN_ID --report-file FILE
rail review <item> --run RUN_ID --key KEY [--profile NAME]
rail dispatch <item> --expect ETAG --key KEY [--profile NAME]
rail merge <item> --run RUN_ID --expect ETAG --key KEY [--push]
rail cto brief
rail cto tick --key KEY [--timeout SECONDS]
rail triage [--limit N]
rail ledger [--item SLUG] [--profile NAME] [--since ISO] [--limit N]
rail digest [--since ISO] [--limit N] [--max-chars N]
rail status
```

## Gates and approvals

```text
rail --actor human approve <target> --confirm "APPROVE <target>" --confirmer NAME [--expect DIGEST]
```

`target` is `charter`, a protected slug, or `pack:<name>`.

## Versions and measurement

```text
rail rollback --target-class skill|profile --target PATH --version N --expect ETAG
              [--confirm "APPROVE <slug>"]
rail measure versions --target-class skill|profile --target NAME
                      --before N --after N [--since ISO] [--until ISO]
rail measure fix --item SLUG [--until ISO]
```

## Packs

```text
rail pack list
rail pack show --name NAME
rail pack validate --name NAME
rail pack create --name NAME [--from SOURCE]
rail --actor human pack use --name NAME --expect RAIL_YAML_ETAG
              [--map OLD=NEW]... [--dry-run] --confirm "USE PACK <name>"
```

## Operations

```text
rail doctor [--repair]
rail reindex [--full]
rail ci [--warnings-as-errors]
rail sync [--install] [--remote NAME] [--branch NAME]
rail sync driver <doc|record|config> <base> <ours> <theirs> <path>
rail adapters sync [--harness agents|claude|cursor|gemini]... [--dry-run]
                   [--force --confirm "FORCE ADAPTERS adapters"]
rail --actor human migrate --source LEGACY --dir DIR [--root DIR] [--pack NAME] [--dry-run]
rail mcp serve
rail ui serve [--port N]
```

## Error codes seen most

| Code | Meaning |
|---|---|
| `NOT_FOUND` | No store, unknown slug, unknown pack |
| `VALIDATION_FAILED` | Bad flags, illegal transition, unapproved confirmation phrase |
| `CONFLICT` | Etag changed since read; re-read and retry |
| `INVALID_STATE` | Dispatch of a non-executable item, immutable record, wrong profile recipe |
| `BUSINESS_RULE_VIOLATED` | A charter gate is not satisfied |

A refusal always names the reason and the fix in `error.details`.
