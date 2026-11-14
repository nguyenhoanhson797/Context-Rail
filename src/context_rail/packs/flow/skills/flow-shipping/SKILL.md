---
schema: 1
version: 1
name: flow-shipping
description: Ship stories on the flow rail - pull them into work, send built stories to review, ship reviewed ones and record defects.
primitive: note
preset: skill
title: Flow shipping
tags: []
---
### Flow shipping

- Pull a story into work with `rail do start --item <story> --etag <etag>` from
  `backlog` or `ready`. It moves to `building`.
- When a built story's Checks pass and its commit is recorded as a candidate, send it to
  its separated review with `rail do request-review --item <story> --etag <etag>`.
- Ship a story only after its review approves the candidate:
  `rail do ship --item <story> --etag <etag>` moves it from `review` to `shipped`.
- Record a defect before it is fixed with `rail do file-bug --set title=<title>`.
- Triage one report at a time with `rail do triage-report --set report=<report>`, then
  take the outcome the rail-triage skill describes.
- Before you pull new stories, work the stuck and too-long items that `rail read digest`
  names.
