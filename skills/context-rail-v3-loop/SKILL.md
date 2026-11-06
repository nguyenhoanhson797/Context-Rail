---
name: context-rail-v3-loop
description: 'Run and supervise the Context Rail v3 agent loop: brief, plan, dispatch, verify, review, merge, report, digest, plus the exact refusals, retries and escalation rules. Load when launching or reviewing agent work.'
---

# Context Rail v3 — the agent loop

The CTO keeps no state between ticks beyond the store and its memory notes.
One tick is: read the state, choose work, launch it, verify it, review it,
merge it, report it, summarize it.

## The cycle

```text
1 Brief ─→ 2 Plan ─→ 3 Dispatch ─→ 4 Verify
                                    │
8 Digest ←─ 7 Report ←─ 6 Merge ←─ 5 Review
    │
    └──────────────→ next tick (back to 1)
```

Blue steps (Brief, Digest) are where humans usually look. The rest can run
unattended inside the charter's green and yellow rights.

## Commands

```powershell
rail overview                                   # bounded starting page
rail loop tick --key day-2026-11-02             # one CTO session, single-instance lock

rail loop dispatch <item> --expect <etag> --key <k> [--profile <name>] [--intent "<note>"]
rail loop review   <item> --run <run-id> --key <k> [--profile <name>]
rail loop merge    <item> --run <run-id> --expect <etag> --key <k> [--push]

rail loop report [<item>] --run <run-id> --report-file <json>
rail loop triage [--limit N]
rail read ledger [--item <slug>] [--since <iso>]
```

- Every `--key` is an idempotency key: a retry returns the same record.
- Every `--expect` is the etag just read: a stale write is refused.
- `loop tick` must be the outer session; it refuses when `RAIL_ACTOR` is set.

## Focus and the queue

The pack names one focus container (the active sprint, the current phase, the
active scene). The queue is focus-first and the brief's headline names it.

- `rail next` returns only the operating queue. `rail next --all` reaches every
  item preset when the focus is not what you want; it still respects workflow
  transitions, gates, claims and actor permissions.
- An ambiguous focus is refused with `AMBIGUOUS_FOCUS` and its candidates. Use
  `rail next --into <id>` or move one container out of the active category.
  Never pick one by guessing.
- `NO_FOCUS` on `add` means the pack needs a parent and none matches: pass
  `--rel part_of=<container>` or start the container first.
- A work order's card carries the focus breadcrumb and the applicable actions;
  a worker reads them rather than scanning the store.

## What each step guarantees

| Step | Guarantee |
|---|---|
| Dispatch | A fresh git worktree and branch, a bounded `droid exec`, a run record, then verification. `--intent` adds a bounded CTO note (approach and rejected alternatives) to the objective; leave it out and the order carries only the Item. |
| Verify | The item's declared checks run in that worktree; a check is fresh only while its inputs still match |
| Review | A different actor in its own worktree; the implementer cannot approve its own work; the charter may require a different model |
| Merge | Rebase onto the default branch, then fast-forward; `--push` only when the charter allows |
| Report | One structured report per completed run: target class, expectation, observation, impact, evidence |
| Digest | Counts, waits, reversible decisions, quality, usage — the human view |

## Refusals you should expect

| Refusal | Cause |
|---|---|
| `INVALID_STATE: '<slug>' cannot be dispatched: depends_on:<x>` | Not an executable leaf; finish or drop the dependency |
| `INVALID_STATE: ... is a 'reviewer', not an implementer` | Wrong profile recipe for the command |
| `INVALID_STATE: the charter is not approved` (the gateway says “the charter is not approved; nothing executes yet”) | Run `rail --actor human approve charter ...` first |
| `BUSINESS_RULE_VIOLATED: the item_done gate is not satisfied` | Missing/stale checks, review, acceptance or human verdict |
| `'cto tick' cannot be nested inside a running session` | A child session already set `RAIL_ACTOR` |
| `AMBIGUOUS_FOCUS: N containers are active` | Two containers match; pass `--into <id>` or finish one |
| `NO_FOCUS: a 'story' needs a parent 'sprint'` | Start the container, or pass `--rel part_of=<container>` |
| A review from the implementer | Separation rule; use a distinct reviewer profile |
| A merge with a conflicting candidate | One rebase retry, then escalate to the human |

## Failure and retry

- A failed run gets one retry by default (the pack's `policy.retries`), continuing
  the same `droid` session with the failure output attached.
- Still failing → the item escalates with its evidence; it is not retried
  forever.
- A merge conflict gets one rebase retry, then escalation.
- Cheaper than guessing: `rail read status` names the blocked reason;
  `rail overview` lists what needs a human.

## Budgets and the ledger

- Attempts and seconds always count. Token and credit budgets apply only when
  the run reported usage; `unknown` is recorded, never zero.
- `rail read ledger` lists runs with attempt-counted usage and unknown counters
  kept apart.
- The charter and the profile each carry budgets; dispatch uses the stricter
  non-null bound.

## Human touchpoints

| When | Command | What to look for |
|---|---|---|
| Morning | `rail overview` | approval, executable work, `needs_human`, stuck, too long |
| During the day | the brief's waits | approvals, escalations, budget pressure |
| Evening | `rail read digest` | reversible decisions, quality, usage |
| Before pushing | `rail gate` | doctor issues, approval drift, open done gates |

You own the goal, the acceptance bar, secrets, money and anything
irreversible. Everything else is delegated on purpose.
