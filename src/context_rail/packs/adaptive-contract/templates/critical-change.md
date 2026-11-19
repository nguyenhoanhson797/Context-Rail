# {title}

## Objective

What the change must accomplish, in one or two sentences.

## Contract

The behaviour, interfaces, data and compatibility this change promises.

## Blast radius

The files, interfaces, data, users and systems this can affect.

## Irreversible effects

What cannot be undone cheaply: data migration, published interfaces, secrets,
payments, destructive commands or a force push.

## Human approval

Who approved the work, when, and what they approved. The `approval` field is
written by the human before `rail do approve-critical` may run.

## Verification plan

The few boundary, failure-path and smoke tests that prove the contract, plus the
existing checks. No internal mocks.

## Rollback

How the change is reverted if verification fails after approval.
