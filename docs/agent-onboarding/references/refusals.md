# Refusals and recovery

A refusal is a typed answer, not a traceback. Read `error.code` and
`error.details` first: the details name the missing condition and usually the
command that produces it.

```json
{
  "ok": false,
  "error": {
    "code": "BUSINESS_RULE_VIOLATED",
    "message": "the item_done gate is not satisfied",
    "details": {"gate": "item_done", "missing": ["repro-check"], "command": "rail check run ..."}
  }
}
```

## Recovery order

1. Read the code and details.
2. `rail ops doctor` for integrity.
3. `rail ops reindex` when reads disagree with files.
4. `rail ops doctor --repair` for an interrupted multi-file write.
5. Only then edit files by hand; the next command re-syncs the index.

Never hand-edit the SQLite index and never delete the store to fix a view.

## By code

| Code | Typical cause | Fix |
|---|---|---|
| `NOT_FOUND` | no store, unknown slug, unknown pack | `rail init`, fix `--store` placement, or `rail read find` for the slug |
| `VALIDATION_FAILED` | bad flags, illegal transition, wrong phrase, bad pack field | Read the message; `--help` for flags; `rail explain <workflow>` for legal moves |
| `CONFLICT` | etag changed, adapter block edited, duplicate identity | Re-read and retry with the new etag; repair an adapter by hand or force as a human |
| `INVALID_STATE` | non-executable item, immutable record, wrong profile recipe | `rail show <slug>`; finish the dependency; use an implementer for dispatch, reviewer for review |
| `BUSINESS_RULE_VIOLATED` | a charter gate is unsatisfied | `rail why <slug>`; run the named check or obtain the named review/acceptance |
| `NO_FOCUS` | the pack needs a parent container and none matches | Start the container, or pass `--rel part_of=<container>` |
| `AMBIGUOUS_FOCUS` | several containers match the focus | Pass `--into <id>` from `details.candidates`; never guess |
| `INVALID_FOCUS` | `--into` is not a declared hierarchy container | `rail model` lists the hierarchy |
| `INFRA_FAILURE` | process, git or filesystem failure | Fix the environment; the write path is atomic, so no partial document |

## By symptom

| Symptom | Cause | Fix |
|---|---|---|
| `no store found` | not inside a project, or no `rail.yaml` | `rail init`, or put `--store` before the command |
| `unrecognized arguments` | global option after the command, or `add` used with flags instead of positionals | `rail --store ... change add <preset> "<title>"` |
| `CONFLICT` on every write | you are reusing an old etag | `rail read show <slug>`, then retry with the new etag |
| A check reports `stale` | declared inputs changed since the verdict | `rail check run <target>` again; a stale pass is not a pass |
| `the charter is not approved` | approval missing or drifted | `rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Name"` |
| `approval_mismatch` | a semantic change moved the digest | Inspect `rail ops pack show --name <pack>`, then reapprove `pack:<pack>` and the charter |
| `doctor` reports `placement_mismatch` | a path is not what the placement renders | `rail change repath --dry-run`, then `rail change repath` |
| `placement_unresolved` / `placement_ambiguous` | the relation the placement walks has no single target | Fix the relation, or choose a different parent |
| `duplicate_slug` | two documents share identity | `rail change rename` the newer one |
| `required_field_missing` | a preset requires metadata | `rail change update <slug> --set field=value --expect <etag>` |
| `adapter_modified` | a managed block was edited outside rail | Repair by hand, or a human runs `--force --confirm "FORCE ADAPTERS adapters"` |
| `doctor` reports an interrupted write | a multi-file commit stopped mid-way | `rail ops doctor --repair`, then `rail ops reindex` |
| The index is corrupt or missing | cache only | `rail ops reindex --full` |
| `'cto tick' cannot be nested` | a child session already set `RAIL_ACTOR` | Run `rail loop tick` from the outer session with `RAIL_ACTOR` unset |
| Two CTOs at once | single-instance lock | Let the running tick finish |
| A dispatch refuses | dependency, gate, claim or profile recipe | `rail why <item>` and `rail show <item>`; finish the dependency or use the right profile |
| A review refuses | the reviewer is the implementer, or the run is not verified | Use a distinct reviewer actor; review after verification |
| A merge refuses | the candidate is not approved or conflicts | Review first; one rebase retry, then escalate |
| `ops pack validate` fails | missing preset, template, field or operating reference | Fix the named key; `pack validate` is read-only |
| `pack use` refuses | unmapped status, missing preset or an unsatisfiable required field | Add `--map OLD=NEW` or fix the target pack; never force |
| `migrate` refuses a structural target | a legacy store has no work graph | Migrate into a flat layout first, then add structure |
| `ops pack use` needs a human | actor is not explicitly human | `rail --actor human ops pack use ... --confirm "USE PACK <name>"` |

## Gate and proof refusals

A gate refusal names the gate and what is missing:

| Missing | Produce it with |
|---|---|
| `checks` | `rail check declare` then `rail check run` |
| `acceptance` | a fresh human acceptance check |
| `review` | a separated `rail loop review` |
| `human` | an explicitly named human verdict |
| `children` | finish or drop every child item |

Do not lower the gate. Propose the exception and let the human decide.

## Focus refusals

Focus is the container's own status, never an inference from its children.

- `NO_FOCUS` — the pack needs a parent and none matches. Pass
  `--rel part_of=<container>` or start the container with the pack's start
  action.
- `AMBIGUOUS_FOCUS` — more than one container matches. `details.candidates`
  names them; pass `--into <id>` or move one out of the active category.
- `INVALID_FOCUS` — `--into` is not a declared hierarchy container. `rail model`
  lists the valid ones.
- `--all` widens the queue scope; it never bypasses a gate, a claim or actor
  permissions.
- `--focus-only` narrows to the focus; it is the safe default for a worker.

## Approval refusals

Approval is a human-only decision with a typed phrase and a protected-file
snapshot digest.

```bash
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Ada"
rail --actor human approve pack:scrum --confirm "APPROVE pack:scrum" --confirmer "Ada"
rail --actor human approve <protected-slug> --confirm "APPROVE <slug>" --confirmer "Ada"
```

If the semantic digest moved, reapprove the pack first, then the charter. A
label, screen or card-field change touches only the presentation digest and does
not need reapproval.

## Doctor codes

| Code | Severity | Fix |
|---|---|---|
| `placement_mismatch` | warning | `rail change repath` |
| `placement_unresolved` | error | fix the relation the placement walks |
| `placement_ambiguous` | error | choose one parent |
| `placement_cycle` | error | break the `part_of`/`about`/`verifies` loop |
| `placement_invalid` | error | fix the rendered path or `rail.yaml` |
| `required_field_missing` / `required_field_invalid` | error | update the document's metadata |
| `placement_historical` | warning | a record lost its anchor; it is never rewritten |
| `record_metadata_missing` / `record_metadata_invalid` | warning | stale record metadata |
| `focus_missing` | warning | `rail next --all`, or start the focus container |
| `focus_ambiguous` | warning | `rail next --into <id>`, or finish one |
| `orphan_unit` | warning | `rail change link <unit> part_of <container>` |
| `empty_container` | warning | add a unit, or drop the empty container |
| `hierarchy_placement` | error | `rail change repath`, or fix `rail.yaml` |
| `surface_invalid` | warning | fix `surface.yaml` |
| `progress_invalid` | error | fix the operating progress rule |
| `execution_failed` | warning | inspect or re-run the run |
| `execution_missing` | warning | run the action the operating model expects |
| `duplicate_slug` | error | rename one document |
| `adapter_modified` | warning | repair or force the managed block |

An invalid pack is a `pack validate` error, not a doctor cascade: the compiler
refuses it before any check runs.

## What never to do

- Never edit the SQLite index.
- Never delete the store to fix a view.
- Never hand-edit a Record.
- Never lower a gate or approve as an agent.
- Never guess an etag, a focus or a workflow.
- Never pass a path to `change add`.
- Never trust a stale check verdict.
