---
schema: 1
version: 1
name: rail-triage
description: Turn filed reports into fixes, decisions or proposals for the human, and close each loop.
primitive: note
preset: skill
title: Triage reports
tags: []
---
### Triage reports

Read the reports before you act on any of them. `rail loop triage` clusters them, so work the clusters rather than single reports.

Each report has an impact (`low`, `medium`, `high` or `critical`), a fingerprint that groups repeats, and evidence. Read the evidence itself, not only the summary. A summary can say "flaky" where the evidence shows a refusal.

Work the clusters by impact, `critical` first. For each cluster, choose one outcome:

- An Item: create a fix with `rail add --preset fix --title <title>`. Give it the acceptance and the Checks that prove the fix works.
- A decision: record it with `rail add --preset decision --title <title>`. State what was decided, why, and which reports it covers.
- A proposal: when the fix needs a change the human must approve, file the proposed change for the human with `rail loop report <item> --report-file <json>` and stop. Do not apply it yourself.

Name the report fingerprint in each Item or decision so the trail links back to the evidence. A cluster is closed once it has one outcome. If anything in it is still open, say what is open and who owns it.
