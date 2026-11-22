---
schema: 1
version: 1
name: adaptive-intake
description: Recognize a change as trivial, standard or critical, write its contract, and hold critical work for human approval.
primitive: note
preset: skill
title: Adaptive intake
tags: []
---
### Adaptive intake

Recognize the tier before creating the work, from blast radius, reversibility,
data, security, public interface and uncertainty. Never from line count alone.

- **Trivial**: docs, formatting, a dependency or configuration bump, or a
  one-line fix with no behaviour, interface or data change. Create a
  `trivial-change`; no new test is owed.
- **Standard**: an ordinary feature, fix or refactor with a known local blast
  radius and existing checks that can prove it. Create a `standard-change` and
  write its `contract` note.
- **Critical**: security, payments, secrets, authentication, data migration,
  a public interface, an irreversible command, or a blast radius you cannot
  bound. Create a `critical-change` and write its `contract` note.
- When uncertain, choose the higher tier. Record the trigger that decided it in
  the Item body, not only in chat.

Before building:

1. For a standard change, write the `contract` note and run
   `rail do contract-standard --item <change> --etag <etag>`.
2. For a critical change, write the `contract` note and run
   `rail do contract-critical --item <change> --etag <etag>`. Then stop.
3. Ask the human to read the contract and answer in their own words. When their
   reply is an explicit yes, record exactly what they said:
   `rail consent <change> --on-behalf-of "<name>" --text "<their exact words>" --expect <etag>`
   Record only words the human actually gave; never infer consent, paraphrase
   it, or record it before they answer. The human may instead approve from the
   Item sheet in `rail serve ui`, which records the same consent. If the
   charter sets `approval.agents: false`, the human records the consent.
4. Run `rail do approve-critical --item <change> --etag <etag>`. The action
   refuses until a consent record covers the change's current version, then
   starts the critical verification path.

Build and verification:

- Start a trivial change with `rail do start-trivial --item <change> --etag <etag>`.
- Start a contracted standard change with
  `rail do start-standard --item <change> --etag <etag>`.
- Start an approved critical change with
  `rail do start-critical --item <change> --etag <etag>`.
- Move built work to verification with
  `rail do verify-change --item <change> --etag <etag>`, run the Items checks
  with `rail check run <item>`, and close only with
  `rail do close-change --item <change> --etag <etag>`.

A standard or critical change that grows past its contract goes back to
`contracted` or a higher tier; do not silently widen it.
