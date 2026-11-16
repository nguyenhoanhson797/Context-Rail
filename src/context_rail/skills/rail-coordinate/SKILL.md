---
schema: 1
version: 1
name: rail-coordinate
description: Run the CTO loop on the rail - read the queue, plan Items, hand off work, read Markdown result records, judge candidates, and leave human-only decisions to people.
primitive: note
preset: skill
title: Coordinate the rail
tags: []
---
### Coordinate the rail

Read the state before you act. Each step below uses a rail command, not a launcher.

1. Read the project with `rail overview`, then the executable queue with `rail next`.
2. If the focus is ambiguous, choose a container on purpose: `rail next --into <container>`, or `rail next --all` for every one. Neither escape skips a gate or a claim.
3. Plan one registered Item per assignment (individual shell reads are not separate assignments; the rail never claims to detect unregistered agent work). State each Item's acceptance and declared Checks.
4. Hand an Item to a worker with `rail loop brief <item> --role implementer`. The rail does not run agents. For research, discovery, design or other commitless work, the worker records `rail loop result <item> --body-file <md>` in the compiled shape without fabricating a commit. For code work, the worker commits, records `rail loop candidate <item> --commit <sha>`, and then records `rail loop result <item> --body-file <md> --commit <sha>`.
5. Read the exact producer Markdown result record (not a chat-only summary), run declared Checks with `rail check run <item>`, then hand off to a separate reviewer: `rail loop brief <item> --role reviewer` and `rail loop review <item> --verdict approve|reject --notes-file <md>`. The reviewer is never the implementer.
6. Move the Item with `rail change move <slug> <status> --expect <etag>`, or run a declared pack action with `rail do <action>`.
7. File `rail loop report <item> --report-file <json>` only as the diagnostic channel for rail or environment issues, and read `rail read digest` for waits, stuck Items and reversible decisions.

People decide these, and you ask for them: approve charters, packs and result adoptions (`rail --actor human ops upgrade` + `approve`), pass gates, change budgets, and edit acceptance Checks.
