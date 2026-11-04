# Context Rail v3

Context Rail v3 is an agentic project workspace. A CTO agent runs a project
through the rail, sub-agents get narrow role-specific context, and a human
approves the boundary, the quality bar and irreversible actions. This
repository is a clean rebuild. The legacy v1/v2 code lives in the private
sibling `context-rail-link` repository and is frozen; nothing here needs it.

## Source of truth

These records are working material: they stay on disk, untracked, and are never
published.

- Design: `docs/superpowers/specs/2026-10-28-context-rail-v3-design.md`
- Operating-model design (v5): `docs/superpowers/specs/2026-11-04-v5-operating-model.md`
- Operating-model plan (v5): `docs/superpowers/plans/2026-11-04-v5-operating-model.md`
- Operating-model architecture: `library/arch-operating-model.md`
- Stage 3 plan (complete): `docs/superpowers/plans/2026-10-28-v3-stage-3.md`
- Stage 3 architecture: `library/arch-stage-3.md`
- Command surface (implemented): `docs/superpowers/specs/2026-11-02-command-surface-consolidation.md`
- Stage 2 plan (complete): `docs/superpowers/plans/2026-10-28-v3-stage-2.md`
- Stage 2 architecture: `library/arch-stage-2.md`
- Stage 1b plan (complete): `docs/superpowers/plans/2026-10-28-v3-stage-1b-cto-slice.md`
- Stage 1a plan (complete): `docs/superpowers/plans/2026-10-28-v3-stage-1a-engine-core.md`
- Stage 1a amendments: `docs/superpowers/plans/2026-10-28-v3-stage-1a-amendments.md`, which
  win over the Stage 1a plan where they differ.
- Stage 1b amendments: `docs/superpowers/plans/2026-10-29-v3-stage-1b-amendments.md`, which
  win over the Stage 1b plan where they differ.

Work the plan task by task, in order. If the plan and the code disagree, or a
step cannot be done as written, stop and report it rather than improvising a
different design. Stage 3 completes the planned product; a Stage 4 does not
exist, so new work starts from a new design rather than a new stage plan.

## Environment (Windows, PowerShell)

- Use `py`, not `python`.
- Install: `py -m pip install -e .[test]`
- Default test lane: `py -m pytest -q` (excludes `slow` only, so it also collects
  `perf`; the milestone gate is `.github/workflows/ci.yml`, functional lane then
  serial perf lane)
- Speed budgets: `py -m pytest -q -m perf`; 10k-doc and other slow tests: `py -m pytest -q -m slow`
- Profile a slow command: `py -m cProfile -s cumtime -m context_rail <command>`

## Legacy reference code

`docs/reference/legacy/` (local only, never published) holds read-only copies of
the v2 modules the plan reuses (see its README for which task uses what). Copy
what a task names into `src/context_rail/` and adapt it there. Never import from
`docs/`, never edit the copies, and never modify the `context-rail-link`
repository.

## Engine rules (from the design; do not break them)

- Files are the truth. The SQLite index is a rebuildable cache under the
  worktree's git dir (or `<store>/.rail-local/` outside git), never in the repo
  tree.
- Every store change goes through `context_rail.store.write.commit`. Nothing
  else writes store files.
- Reads never execute commands and never hash source trees.
- `context_rail.kernel` is pure: no file, git, process or index access, and no
  imports from `store/`, `runner/` or `gateway/`.
- Fail closed: an unknown manifest schema, pack version, key or policy field is
  refused, never guessed.
- Primitive, relation and category names are fixed (see the plan's Global
  Constraints). Engine logic never branches on tag names.
- Paths are derived: `rail.yaml` plus the work graph decides where a document or
  record lives. No caller supplies a path, a parent folder or a final title, and
  nothing but the resolver renders one.
- A pack is one project type with many named workflows; `statuses` is pack-wide
  and a preset selects exactly one workflow. Transitions and execution flags are
  read through the workflow, never the pack.
- Pack version 2 may declare `operating`: unit, hierarchy, focus, queue, progress
  and execution relevance. It is semantic and approved; version 1 packs load
  with the neutral model and may not declare it.
- `surface.yaml` is presentation only and is not part of the approval. It cannot
  introduce a preset, workflow, status or action. A label or card change changes
  the presentation digest; an operating, transition, preset or placement change
  changes the semantic digest and needs reapproval.
- The compiler is the only interpreter. Commands, agents and the UI read the
  compiled model (`model`, `types`, `next`, `show`, `explain`, `why`) and never
  parse `operating` or `surface.yaml` themselves.
- Focus lives in the container's own status. Ambiguity is refused with its
  candidates; `--into` and `--all` are the explicit escapes, and neither
  bypasses a gate, a claim or actor permissions.
- Queue eligibility is workflow transitions (default) or declared actor actions
  (`require: action`). Completion counts declared `done`-category states;
  execution progress comes from execution records only and is never merged.
- No engine logic branches on a pack name, a preset name or a tag name, and no
  React module may branch on a pack name.
- Quality belongs to the workflow: a status may name a charter gate and the
  checks it selects. The engine never branches on a gate name.
- Agents read the compiled surface (`types`, `next`, `show`, `explain`, `why`),
  never `pack.yaml`. Pack actions are declarative and run as the caller through
  the gateway; no agent-defined executable tools.
- `cli.py` imports only stdlib modules and `gateway.tools` at import time; every
  heavier import is lazy.
- The speed budgets and safety assertions in the plan are fixed. Fix the code,
  never the budget or the assertion.

## Working rules

- Test first: write the failing test, see it fail, implement, see it pass.
- One commit per plan task, Conventional Commits style with the area as scope,
  e.g. `feat(store): ...`, `test: ...`, `docs: ...`.
- Type hints on public functions; no bare `except`.
- Comments only for a reason the code cannot show (a hidden constraint, a
  workaround, a surprising invariant). Do not narrate the code or mention the
  task or plan in comments.
- Shared test fixtures and helpers are defined once, as listed in plan Task 1;
  reuse them instead of writing local variants.
- A new operating or presentation refusal gets a negative test: unknown key,
  preset, workflow, state, category, field, screen kind or action, an ambiguous
  or missing focus, an invalid completion rule, and an invalid surface.
- Every doctor code is in `SEVERITIES`; an invalid pack is a `pack validate`
  error, never a doctor cascade.
- Docs and skills tests cover the public files only; `docs/superpowers/` stays
  local working material.
