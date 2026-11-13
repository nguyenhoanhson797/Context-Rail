---
name: context-rail-v3-loop
description: 'Run the Context Rail v3 record loop with any coding tool: brief, work in your own tool, candidate, verify, review, report, digest, plus the exact refusals and how a failure is reported. Load when handing out or reviewing agent work.'
---

# Context Rail v3 — the record loop

The rail does not run agents. Your coding tool does, with its own sub-agents,
models and sandboxes (Claude Code, Codex, opencode, Droid, Cursor, Gemini CLI,
or a person). The rail keeps the work order, the proof and the record. Whoever
leads the work keeps no state between sessions beyond the store and its memory
notes.

## The cycle

```text
1 Brief ─→ 2 Work ─→ 3 Candidate ─→ 4 Verify
                                      │
8 Digest ←─ 7 Report ←─ 6 Move ←─ 5 Review
    │
    └──────────────→ next session (back to 1)
```

Steps 1, 3, 4, 5 and 7 are rail commands. Step 2 is your tool, in your own
checkout. Step 6 is `rail change move`, which the item's gate allows or
refuses.

## Commands

```powershell
rail overview                                   # bounded starting page

rail loop brief <item> [--role implementer|reviewer] [--commit <sha>] [--max-chars N]
rail loop candidate <item> --commit <sha> [--base <sha>] [--model <text>] [--harness <text>]
                    [--usage NAME=NUMBER]... [--duration SECONDS] [--key <k>]
rail check run <item> [--worktree <path>]       # the declared checks, in that checkout
rail loop review <item> --commit <sha> --verdict approve|reject [--notes-file <md>] [--key <k>]
rail change move <item> <status> --expect <etag>

rail loop report [<item>] [--candidate <sha>] --report-file <json>
rail loop triage [--limit N] [--report <id>] [--acknowledge]
rail loop execution <item> --action <name> --state running|succeeded|failed|cancelled [--output <file>]... [--error <why>]
rail read ledger [--item <slug>] [--since <iso>]
```

- Every `--key` is an idempotency key: a retry returns the same record.
- Every `--expect` is the etag just read: a stale write is refused.
- `loop triage --report <id> --acknowledge` records a deliberate
  acknowledgement for one report below the recurrence threshold; it writes a
  `report.triaged` record and clears the doctor warning.

## How one item goes

1. **Brief.** `rail loop brief <item> --role implementer` prints the objective,
   the selected scope, the Checks, the code area and how to record results.
   Hand it to your own sub-agent. Nothing in it names a tool.
2. **Work.** Your tool works in its own checkout or git worktree and commits.
   The rail never creates the worktree and never runs git writes for you.
3. **Candidate.** `rail loop candidate <item> --commit <sha>` records the
   commit, the files it changed and, when you pass them, the model, harness,
   usage and duration. Recording the same commit again returns the first
   record.
4. **Verify.** `rail check run <item> --worktree <path>` runs the declared
   Checks in that checkout. A Check is fresh only while its inputs still match
   the commit.
5. **Review.** A different actor reads
   `rail loop brief <item> --role reviewer --commit <sha>` and records
   `rail loop review <item> --commit <sha> --verdict approve|reject`. The
   author of a candidate cannot approve it, and the charter may require a
   different model.
6. **Move.** Merge or integrate in git yourself, then
   `rail change move <item> done`. The gate reads the records: fresh Checks
   for the candidate, the review when the gate needs one, acceptance and any
   human verdict.
7. **Report.** `rail loop report <item> --report-file <json>`: target class,
   expectation, observation, impact, evidence. File one even when nothing
   happened.

## Role mode

When the pack declares roles, every caller acts through a seat. Open one with
`rail seat open <seat> --print-env` and export the printed `RAIL_SESSION`; the
seat's role decides which commands it may call and which items and paths it may
reach. A seat cannot record a candidate or a review for an item outside its
write reach, and independence compares the acting seat and its profile with the
seat and profile that produced the candidate. The code reach check re-resolves
the producer's patterns from its seat at review time.

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
- A work order carries the focus breadcrumb and the applicable actions; a
  worker reads them rather than scanning the store.
- A pack may keep a status out of the queue with `execution: {<status>: {queue: false}}`.

## What each step guarantees

| Step | Guarantee |
|---|---|
| Brief | A bounded, role-scoped work order built from the store; the same item gives the same sections for any tool |
| Candidate | One immutable record: item, commit, base, changed files, producer, usage and duration |
| Verify | The item's declared checks run in the checkout you name; a check is fresh only while its inputs still match |
| Review | A different actor's verdict on one candidate commit; the author cannot approve its own work |
| Move | The workflow's transition and gate decide; the rail records, it does not merge |
| Report | One structured report per piece of work: target class, expectation, observation, impact, evidence |
| Digest | Counts, waits, reversible decisions, quality, usage — the human view |

## Refusals you should expect

| Refusal | Cause |
|---|---|
| `INVALID_STATE: '<slug>' is not an executable leaf` | Finish or drop the dependency, or pick a leaf item |
| `INVALID_STATE: the charter is not approved` (the gateway says “the charter is not approved; nothing executes yet”) | Run `rail --actor human approve charter ...` first |
| `BUSINESS_RULE_VIOLATED: the item_done gate is not satisfied` | Missing or stale checks, review, acceptance or human verdict |
| `PERMISSION_DENIED` with `actor_required` | Name yourself: pass `--actor <name>` or set `RAIL_ACTOR` |
| `AMBIGUOUS_FOCUS: N containers are active` | Two containers match; pass `--into <id>` or finish one |
| `NO_FOCUS: a 'story' needs a parent 'sprint'` | Start the container, or pass `--rel part_of=<container>` |
| A review from the candidate's author | Separation rule; use a different actor or profile |
| A candidate that touches code outside the producer's reach (role mode) | Widen the seat's reach as a human, or split the change |
| A commit the rail cannot read | Run it from the checkout that holds the commit, or pass a sha |

## Failure and retry

- A failed Check does not fail the loop. Fix the work, commit again, record a
  new candidate and verify again.
- A rejected review is the same: the next candidate is a new record.
- Still failing → say so plainly in the report. Nothing escalates on a timer:
  the item stays where it is and shows up as too_long once its attempts or
  seconds pass the budget, or as stuck when it goes quiet.
- Cheaper than guessing: `rail read status` names the blocked reason;
  `rail overview` lists what needs a human.

## Budgets and the ledger

- A budget only warns. The brief and `rail ops doctor` name an item that is over
  its attempts or seconds; nothing is refused for it.
- Attempts and seconds always count. Token and credit budgets apply only when
  the candidate reported usage; `unknown` is recorded, never zero.
- `rail read ledger` lists candidates with attempt-counted usage and unknown
  counters kept apart.
- The charter and the profile each carry budgets; the stricter non-null bound
  is the one reported.

## Human touchpoints

| When | Command | What to look for |
|---|---|---|
| Morning | `rail overview` | approval, executable work, `needs_human`, stuck, too long |
| During the day | the brief's waits | approvals, stuck and too-long items, budget warnings |
| Evening | `rail read digest` | reversible decisions, quality, usage |
| Before pushing | `rail gate` | doctor issues, approval drift, open done gates |

You own the goal, the acceptance bar, secrets, money and anything
irreversible. Everything else is delegated on purpose.
