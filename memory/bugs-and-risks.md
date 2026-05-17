---
name: bugs-and-risks
description: Open issues and watch-outs for ai-trends
type: project
---

*Last updated: 2026-05-17*

Format: - **slug** *(severity / category)*: description · evidence · what would resolve it

## Open

- **topic-carryover-unverified** *(medium / regression-watch)*: Task 4 (yesterday-keyword bias on topic extraction) shipped via `6073f63` and signed off, but the empirical 3-day day-over-day keyword overlap can't be measured until 3 more daily snapshots accumulate. First measurable date: 2026-05-20. **Measurement tool now exists** — run `poetry run python scripts/topic_carryover.py` against the snapshots on or after that date and check the PASS / FAIL verdict. First-pass run on 2026-05-17 showed 0% on the (single) post-bias pair (2026-05-16 → 2026-05-17), but this is likely because the bias commit landed AFTER that day's 06:00 UTC daily cron rather than the bias being broken — confirm with 2026-05-18+ data. If <50% across 3 post-bias pairs, sharpen the bias prompt. Evidence: `memory/next-actions.md` Task 4 final checkbox; `scripts/topic_carryover.py` (commit `96c3fe3`).

- **comets-cache-staleness** *(medium / data-freshness)*: VidIQ MCP tools are only callable inside Claude Code sessions, so `pipeline/fetch/youtube_outliers.py` reads from a disk-backed cache (`data/youtube_outliers.json`) that an operator must manually refresh. If no one runs the refresh, the Comets route silently goes stale. Open: weekly reminder vs external scheduler vs surfacing cache age on the page. Evidence: `pipeline/fetch/youtube_outliers.py` docstring.

- **paid-api-cost-unobserved-post-wave-5-6** *(medium / cost)*: Wave 5 (Perplexity Sonar) and Wave 6 (xAI Grok X Search) both fold into `--max-cost-cents 50` but the actual daily spend with both enrichers live hasn't been observed end-to-end yet. Target: <50¢/day total. If overshooting, tighten `top_k`, narrow query windows, or rate-limit. Evidence: `pipeline/fetch/perplexity.py` and `pipeline/fetch/grok.py` cost-tracking is present but no daily report aggregates them yet.

- **grok-cost-conversion-recurrence-risk** *(low / regression-prone)*: `pipeline/fetch/grok.py` reports cost in xAI's `usage.cost_in_usd_ticks` (100M ticks = $1). First implementation was 100× over-reporting, fixed in `0fd805d fix(grok): correct ticks→cents conversion`. Whenever xAI changes their cost unit (likely), this is the failure point. Evidence: commit `0fd805d` and the unit-test that locks the math.

- **digg-firecrawl-token-dependency** *(low / fragile-external-dep)*: `pipeline/fetch/digg.py` accesses Digg via Firecrawl `/v2/scrape` because Digg has no API. Earlier in this session's history (2026-05-16-ish), the Firecrawl MCP token was throwing `Unauthorized: Invalid token`. If Firecrawl is down or the token expires, Digg signal goes to zero silently (the fetcher returns `[]`). Falls back to the cached `data/digg_ai_corpus.json` but the cache eventually staledates. Evidence: `pipeline/fetch/digg.py` docstring + earlier session notes.

- **comets-cache-no-staleness-surface** *(low / UX)*: When the Comets cache is older than N days, the dashboard doesn't visually indicate it. Operator may not notice they're looking at week-old YouTube outliers. Add an "as of {date}" stamp on the Comets route header. Evidence: `public/index.html` line 1911+ — `outliers-wrap` has no freshness marker.

## Resolved (recent)

- ✅ 2026-05-17 — **Predictions.jsonl legacy n-gram-era rows pruned** — 15 rows dropped (all `filed_at=2026-05-13`, all `verdict=pending`, all single-token n-gram garbage: `llm`, `ai`, `llms`, `model`, `agent`, `agents`, `hn`, `language`, `models`, `memory`, `training`, `introduce`, `work`, `ai-agents`, `claude`). New tool `scripts/prune_legacy_predictions.py` (commit `0a5a8ea`) is the reusable cleanup; data change committed separately as `cdd9657 chore(data)`. No `verified` / `verified_early` / `wrong` / `tracking` rows touched.

- ✅ 2026-05-17 — **Topic carry-over measurement tool added** — `scripts/topic_carryover.py` (commit `96c3fe3`) walks `public/snapshots/` and emits the day-over-day overlap ratio + PASS / FAIL / NEEDS-MORE-DATA verdict. The carry-over-unverified risk above is still open until 2026-05-20 produces 3 post-bias pairs, but the tooling to measure is no longer the blocker.

- ✅ 2026-05-17 — **Audit/roadmap doc marked closed** — added a "Status: CLOSED — historical snapshot" banner to `docs/AUDIT_AND_ROADMAP.md` pointing readers to `CLAUDE.md` section D and `memory/decisions.md` for current state. Doc preserved unchanged below the banner.

- ✅ 2026-05-17 — **Star Log keyword-mismatch regression** — fixed via embedding-cosine fuzzy verdict lookup in `predict.py`. PR #9 (commit `34802e1`). Star Log went 9 → 0 on the morning run; fix shipped end-to-end same day.

- ✅ 2026-05-17 — **Empty demand wedge (`demand_clusters: 0`)** — root cause was `sources=["inferred"]` failing Pydantic validation inside `try/except: continue`. Fixed by adding `"inferred"` to the SourceName Literal (commit `fbba547`). Earlier same-day workaround (commit `75a8fe4`) had tagged synthesized clusters as `"hackernews"` — replaced with the clean enum extension.

- ✅ 2026-05-17 — **Summarize dropping list-shaped Claude responses** — single-element-only unwrap in summarize batch parser. Commit `5fc2604`.

- ✅ 2026-05-17 — **Lint failing silently on `main`** — `lint` and `test` now required checks via branch protection (`enforce_admins: false`). Baseline bumped 24 → 27 (`e1142d5`).

- ✅ 2026-05-17 — **xAI Grok cost reporting 100× over** — ticks→cents conversion fix in `0fd805d`.

- ✅ 2026-05-15 — **Wave 4 hardening residuals** — escapeHtml XSS unit-tested; `.batch_state.json` cached across GH Actions runs. Both shipped via PR #1's integration branch (commits `3f3fca2`, `d78eba9`).

- ✅ 2026-05-15 — **Daily snapshot shipping placeholder content** — `.github/workflows/daily-snapshot.yml` flipped to `--claude --max-cost-cents 50`. Commits `5609f53`, `672fef6`.

- ✅ 2026-05-14 — **Garbage n-gram keywords** (`llms`, `ai`, `hn`, etc.) — topic primitive switched to Claude Haiku extraction. Commit `b1622b0`.
