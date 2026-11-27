---
schema: 1
version: 2
name: portfolio-lead
description: Coordinate components, slices, contracts, releases and component-cto seats on the portfolio rail.
primitive: note
preset: skill
title: Portfolio lead
tags: []
---
### Portfolio lead

Run the master portfolio store as `portfolio-lead`. Keep cross-repo truth in committed
child stores and declared `store` Checks, never in prose alone.

- Register each child repository as a `component` with `rail change add component "<name>" --set store=<path>` and start it with `rail do start-component --item <component> --etag <etag>`.
- Break cross-repo deliverables into `slice` items under their component (`rail change add slice "<name>" --rel part_of=<component>`) and link cross-component order with `--rel depends_on=<slice>`.
- Record shared interfaces as `contract` notes (`rail change add contract "<name>"`) so every `component-cto` can read them.
- Delegate each component's seat with `rail seat grant <seat> --role component-cto --anchor <component>`. For a local component agent, bind it once with `rail agent enable <seat> --role component-cto --anchor <component>`; for a remote environment, open its session token with `rail seat open <seat> --print-env`.
- If the master store has a local agent credential, you are the lead: run `rail read map`, `rail change ...` and `rail do ...` directly. Check it with `rail agent show`; if it is disabled, ask the project owner once to run `rail agent enable portfolio-lead --role portfolio-lead --anchor project` in a terminal or the local UI. Never ask for or paste a token into the conversation. For a shared or remote master, the owner may instead open a seat with `rail seat open portfolio-lead --print-env` and set `RAIL_SESSION` in the environment that runs the lead.
- Require each subagent or `component-cto` assignment to leave a `rail loop result` Markdown record on its slice or component (not chat-only summaries); keep `rail loop report` for diagnostics.
- Move a `slice` from `integrating` to `verified` with `rail do verify-slice --item <slice> --etag <etag>` only once its current completed slice result and declared `store` Check pass fresh.
- Group verified slices into a `release` (`rail change add release "<name>" --rel depends_on=<slice>`), move it to `releasing` (`rail change move <release> releasing --expect <etag>`), record the release result, and close it with `rail do ship-release --item <release> --etag <etag>` through the `release` gate.
- Track the portfolio with `rail overview` and `rail read digest` for waits, stuck components and usage; `rail ops measure usage --group-by item` shows recorded effort per slice or component, with unknown counters kept apart.
- Triage incoming reports with `rail do triage-report --set report=<report>` and escalate charter, gate or pack changes to the human.
