---
schema: 1
version: 1
name: sprint-cadence
description: Run sprints on the scrum rail - start and close them, file bugs and triage reports with the declared actions.
primitive: note
preset: skill
title: Sprint cadence
tags: []
---
### Sprint cadence

- Start a sprint with `rail do start-sprint --item <sprint> --etag <etag>` once its
  Stories are ready. It moves the sprint from `todo` or `ready` to `doing`.
- Require each subagent assignment to record a `rail loop result` Markdown record (not
  chat-only summaries), plus `rail loop candidate` when code changed; keep
  `rail loop report` for diagnostic issues.
- Close a sprint with `rail do close-sprint --item <sprint> --etag <etag>` only when
  every Story and task in it is finished and the sprint itself has a current completed
  result record.
- Record a defect before anyone fixes it with
  `rail do file-bug --set title=<title> --set parent=<sprint>`. A bug moves `reported`,
  `fixing`, `verifying`, then `closed`, using the `bugfix` result shape.
- Triage one report at a time with `rail do triage-report --set report=<report>`, then
  take the outcome the rail-triage skill describes.
- Move a Story to `done` only after a current completed result exists, a separated
  review approves the work, and its Checks pass.
