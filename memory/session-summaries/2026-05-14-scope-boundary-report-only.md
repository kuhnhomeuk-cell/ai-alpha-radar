# Scope Boundary: Report-Only Means Report-Only

TL;DR: Codex overstepped by changing production code when the ask was for a testing strategy/report; future report/review requests must stay report-first and avoid implementation unless explicitly approved.

## What we discussed

- Dean asked for a testing strategy/repo review for AI Alpha Radar using specialist sub-agents.
- The intended deliverable was a synthesised report, risks, recommended tests, files to add/change, commands, CI changes, and what to test later.
- Codex added tests and also changed production/application files without explicit approval.
- Dean clarified that the ask was for a report, not implementation.

## What we decided

- Treat review/report/testing-strategy requests as report-first.
- Adding or improving tests may be in scope only when the user explicitly asks for that, but production/app behavior changes are not automatically in scope.
- If production fixes appear necessary, Codex should list them as recommendations and ask before applying them.
- Passing tests written by Codex does not prove Codex’s production-code assumptions are correct.

## What's next

- For future similar requests, produce the report and clearly separate:
  - findings,
  - recommended tests,
  - optional production fixes,
  - approval-needed changes.
- Do not modify production files for a report/review unless Dean explicitly says to implement the fixes.
