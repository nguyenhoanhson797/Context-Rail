# Context Rail

[![PyPI](https://img.shields.io/pypi/v/context-rail)](https://pypi.org/project/context-rail/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://pypi.org/project/context-rail/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/nguyenhoanhson797/Context-Rail/blob/master/LICENSE)

**Keep your AI agents on the rails.** Context Rail turns your project into a
shared workspace where AI agents do the work and you stay in charge of what
matters.

Building with AI agents is fast, until it isn't. Agents forget what was
decided, lose track of what is done, skip the tests and change things nobody
asked for. The bigger the project, the worse it gets.

Context Rail gives every project one source of truth that lives in your git
repo. A lead agent plans and hands out the work. Each helper agent sees only
what its job needs. Nothing counts as done until it is proven, and the big
calls wait for a human yes.

## What you get

- **Agents that stay focused.** Each agent gets a short brief for its role
  instead of the whole project. Less noise, fewer wrong turns.
- **You approve what matters.** The project's scope, its quality bar and
  anything irreversible wait for a human. Change the rules, and Context Rail
  asks again.
- **"Done" means proven.** Work only reaches done when its checks pass. When
  proof is missing, Context Rail tells you which command produces it.
- **Every agent works in its own sandbox.** Each job runs in a separate git
  worktree and lands only after review, as a clean fast-forward merge.
- **Your project stays yours.** Everything is plain Markdown and JSON in your
  repo. No server, no account, no lock-in.
- **One tool for any kind of project.** Kanban, sprints, phase gates or video
  production: pick a template and the same engine runs it.

## Try it in 5 minutes

You need Python 3.11+ and git.

```bash
python -m pip install context-rail     # Windows: py -m pip install context-rail
cd your-project
rail init --pack basic                 # sets up the workspace and walks you through the project brief
rail change add task "Write the first task"
rail overview                          # your daily starting page
rail serve ui                          # the local web view
```

`rail init` creates a `context-rail/` folder in your project and asks a few
questions about what you are building. If it reports that approval is missing,
approve the brief yourself:

```bash
rail --actor human approve charter --confirm "APPROVE charter" --confirmer "Your Name"
```

To let agents pick up the work, install the Factory `droid` CLI (or point
`RAIL_DROID_BIN` at it).

## How a day looks

1. **See where things stand.** `rail overview` shows progress, what is next
   and what is blocked.
2. **Approve the plan.** Nothing runs until a human approves the brief.
   Working alone? Solo mode skips the ceremony; the quality checks still apply.
3. **Hand out work.** `rail loop dispatch` gives an agent one task, a focused
   brief and its own sandbox.
4. **Prove it.** The checks you chose run against the agent's work.
5. **Review and merge.** A different actor reviews, and `rail loop merge`
   lands only approved work.

## Works for any kind of project

Pick a template when you start with `rail init --pack <name>`:

| Template | Best for | How work flows |
|---|---|---|
| `basic` | Getting started, small projects | todo → doing → done |
| `flow` | Work that arrives all the time, no sprints | backlog → ready → building → review → shipped |
| `scrum` | Teams that work in sprints | goal → scope → sprint → story → task |
| `waterfall` | Projects with distinct phases | planned → designing → building → testing → done |
| `video` | Video and content production | scene → shot, each shot: storyboard → generating → review → approved → published |

Want your own words or steps? Copy a template and make it yours with
`rail ops pack create --name <name> --from basic`. Renaming things never needs
re-approval; changing the rules does.

## Use it your way

- **Command line.** `rail` works for you and your agents alike. Every answer
  is one JSON reply that agents read easily.
- **Any MCP client.** `rail serve mcp` plugs Context Rail into AI tools that
  speak MCP.
- **Local web UI.** `rail serve ui` shows Flow, Plan and Records views. It runs
  on your machine only and is protected by a token.
- **Quick by default.** A small background helper keeps everyday commands fast
  and stops itself after 10 idle minutes.

## For engineers

- **Files are the truth.** The store is Markdown, JSON records and `rail.yaml`
  in your repo. SQLite is only a cache under the git dir; delete it and the
  next command rebuilds it.
- **One write path.** CLI, MCP and UI all go through validate → authorize →
  lock → atomic write → record → index.
- **Fail closed.** An unknown pack key, manifest schema or policy field is
  refused, never guessed.
- **Agents never read `pack.yaml`.** A compiler turns the pack into one model,
  and agents ask `rail next`, `rail show`, `rail why` and `rail model`.
- **No lost updates, safe retries.** Every write carries the etag it read, and
  every retry carries an idempotency key that returns the same record.
- **Project shape is data.** Six primitives, eight relations and five status
  categories are fixed; sprints, phases and scenes are pack data, not engine
  code.

The full picture, with the architecture, the operating model and every
command, is in
[How Context Rail works](https://github.com/nguyenhoanhson797/Context-Rail/blob/master/docs/how-it-works.md).

## Learn more

- [How Context Rail works](https://github.com/nguyenhoanhson797/Context-Rail/blob/master/docs/how-it-works.md): architecture, operating model, commands, surfaces
- [User guide](https://github.com/nguyenhoanhson797/Context-Rail/blob/master/docs/context-rail-v3-guide.html): diagrams and a zero-to-daily walkthrough
- [Interactive engine graph](https://github.com/nguyenhoanhson797/Context-Rail/blob/master/docs/context-rail-v3-engine-graph.html)
- [Agent onboarding kit](https://github.com/nguyenhoanhson797/Context-Rail/blob/master/docs/agent-onboarding/README.md): set up any agent harness
- [Operator skills](https://github.com/nguyenhoanhson797/Context-Rail/blob/master/skills/README.md): drop-in skills for your agents
- [Upgrading from v1/v2](https://github.com/nguyenhoanhson797/Context-Rail/blob/master/docs/how-it-works.md#upgrading-a-v1v2-store)

## Contributing

```bash
python -m pip install -e .[test]
python -m pytest -q -m "not slow and not perf"
```

The test lanes, speed budgets and working rules are in
[How Context Rail works](https://github.com/nguyenhoanhson797/Context-Rail/blob/master/docs/how-it-works.md#development)
and [AGENTS.md](https://github.com/nguyenhoanhson797/Context-Rail/blob/master/AGENTS.md).

## License

MIT. See [LICENSE](https://github.com/nguyenhoanhson797/Context-Rail/blob/master/LICENSE).
