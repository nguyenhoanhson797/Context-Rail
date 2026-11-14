---
schema: 1
version: 1
name: phase-gates
description: Run waterfall phases - start and complete them with the declared actions, and keep risks visible until they are closed.
primitive: note
preset: skill
title: Phase gates
tags: []
---
### Phase gates

- Start a planned phase with `rail do start-phase --item <phase> --etag <etag>`. It
  moves to `designing`. Move its deliverables through `building` and `testing` with
  `rail change move` as their work moves.
- Complete a phase with `rail do complete-phase --item <phase> --etag <etag>` only from
  `testing`, and only when every deliverable in it is finished.
- Passing a phase's done gate is a human decision. When its Checks pass, ask a person to
  pass the gate; do not complete the phase on your own.
- Keep risks visible: file each one as a `risk` Item under the phase it threatens with
  `rail add --preset risk --title <title> --rel part_of=<phase> --set severity=<level>`.
  Do not drop a risk when its phase moves on.
