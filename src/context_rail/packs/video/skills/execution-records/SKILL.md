---
schema: 1
version: 1
name: execution-records
description: Record render attempts on shots with rail execution record, so progress comes from what actually ran.
primitive: note
preset: skill
title: Execution records
tags: []
---
### Execution records

- Record each attempt of the declared `render` action on its shot with
  `rail execution record <shot> --action render --state <state>`.
- The states are `running`, `succeeded`, `failed` and `cancelled`. Give a failed or
  cancelled attempt its reason with `--error <why>`.
- Attach every file or URL an attempt produced with `--output <path or URL>`, one flag
  for each.
- Progress comes from execution records alone. A record shows what ran; it does not
  approve a take or pass a gate.
