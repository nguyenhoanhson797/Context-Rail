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

- Register each child repository as a `component` with `rail add --preset component --title <name> --set store=<path>` and start it with `rail do start-component --item <component> --etag <etag>`.
- Break cross-repo deliverables into `slice` items under their component (`--rel part_of=<component>`) and link cross-component order with `--rel depends_on=<slice>`.
- Record shared interfaces as `contract` notes so every `component-cto` can read them.
- Delegate each component's seat with `rail seat grant <seat> --role component-cto --anchor <component>`, and open its session token for the component environment with `rail seat open <seat> --print-env`.
- Move a `slice` from `integrating` to `verified` with `rail do verify-slice --item <slice> --etag <etag>` only once its declared `store` Check passes fresh.
- Group verified slices into a `release` (`--rel depends_on=<slice>`), move it to `releasing`, and close it with `rail do ship-release --item <release> --etag <etag>` through the `release` gate.
- Triage incoming reports with `rail do triage-report --set report=<report>` and escalate charter, gate or pack changes to the human.
