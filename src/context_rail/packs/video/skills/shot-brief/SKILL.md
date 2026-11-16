---
schema: 1
version: 1
name: shot-brief
description: Turn a storyboarded shot into a take anyone can regenerate.
primitive: note
preset: skill
title: Shot brief
tags: []
---
# Shot brief

1. Read the shot's storyboard, acceptance, `model` and `duration`, compiled result
   sections (`rail loop brief <shot>`), and neighboring shots in the scene for continuity.
2. Write the prompt down before you generate: subject, framing, camera move, lighting
   and anything the acceptance names.
3. Generate, and record the model, seed, duration and every reference asset you used.
4. Compare the take to the acceptance yourself; regenerate rather than hand over a take
   you would refuse.
5. Commit tracked prompt/output changes if needed (`rail loop candidate`), then record
   your Markdown result with `rail loop result <shot> --body-file <md>`. Keep
   `rail loop report` for diagnostic problems.

A take you cannot regenerate is a guess, not a deliverable.
