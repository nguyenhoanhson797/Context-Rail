---
schema: 1
version: 1
name: code-map
description: Record every new or changed code unit as a linked code note.
primitive: note
preset: skill
title: Code map
tags: []
---
# Code map

A code change is not complete until the code map names it. The map is part of the
deliverable, not documentation written after the fact.

- Every new or changed source file gets one `code` note:

  `rail add code "<path>" --set source_path=<path> --set kind=file`

- Add a separate note for a class, function or method only when it is shared or
  high-risk (`--set kind=function`, `class` or `method`), then link it to its file
  note with `part_of`.
- Record dependencies in the direction "this unit depends on that unit":
  `rail link <this> depends_on <that>`. Editing `<that>` affects every note whose
  `depends_on` points at it.
- Use `source_path`, never `path`: the engine reserves `path`.
- Run `rail` from your worktree, never with `--store` pointing at the canonical store.
- If you implement, write or update the notes in your worktree store and commit
  them with the code. If you review, check them against the diff. If you are the
  CTO, refuse a merge whose candidate lacks them; do not write them for the
  implementer.
- `rail show <slug>` lists both directions. `rail find --preset code --rel
  depends_on=<slug>` lists direct dependents.

Never edit the store by hand: every note and link goes through `rail`.
