---
name: 2026-05-17-full-codebase-audit-and-fix-branch
description: Multi-agent code-review + QA sweep over the full repo, surgical fixes applied, branched and committed for PR
type: project
---

# 2026-05-17 — Full-codebase audit + fix branch

**TL;DR:** Ran `code-reviewer` and `qa` subagents twice — first against the 3 latest commits, then against the whole codebase. Verified diagnoses (caught one wrong one), applied 7 categories of fixes, opened branch `audit-fixes-2026-05-17` with 9 commits. Tests 465/465, lint 27/27. Discovered the light-mode theme-toggle icon-swap is a pre-existing bug — added to bugs-and-risks.

## What we discussed

- The standing CLAUDE.md subagent workflow (code-reviewer + qa in parallel, write to `.tmp/`, parent applies fixes) and how it scales from a single-commit review to a full-codebase audit
- Two passes of subagent review:
  - **Pass 1** (3 commits — `feat(topics)` + `fix(demand)` + `polish(ui)`): code-review PASS WITH NOTES (3 items), qa PASS (3 new edge-case tests for `previous_keywords`)
  - **Pass 2** (whole repo — 41 Python modules, Worker, 8,274-line SPA): code-review PASS WITH NOTES (3 HIGH, 7 MEDIUM, 7 LOW), qa PASS (9 new tests across 4 files; coverage gap map across every pipeline module)
- The Karpathy "verify the deliverable" lesson on practice: the code-reviewer's HIGH finding on Perplexity/Grok budget arithmetic had the location wrong — they said `prediction_budget` double-subtracts, but tracing showed the actual bug was `grok_budget` missing the demand reservation. Their proposed fix would have made it worse.
- The light-mode theme-toggle icon-swap bug — first thought I broke it with the CSS dedup, then proved (via injecting the deleted rules back via `<style>`) that it was pre-existing. Root cause is two competing implementations across 6+ rule blocks.

## What we decided

- **Fixes applied** (now on branch `audit-fixes-2026-05-17`, 9 commits):
  - `fix(predict)` — raise `ClaudeParseError` on missing prediction fields (typed failure mode, no behavior change)
  - `fix(run)` — Grok budget now reserves `demand_estimated_cents` (the actual budget bug); `_percentile_ranks` warns when input collapses to a single value; numpy import hoisted
  - `chore(summarize)` — `sys` imports hoisted to top-level (was 3 in-function calls)
  - `polish(ui)` — `forecastSVG` hex-color validator (defense-in-depth XSS); `.nav button:focus-visible`; removed 4 cleanly-dead `.theme-toggle` icon-state rules
  - `docs(worker)` — annotated soft-cap race + null-origin CORS bypass intent
  - `ci(snapshot)` — concurrency group on the daily cron (`cancel-in-progress: false` to avoid orphaning Anthropic batches)
  - `test` — 12 new tests covering `previous_keywords` edge cases, `niche_filter` `word_boundary=True` path, `clamp_peak_days` (new file), and `lifecycle_stage` priority order
  - `docs(memory)` — CLAUDE.md refresh to Project Operating Manual standard, plus strategy/decisions/bugs-and-risks expansions and the 3 untracked session summaries from earlier today
- **Deferred** (architectural or out-of-scope):
  - Worker KV → Durable Object migration (only if concurrent traffic outgrows solo-creator scale)
  - Theme-toggle CSS consolidation (separate PR — has the pre-existing light-mode bug attached)
- **Not pushed** — branch is local. Push and `gh pr create` require explicit chat confirmation per the standing memory rule.

## What's next

- Push `audit-fixes-2026-05-17` and open the PR (`gh pr create`) when ready
- Pick up the light-mode theme-toggle bug and CSS dedup as its own focused PR — verify both themes visually
- Watch `topic-carryover-unverified` on 2026-05-20 (first measurable date)
- Decide whether Worker concurrency justifies a Durable Object migration or if the soft-cap doc is sufficient
