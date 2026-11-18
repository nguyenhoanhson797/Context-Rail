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
context-rail/.rail-local/    index, locks, sync progress
```

- The store is portable Markdown + YAML; back it up with git.
- The index is rebuilt from files; deleting it costs one rebuild, never data.
- Control files record local sync progress; they are written atomically and are
  machine-local.

## Actor identity

`--actor` and `RAIL_ACTOR` are workflow claims, not authentication. Human-only
tools (`ops migrate`, `ops upgrade`, `ops pack use`, `ops delegate`) require an
explicitly named human: `--actor human` or `RAIL_ACTOR=human`. `approve` records its signer: a
named lead may sign when the charter allows it (`approval.agents: true`, the
default), naming the human in `--confirmer`. A charter may also delegate named
operational actions to one lead:

```yaml
delegation:
  to: portfolio-lead
  actions: [ops upgrade, ops adapters render, ops rollback]
```

The block is part of the approved picture; the lead may run only those actions,
only while the approval is current, and each use writes a `delegation.used`
record. Set it as the human with `rail ops delegate <seat> --action <id>
--expect <charter-etag> --confirm "DELEGATE <seat>"` (name mode: add `--actor
human`; role mode: run on a terminal with no session), then approve the charter;
`--remove` clears it.
`ops pack use`, `ops migrate`, `init`, `pack create` and a `--force` render stay
human: they replace or create the boundary that authorizes the delegate. Anyone with shell access as your OS user can act as you; use OS-level
isolation if you need a real boundary.

## Maintenance

```powershell
rail ops doctor             # integrity issues
rail ops repair             # finish or roll back an interrupted write first
rail ops reindex            # sync the index with the files
rail ops reindex --full     # rebuild from scratch
rail gate                   # doctor + approval and done gates (never runs checks)
rail gate --warnings-as-errors
```

## Code graph

```powershell
rail ops codegraph setup      # probe the provider, cache readiness, build HEAD
rail ops codegraph status     # provider, coverage, freshness; never probes
rail ops codegraph refresh    # rebuild HEAD with the configured provider
rail --actor human ops codegraph adopt   # move an old store off the code map
```

`rail.yaml` `code_graph` is `builtin` (default), `graphify`, `gitnexus` or
`none`; the pack policy can only say `builtin` or `none`. Pass
`--code-graph <choice>` to `rail init` to write it at onboarding; an external
choice makes init probe the tool once and report the readiness line. The rail
never installs a tool. Graphify is `uv tool install "graphifyy>=0.9.100,<0.10"`;
GitNexus is `npm install -g gitnexus` (Node 22.18+ or 24.11+, PolyForm
Noncommercial). Override either binary with `RAIL_GRAPHIFY_BIN` or
`RAIL_GITNEXUS_BIN`.

Readiness is cached under the local directory. `status`, the overview, an
approval summary and doctor read the cache and never start a process; only
`setup` probes. GitNexus indexes an isolated mirror under the cache with
`GITNEXUS_HOME` set there, so the real checkout and `~/.gitnexus` stay clean.
A missing or broken provider falls back to `builtin`; the brief never blocks on
it.

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

A named lead may sign both instead when the charter allows it
(`approval.agents: true`, the default); drop `--actor human` and name the human
in `--confirmer`. A charter with `approval.agents: false` accepts only the human
signature shown above.

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
| `seat_role_unknown` | error | restore the role in the pack, or `rail seat revoke <seat>` |
| `seat_anchor_missing` | error | restore the anchor document, or `rail seat revoke <seat>` |
| `role_skill_missing` | warning | add the skill note, or remove it from the role |
| `session_stale` | warning | `rail seat close <seat>` |

An invalid pack is a `pack validate` error, not a doctor cascade: the compiler
refuses it before any check runs.

## Recovery order

1. Read the code and `error.details` in the envelope — refusals name the fix.
2. `rail ops doctor` for integrity.
3. `rail ops reindex` when reads disagree with files.
4. `rail ops repair` for an interrupted multi-file write.
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
| An old store refuses a command with `rail ops upgrade` in the message | It predates the runtime-neutral engine or result contracts | A human runs `rail --actor human ops upgrade --dry-run`, then `--confirm "UPGRADE store"`; re-approve if told to (preserving historical done items and custom guidance); `rail ops adapters render` |
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
rail change update implementer --set skills=<list> --expect <etag> --key <k>
rail change update review-checklist --set description=<text> --expect <etag> --key <k>
rail ops measure versions --target-class profile --target implementer --before 1 --after 2
rail ops rollback --target-class profile --target implementer --version 1
```

- `skills` and narrowed `allowed_methods` are `cto-alone`; `allowed_methods` may
  only shrink. A profile names no model, effort, autonomy, runner or tools: the
  tool running the agent owns those, and `rail ops upgrade` strips them from an
  older store.
- Any budget increase is `human-only` (typed phrase) — the gateway refuses
  a CTO `update` that raises `budgets.attempts/seconds/tokens/credits`.
- Skills: `name == file stem`, `description` non-empty, `allowed_tools` is a
  free-form list your tool understands; version auto-increments.
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
