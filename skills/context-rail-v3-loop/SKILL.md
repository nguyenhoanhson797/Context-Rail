---
name: context-rail-v3-loop
description: 'Use when handing out, recording, verifying, or reviewing agent work through Context Rail v3: brief, workflow-shaped Markdown result records (commitless research or commit-bound code), candidate, review, report, digest, and refusals.'
---

# Context Rail v3 — the record loop

The rail does not run agents. Your coding tool does, with its own sub-agents,
models and sandboxes (Claude Code, Codex, opencode, Droid, Cursor, Gemini CLI,
or a person). The rail keeps the work order, the proof and the record. Whoever
leads the work keeps no state between sessions beyond the store and its memory
notes. Register one item per separate assignment (individual shell reads inside
an assignment do not need items; the rail never claims to detect unregistered
work).

## The cycle

Two public loop paths share one evidence contract:

```text
Research / commitless:
  1 Brief ─→ 2 Work ─→ 3 Result ─→ 4 Verify (when declared) ─→ 5 Review ─→ 6 Move ─→ 7 Report ─→ 8 Digest

Code:
  1 Brief ─→ 2 Work/commit ─→ 3 Candidate ─→ 4 Result --commit ─→ 5 Verify ─→ 6 Review ─→ 7 Move ─→ 8 Report ─→ 9 Digest
```

Steps 1, 3–6 and 7/8 are rail commands. Working/committing is your tool, in
your own checkout. Move is `rail change move`, which the item's gate allows or
refuses.

## Commands

```powershell
rail overview                                   # bounded starting page
rail read map [<item>]                          # where this item sits in the whole picture

rail loop brief <item> [--role implementer|reviewer] [--commit <sha>] [--max-chars N]
rail loop result <item> --body-file <md> [--commit <sha>] [--outcome completed|partial|blocked|failed]
                 [--set <k=v>]... [--model <text>] [--harness <text>] [--key <k>]
rail loop candidate <item> --commit <sha> [--base <sha>] [--model <text>] [--harness <text>]
                    [--usage NAME=NUMBER]... [--duration SECONDS] [--key <k>]
rail check run <item> [--worktree <path>]       # the declared checks (incl. mode: result)
rail loop review <item> [--commit <sha>] [--result <id>] --verdict approve|reject
                 [--notes-file <md>] [--set <k=v>]... [--key <k>]
rail change move <item> <status> --expect <etag>

rail loop report [<item>] [--candidate <sha>] --report-file <json>
rail loop triage [--limit N] [--report <id>] [--acknowledge]
rail loop execution <item> --action <name> --state running|succeeded|failed|cancelled [--output <file>]... [--error <why>]
rail read ledger [--item <slug>] [--since <iso>]
rail ops measure usage --group-by item|model|harness|role|profile|skill
                   [--item <slug>] [--since <iso>] [--until <iso>] [--limit N]
```

- Every `--key` is an idempotency key: a retry returns the same record (even if
  the temporary body file disappeared after the first commit).
- Every `--expect` is the etag just read: a stale write is refused.
- Paths and final titles are derived: `loop result` has no `--path` or `--title`.
- `loop triage --report <id> --acknowledge` records a deliberate
  acknowledgement for one report below the recurrence threshold; it writes a
  `report.triaged` record and clears the doctor warning.

## One loop for every work kind

Code, research, investigation, find and review all run one loop, keyed by the
item's `taskId`:

```text
plan    rail change add task "<title>" --rel part_of=<container>
pick    rail next
hand    rail loop brief <taskId>
work    ... any harness, any sub-agents ...
report  rail loop result <taskId> --body-file <file> [--commit <sha>]
        rail loop candidate <taskId> --commit <sha> --usage tokens=...  (code only)
review  rail loop review <taskId> --result <id> --verdict approve
close   rail change move <taskId> <done-status>
learn   rail loop report <taskId> --report-file <json>                (optional)
```

Record proportionately:

- Navigation and orientation are not recorded.
- A conclusion that will be reused or will change a decision is a task plus a
  commitless result naming the model and harness that produced it.
- A decision that changes scope, design, a gate or an approval must cite
  evidence that already exists in the store.
- Sub-agents are the harness's own business: the parent creates the task, passes
  the brief, and records the answer as the accountable producer. The rail never
  launches an agent and cannot see inside a harness's tool calls.
- Use the `task` preset for research, not `question`: a lead's `can.move` names
  the presets it can close (the portfolio lead's: `component, slice, release,
  task, bug, fix`), so a question cannot be closed by that lead.
- `loop result` is the report step; `loop report` is optional findings for triage.

## How one item goes

1. **Brief.** `rail loop brief <item> --role implementer` prints the objective,
   the selected scope, the compiled result shape and derived path, the Checks,
   the code area and how to record results. Hand it to your own sub-agent.
   Nothing in it names a tool or parses `pack.yaml`.
2. **Work.** Your tool works in its own checkout or git worktree and commits
   when code changed. The rail never creates the worktree and never runs git
   writes for you.
3. **Candidate (code work only).** `rail loop candidate <item> --commit <sha>`
   records the commit, the files it changed and, when you pass them, the model,
   harness, usage and duration. For discovery, research, design or other
   commitless work, skip `candidate` and never fabricate a commit SHA.
4. **Result.** `rail loop result <item> --body-file <md>` writes an immutable
   workflow-shaped Markdown record under `records/`:
   - Body limit: `256 KiB` (`262,144` bytes) checked on raw input bytes; BOM/CRLF
     normalized to LF.
   - Required sections: exact, ordered, unique nonempty `##` headings outside
     code fences (`Not applicable: <reason>` counts; extra headings allowed).
   - Commitless work leaves `candidate_commit` null (`None`); code work passes
     `--commit <sha>` matching a recorded candidate on the item.
   - Outcome: `completed`, `partial`, `blocked`, or `failed`. Only `completed`
     can satisfy a done gate; a newer partial/failed/altered attempt supersedes
     older proof.
   - Effort: a leading `---` block before the first heading carries
     `duration_s`, `usage: {tokens: ..., requests: ...}`, `model` and `harness`.
     The block stays in the stored body, so the review binds the numbers too.
     Use `unknown` for a counter the harness does not report; never guess, and
     never report money. An unknown key or an invalid counter refuses the write,
     and `--model`/`--harness` may not name the same fact the block names.
5. **Verify.** `rail check run <item> [--worktree <path>]` runs the declared
   Checks (including `mode: result`, which checks the current completed producer
   result via file-only freshness). A Check is fresh only while its inputs and
   result binding still match.
6. **Review.** A different actor reads
   `rail loop brief <item> --role reviewer [--commit <sha>]` (bounded with up
   to `4000` chars of the selected result body, `5` history entries, and `20`
   lead summaries; full bodies stay on authorized `read show`) and records
   `rail loop review <item> [--commit <sha>] [--result <id>] --verdict approve|reject --notes-file <md>`.
   The author of a candidate or result cannot approve it, reviewer notes never
   count as producer evidence, and the charter may require a different model.
7. **Move.** Merge or integrate in git yourself, then
   `rail change move <item> done`. The gate reads the records: current completed
   producer result bound to item content and work cycle, fresh Checks, the
   separated review, acceptance and any human verdict.
8. **Report.** `rail loop report <item> --report-file <json>` is the diagnostic
   channel (target class, expectation, observation, impact, evidence) when
   something in the rail or environment needs attention, even when nothing
   happened.

## Role mode and adoption

When the pack declares roles, every caller acts through a seat. Open one with
`rail seat open <seat> --print-env` and export the printed `RAIL_SESSION`; the
seat's role decides which commands it may call and which items and paths it may
reach (`can.result` and `sees.parts` `results` are role-scoped). A seat cannot
record a result, candidate or review for an item outside its write reach, and
independence compares the acting seat and its profile with the seat and
profile that produced the candidate or result.

Existing stores adopt shipped result contracts explicitly when a human runs
`rail ops upgrade --confirm "UPGRADE store"` (name mode: `--actor human`; a
role-mode store acts by seat, so a terminal or the local UI, with no
`--actor`), followed by `approve`;
unchanged historical done items stay preserved without fabricating retroactive
results.

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
| `INVALID_STATE: the charter is not approved` (the gateway says “the charter is not approved; nothing executes yet”) | Run `rail approve charter ... --confirmer "<you>"` first; add `--actor human` when the charter requires a human signature |
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
- Attempts count candidates. Seconds and usage counters sum the joined effort:
  a result bound to a commit supplies that attempt's numbers, and a commitless
  result is its own pending attempt. `unknown` is recorded, never zero.
- `rail ops measure usage --group-by item` is the per-task view of that effort;
  the digest's usage line reads the same join.
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
