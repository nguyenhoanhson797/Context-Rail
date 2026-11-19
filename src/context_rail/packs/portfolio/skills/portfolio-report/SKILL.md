---
schema: 1
version: 2
name: portfolio-report
description: Run the component CTO loop against the master portfolio store using a named session environment variable.
primitive: note
preset: skill
title: Portfolio report
tags: []
---
### Portfolio report

Act as `component-cto` against the master portfolio store while keeping your child repo's
own workflow in `RAIL_SESSION`. The human opens your master seat session and places the
token in `RAIL_SESSION_PORTFOLIO`.

1. Read your queue in the master store with `rail --store <master> --session-env RAIL_SESSION_PORTFOLIO next` and inspect your component brief with `rail --store <master> --session-env RAIL_SESSION_PORTFOLIO cto brief`.
2. Read shared `contract`, `decision`, and `convention` notes plus `depends_on` targets before planning child work.
3. Plan or move slices under your component through `planned`, `delegated`, and `integrating` (`rail do plan-slice`, `rail do delegate-slice`, `rail do integrate-slice`). You cannot sign off `verified` or `released`.
4. Declare a `store` Check (`mode: "store"`) on each slice pointing to your child store (`store`), child item slug (`item`), and target `category` (`done`) via `rail --store <master> --session-env RAIL_SESSION_PORTFOLIO check declare <slug> --target <slice> --spec-file <json> --expect absent`, then run `rail --store <master> --session-env RAIL_SESSION_PORTFOLIO check run <slice>` once the child item is committed in that category.
5. Record a workflow-shaped Markdown result on your component or slice with `rail --store <master> --session-env RAIL_SESSION_PORTFOLIO loop result <target> --body-file <md>` before lead verification (do not rely on chat-only summaries). When your tool reports effort, put it in the result's leading `---` block (or pass it on the candidate); the master's `measure usage --group-by item` groups it by slice or component, never as money.
6. File a structured report with `rail --store <master> --session-env RAIL_SESSION_PORTFOLIO loop report <slice> --report-file <json>` when handoff, dependency, or contract diagnostics need attention.
7. Never edit the pack, charter, or another component. Propose any pack, gate, or cross-component change by filing a report with `target_class` set to `human-wait`.
