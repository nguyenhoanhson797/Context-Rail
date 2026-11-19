# Operator skills

Agent skills that teach the rail itself — not the project. They exist so an
agent can answer “what can this do?” and “how do I customize it?” without
reading the engine source.

| Skill | Use it when |
|---|---|
| `context-rail-v3` | First contact with a `context-rail/` store; quick start and command map |
| `context-rail-v3-onboarding` | Adopting the rail in a new or existing project, with any harness |
| `context-rail-v3-workflows` | Custom document types, statuses, transitions, BA/PO steps, pack switching |
| `context-rail-v3-pack-authoring` | Creating or adjusting a pack: presets, required metadata, defaults, declarative placement |
| `context-rail-v3-surface` | Working from `model`, `types`, `next`, `show`, `explain` and `why` without reading the pack |
| `context-rail-v3-loop` | Handing out and recording agent work: brief → candidate → verify → review → report |
| `context-rail-v3-ops` | Doctor, reindex, recovery, git sync, budgets, cost, steering, test lanes |

Each `SKILL.md` is short and routes to `references/` only when needed:
`context-rail-v3/references/commands.md` for exact flags,
`context-rail-v3-workflows/references/ba-po-pack.yaml` for a complete tested v4
pack (three workflows, per-workflow quality, verbs).

Do not confuse these with the skills a store carries. `rail init` seeds the
engine skills and the pack skills into the store's workspace, and
`rail ops upgrade` refreshes them, so an agent reads them from the store with
its live brief. These public skills are the optional setup reference for a human
or an operator agent who sets up a project, and they are how such an agent
drives the product.

## Install

The public skills are optional: a store does not need them, because its own
skills come with the store. Install them only when an agent should set up
projects from this checkout. Skills are discovered from the agent's skill
directory. For this environment:

```powershell
Copy-Item -Recurse skills\context-rail-v3* "$HOME\.agents\skills\"
```

Restart the session (or reload the skills directory) so they are discovered.
