---
schema: 1
version: 1
name: adaptive-evolution
description: Keep short lessons and propose structural pack changes through a report; never edit pack or surface definitions directly.
primitive: note
preset: skill
title: Adaptive evolution
tags: []
---
### Adaptive evolution

The pack improves through evidence, not by an agent rewriting its own rules.

After a change, ask whether the tier decision, the verification selection or the
review missed something. Record one lesson only when it would change the next
decision.

- Append or edit the project `lesson` note, or add one with
  `rail do capture-lesson --set title=<lesson>` and fill its four sections.
- Keep the lesson note under 150 lines. Merge duplicates, retire stale advice,
  and keep the newest evidence; a long note is not a better memory.
- Prefer a concrete trigger over a slogan: name the change, check or review that
  proved the lesson.

When the lesson calls for a structural change, propose it and stop:

- File `rail loop report --report-file <json>` with `target_class` set to
  `pack`, `skill`, `profile`, `gate` or `engine` and `target_class` in the human
  queue. Include the evidence and the smallest change that would prevent the
  miss.
- Use `rail ops measure usage --group-by item|model|skill` and
  `rail ops measure versions` to show whether a change helped before proposing
  another.
- Never edit `pack.yaml`, `surface.yaml`, `layout.yaml`, a profile or a skill
  file directly. Never apply a patch to a pack or surface. A human approves the
  structural change through the normal pack and charter approval path.
