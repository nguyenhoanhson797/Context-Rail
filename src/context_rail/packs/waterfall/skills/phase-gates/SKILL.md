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
- Complete a phase with `rail do complete-phase --item <phase> --etag <etag>` when it is
  in `testing`. The rail refuses it while a deliverable is open (the phase requires its
  children to be done) or while its done gate is unmet. If it refuses, report the refusal
  and stop; never ask to lower a gate.
- Keep risks visible: file each one as a `risk` Item under the phase it threatens with
  `rail change add risk "<title>" --rel part_of=<phase> --set severity=<level>`.
  Do not drop a risk when its phase moves on.
