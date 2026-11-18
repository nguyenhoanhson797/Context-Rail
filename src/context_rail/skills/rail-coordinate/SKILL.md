---
schema: 1
version: 3
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
3. Plan one registered Item per assignment (individual shell reads are not separate assignments; the rail never claims to detect unregistered agent work). State each Item's acceptance and declared Checks. Use the `task` preset for research, and record a conclusion only when it will be reused or changes a decision; navigation is not recorded.
4. Hand an Item to a worker with `rail loop brief <item> --role implementer`. The rail does not run agents. For research, discovery, design or other commitless work, the worker records `rail loop result <item> --body-file <md>` in the compiled shape without fabricating a commit. For code work, the worker commits, records `rail loop candidate <item> --commit <sha>`, and then records `rail loop result <item> --body-file <md> --commit <sha>`. The result body may open with a `---` effort block (`duration_s`, `usage: {tokens: ..., requests: ...}`, `model`, `harness`); state `unknown` for a counter the harness does not report, never guess, and never report money.
5. Read the exact producer Markdown result record (not a chat-only summary), run declared Checks with `rail check run <item>`, then hand off to a separate reviewer: `rail loop brief <item> --role reviewer` and `rail loop review <item> --verdict approve|reject --notes-file <md>`. The reviewer is never the implementer. Effort joins candidate and result once per attempt; `rail ops measure usage --group-by item` shows it per task.
6. Move the Item with `rail change move <slug> <status> --expect <etag>`, or run a declared pack action with `rail do <action>`.
7. File `rail loop report <item> --report-file <json>` only as the diagnostic channel for rail or environment issues, and read `rail read digest` for waits, stuck Items and reversible decisions.

Approval records its signer. As the named lead you may sign `rail approve <target> --confirm "APPROVE <target>" --confirmer "<human>"` when the charter allows it (`approval.agents: true`, the default); name the human the approval is for, and never claim a human signature you did not receive. When the charter sets `approval.agents: false`, print the exact `rail --actor human approve ...` command and wait. A charter may also delegate named operational actions to you (`delegation: {to: <seat>, actions: [...]}`); when it does and the charter approval is current, run those actions yourself and report the `delegation` facts. Never widen the list: `--force`, a pack switch, a migration, `init`, `pack create` and any action not listed still wait for the human. Otherwise people decide these, and you ask for them: result adoptions (`rail --actor human ops upgrade`, then reapprove as above), human gate verdicts, budget changes, and authoring or loosening acceptance Checks.
