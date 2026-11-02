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
- Stage 3 plan (complete): `docs/superpowers/plans/2026-10-28-v3-stage-3.md`
- Stage 3 architecture: `library/arch-stage-3.md`
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
