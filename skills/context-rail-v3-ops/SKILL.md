---
name: context-rail-v3-ops
description: 'Operate a Context Rail v3 store: where state lives, doctor/reindex/ci/sync/migrate, recovery order after a refusal, speed budgets and the test lanes. Load when something looks wrong or before shipping.'
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
tools (`approve`, `migrate`, `pack use`) require an explicitly named human:
`--actor human` or `RAIL_ACTOR=human`. Anyone with shell access as your OS user
can act as you; use OS-level isolation if you need a real boundary.

## Maintenance

```powershell
rail doctor                 # integrity issues
rail doctor --repair        # finish or roll back an interrupted write first
rail reindex                # sync the index with the files
rail reindex --full         # rebuild from scratch
rail ci                     # doctor + approval and done gates (never runs checks)
rail ci --warnings-as-errors
```

## Recovery order

1. Read the code and `error.details` in the envelope — refusals name the fix.
2. `rail doctor` for integrity.
3. `rail reindex` when reads disagree with files.
4. `rail doctor --repair` for an interrupted multi-file write.
5. Only then edit files by hand; the next command re-syncs the index.

Never hand-edit the SQLite index and never delete the store to fix a view.

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `NOT_FOUND: no store found` | Not inside a project, or no `rail.yaml` | `rail init`, or `--store` before the command |
| `CONFLICT` on a write | Etag changed since you read it | Re-read (`show`/`status`), then retry with the new etag |
| A check reports `stale` | Declared inputs changed since the verdict | `rail verify` again |
| `doctor` flags approval drift | A protected file changed after approval | Re-approve the target |
| The index is corrupt or missing | Cache only | `rail reindex --full` |
| Two CTOS at once | Single-instance lock | Let the running tick finish |

## Surfaces and interop

```powershell
rail mcp serve              # the same tool table over MCP stdio
rail ui serve               # loopback web UI
rail sync --install         # git merge drivers for store-only clones
rail sync                   # integrate and publish clone changes
rail --actor human migrate ...  # import a v1/v2 legacy store
```

## Speed budgets (fixed, tested in CI)

| Operation | 1,000 docs | 10,000 docs |
|---|---|---|
| CLI startup | < 150 ms | < 150 ms |
| Warm read (`cto brief`, `status`) | < 300 ms | < 1 s |
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

- `rail doctor` clean (or only informational findings).
- `rail ci` passes; approval hashes match.
- The default branch is the one the charter names.
- The store is committed; the index and `.rail-local/` are not.
