---
name: context-rail-v3-ops
description: 'Operate a Context Rail v3 store: where state lives, ops doctor/reindex/gate/sync/migrate, recovery order after a refusal, speed budgets and the test lanes. Load when something looks wrong or before shipping.'
---

# Context Rail v3 — operations

## Where state lives

```text
my-project/
├── context-rail/            the store: TRUTH, commit it
│   ├── rail.yaml            placement, pack, schema
│   ├── charter.md           gates, rights, budgets
│   ├── project/ items/ notes/ checks/
│   ├── records/YYYY-MM/     runs, reports, verdicts, approvals
│   └── workspace/           cto + profile prompts, skills, memory
└── .git/
    └── context-rail/        index cache: DISPOSABLE, never commit

outside git:
context-rail/.rail-local/    index, locks, dispatch/tick/merge progress
```

- The store is portable Markdown + YAML; back it up with git.
- The index is rebuilt from files; deleting it costs one rebuild, never data.
- Control files prove a dispatch, tick or merge happened; they are written
  atomically and are machine-local.

## Actor identity

`--actor` and `RAIL_ACTOR` are workflow claims, not authentication. Human-only
tools (`approve`, `ops migrate`, `ops pack use`) require an explicitly named
human: `--actor human` or `RAIL_ACTOR=human`. Anyone with shell access as your
OS user can act as you; use OS-level isolation if you need a real boundary.

## Maintenance

```powershell
rail ops doctor             # integrity issues
rail ops doctor --repair    # finish or roll back an interrupted write first
rail ops reindex            # sync the index with the files
rail ops reindex --full     # rebuild from scratch
rail gate                   # doctor + approval and done gates (never runs checks)
rail gate --warnings-as-errors
```

## Packs, digests and reapproval

```powershell
rail ops pack validate --name scrum    # compile the operating model + surface, never refuses
rail ops pack show --name scrum        # operating block or "neutral", three digests, approval
rail ops pack create --name team --from scrum
```

- `semantic_digest` covers statuses, workflows, quality, presets, fields,
  actions, `operating`, placement and policy. It is the approval and
  compatibility target.
- `presentation_digest` covers `surface.yaml` labels, screens and fields; it is
  only the UI/API cache key.
- `full_digest` is both, for diagnostics.
- The shipped packs are v2, so their semantic digest changed. A store on a
  package built-in reports `approval_mismatch`; inspect and reapprove:

```powershell
rail ops pack show --name scrum
rail --actor human approve pack:scrum --confirm "APPROVE pack:scrum" --confirmer <you>
rail --actor human approve charter --confirm "APPROVE charter" --confirmer <you>
```

A v1 local pack is unaffected: it loads with the neutral model and keeps its
approval. A label-only change never needs reapproval.

## Operating doctor codes

| Code | Severity | Fix |
|---|---|---|
| `focus_missing` | warning | `rail next --all`, or start the focus container |
| `focus_ambiguous` | warning | `rail next --into <id>`, or finish one container |
| `orphan_unit` | warning | `rail change link <unit> part_of <container>` |
| `empty_container` | warning | add a unit, or drop the empty container |
| `hierarchy_placement` | error | `rail change repath`, or fix `rail.yaml` |
| `surface_invalid` | warning | fix `surface.yaml` |
| `progress_invalid` | error | fix the operating progress rule |
| `execution_failed` | warning | inspect the run, or re-run it |
| `execution_missing` | warning | run the action the operating model expects |

An invalid pack is a `pack validate` error, not a doctor cascade: the compiler
refuses it before any check runs.

## Recovery order

1. Read the code and `error.details` in the envelope — refusals name the fix.
2. `rail ops doctor` for integrity.
3. `rail ops reindex` when reads disagree with files.
4. `rail ops doctor --repair` for an interrupted multi-file write.
5. Only then edit files by hand; the next command re-syncs the index.

Never hand-edit the SQLite index and never delete the store to fix a view.

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `NOT_FOUND: no store found` | Not inside a project, or no `rail.yaml` | `rail init`, or `--store` before the command |
| `CONFLICT` on a write | Etag changed since you read it | Re-read (`read show`/`read status`), then retry with the new etag |
| A check reports `stale` | Declared inputs changed since the verdict | `rail check run` again |
| `doctor` flags approval drift | A protected file changed after approval | Re-approve the target |
| The index is corrupt or missing | Cache only | `rail ops reindex --full` |
| Two CTOs at once | Single-instance lock | Let the running tick finish |
| A command seems to act on old data | None expected: every daemon call reads the files afresh | Run it as `rail --no-daemon <command>`; if the answer differs, report it |
| A write says `outcome unknown` | The daemon did not answer after the request may have reached it | Re-read the item to see whether it applied; repeat only a command that carries a `--key` |
| Every command waits about 2 s | A warm daemon is stuck inside a call and `serve daemon --stop` gets no answer | End that `serve daemon` process by hand; commands run direct meanwhile and the next one starts a fresh daemon |

## Surfaces and interop

```powershell
rail serve mcp                  # the same tool table over MCP stdio
rail serve ui                   # loopback web UI
rail serve daemon --stop        # end this store's warm daemon (the CLI restarts it on demand)
rail ops sync install           # git merge drivers for store-only clones
rail ops sync                   # integrate and publish clone changes
rail --actor human ops migrate ...  # import a v1/v2 legacy store
```

## Steering without widening

Profiles and skills are store documents; the CTO may narrow them daily.
Every save goes through the gateway with the current etag — the same call
the UI Settings **Configuration** tab makes:

```powershell
rail change update implementer --set model=<model> --expect <etag> --key <k>
rail change update test-first --set description=<text> --expect <etag> --key <k>
rail ops measure versions --target-class profile --target implementer --before 1 --after 2
rail ops rollback --target-class profile --target implementer --version 1
```

- `model` / `effort` / `auto` / `runner` / `tools` / `skills` / narrowed
  `allowed_methods` are `cto-alone`; `allowed_methods` may only shrink.
- Any budget increase is `human-only` (typed phrase) — the gateway refuses
  a CTO `update` that raises `budgets.attempts/seconds/tokens/credits`.
- Skills: `name == file stem`, `description` non-empty, `allowed_tools`
  must be known tool ids; version auto-increments.
- `measure versions` is observational only, never an auto-rollout; if the
  comparison regresses, `rollback` restores the earlier version as a new one.

## Speed budgets (fixed, tested in CI)

| Operation | 1,000 docs | 10,000 docs |
|---|---|---|
| CLI startup | < 150 ms | < 150 ms |
| Warm read (`overview`, `read status`) | < 300 ms | < 1 s |
| Cold rebuild | < 10 s | < 60 s |
| Single write | < 200 ms | < 200 ms |

If a change needs more time, the change is the bug. Never raise the budget.

## Test lanes

```powershell
py -m pytest -q -m "not slow and not perf"          # functional
py -m pytest -q -m "perf and not slow" -p no:xdist  # budgets, one process
py -m pytest -q -m slow -p no:xdist                 # 10k-document stores
```

On Windows use `py`, not `python`. The bare `py -m pytest -q` also collects
perf tests, so use the lanes above when the machine is busy.

## Before you ship

- `rail ops doctor` clean (or only informational findings).
- `rail gate` passes; approval hashes match.
- The default branch is the one the charter names.
- The store is committed; the index and `.rail-local/` are not.
