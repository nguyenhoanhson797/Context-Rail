---
schema: 1
version: 1
name: rail-coordinate
description: Run the CTO loop on the rail - read the queue, plan Items, hand off work, judge candidates, and leave human-only decisions to people.
primitive: note
preset: skill
title: Coordinate the rail
tags: []
---
### Coordinate the rail

Read the state before you act. Each step below uses a rail command, not a launcher.

1. Read the project with `rail overview`, then the executable queue with `rail next`.
2. If the focus is ambiguous, choose a container on purpose: `rail next --into <container>`, or `rail next --all` for every one. Neither escape skips a gate or a claim.
3. Plan work as Items. Each Item states its acceptance and the Checks that prove it.
4. Hand an Item to a worker with `rail loop brief <item> --role implementer`. The worker records its commit with `rail loop candidate <item> --commit <sha>`. The rail does not run agents.
5. Judge the candidate by its passing Checks, then by a separate reviewer: `rail loop brief <item> --role reviewer` and `rail loop review`. The reviewer is never the implementer.
6. Move the Item with `rail change move <slug> <status> --expect <etag>`, or run a declared pack action with `rail do <action>`.
7. Read `rail read digest` for waits, stuck Items and reversible decisions.

People decide these, and you ask for them: approve charters and packs, pass gates, change budgets, and edit acceptance Checks.
