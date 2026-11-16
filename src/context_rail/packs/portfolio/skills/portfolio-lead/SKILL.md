---
schema: 1
version: 1
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
- Delegate each component's seat with `rail seat grant <seat> --role component-cto --anchor <component>`, and open its session token for the component environment with `rail seat open <seat> --print-env`.
- Require each subagent or `component-cto` assignment to leave a `rail loop result` Markdown record on its slice or component (not chat-only summaries); keep `rail loop report` for diagnostics.
- Move a `slice` from `integrating` to `verified` with `rail do verify-slice --item <slice> --etag <etag>` only once its current completed slice result and declared `store` Check pass fresh.
- Group verified slices into a `release` (`rail change add release "<name>" --rel depends_on=<slice>`), move it to `releasing` (`rail change move <release> releasing --expect <etag>`), record the release result, and close it with `rail do ship-release --item <release> --etag <etag>` through the `release` gate.
- Triage incoming reports with `rail do triage-report --set report=<report>` and escalate charter, gate or pack changes to the human.
