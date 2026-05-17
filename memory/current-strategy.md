---
name: current-strategy
description: Current strategy and now-state for ai-trends — edited weekly
type: project
---

*Last updated: 2026-05-17*

## Where we are

Production-pilot. Daily snapshot runs at 06:00 UTC, lands on Vercel, surfaces a defensible trend list with per-trend Claude enrichment, demand clusters, prediction tracking, Pain Points (Perplexity Sonar), and a Comets route (VidIQ YouTube outliers). 453 tests green. Branch-protected `main`. 9 PRs merged. 11 source fetchers live (arXiv, GitHub, Hacker News, Semantic Scholar, HuggingFace, Reddit, Bluesky, ProductHunt, Replicate, Newsletters, Digg) + 2 enrichment APIs (Perplexity Sonar, xAI Grok X Search).

## Win condition

**Shortest path from trend → published video.** Every feature is evaluated against "does this collapse the time from 'creator sees this trend' to 'creator has a script-ready angle'?" Not "more accurate detection" — every competitor claims that. The differentiator is creator-actionability:

- **Pain Points** (Wave 5 Perplexity) → real audience questions, not synthetic angles
- **Comets** (Wave 5 VidIQ outliers) → proven-format videos to bend onto a topic
- **Convergence-with-timestamps** → "arxiv → GH → HN" lead-lag a creator can act on before saturation
- **Lifecycle stages** (whisper / builder / creator / hype / commodity) → tells a creator when a topic is too cold or too saturated

## Niche lock

**AI tools for solo creators.** Explicit rejection of: AI adoption in medical / legal / finance / enterprise / general business. If a brainstorm thread reaches for a broader category, narrow it back or veto. The category gravity pulls upmarket because B2B pays — staying in the gap is a deliberate choice.

## What's actually shipped (Waves 1-6 closed)

**Wave 1 — Truthfulness:** `read_prior_snapshot()` wired in, Claude enrichment enabled in CI with cost gate, S2 fetcher activated, multi-source assertion (≥3 sources or abort), staleness badge, RADAR_KV configured, predictions drift reconciled.

**Wave 2 — Early-trend thesis:** Beta-Binomial cold-start, Kleinberg burst detector, PELT changepoint → velocity_acceleration, Mann-Kendall significance gating, calibrated `summary_confidence` (pre-LLM), lifecycle-clamped `peak_estimate_days`, retry+backoff fetcher wrapper, S2 batch chunking, stable cluster IDs via centroid matching.

**Wave 3 — Leverage:** 6 new fetchers (HF, Newsletters, Reddit-RSS, ProductHunt, Replicate, Bluesky). Reciprocal Rank Fusion. Granger lead-lag gate. Brier score + reliability diagrams. Diachronic novelty score. Meta-Trends 2nd-pass clustering. UX: sparklines, channel-mix, forecast band, question mining.

**Wave 4 — Hardening:** Worker request/token caps. Local-file batch idempotency (cached via `actions/cache`). Structured JSON logging. `try/except` around prior-snapshot validation. HTTP-layer tests across fetchers. escapeHtml XSS unit tests. innerHTML baseline-must-not-grow lint.

**Wave 5 — Trend → script accelerators:** Perplexity Sonar pain-point enrichment (pipeline-side, scheduled). YouTube outliers via VidIQ MCP (operator-scheduled, disk-backed cache). New "Comets" nav route.

**Wave 6 — X signal closure:** xAI Grok Search filling `x_posts_7d` (the longest-standing audit gap from v0.1.0).

**Bonus this week:**
- DiggAI cross-reference source (consensus boost when HN+arxiv+Digg align)
- Embedding-cosine fuzzy verdict lookup (Star Log resilience to topic paraphrasing)
- Yesterday-keyword bias on topic extraction (vocabulary stability)
- Demand cluster fallback chain (HN → trend synthesis when sparse)
- Reddit OAuth password-grant + Arctic Shift archive fallback
- 36+ UI bug fixes across GROUP A-J
- Header-density CSS pass (single-row nav + controls)

## Operating principles (project-scoped)

1. **PR-only integration** to `main`. `gh pr create`, never direct push. `lint` + `test` required.
2. **`--max-cost-cents` is the budget gate** — every paid call (Anthropic Haiku batch, Sonnet briefing, Perplexity Sonar, Grok X Search, Sonnet deep-dive via worker) reports cost back through `pipeline/run.py` so the gate can throttle.
3. **Data contract is `pipeline/models.py`** — `extra="forbid"`. Schema changes regenerate `docs/DATA_CONTRACT.md` via `scripts/gen_contract.py`.
4. **No silent enrichment failures** — Perplexity / Grok / Digg / VidIQ all degrade to empty results, never crash the snapshot. But log at `warning`, never `info`, so empty isn't invisible.
5. **Verify in the tree you act on** (inherits from global ops principles). Sub-agents pass target-tree paths and run `git ls-tree HEAD <path>` before claiming verified.
6. **Verify-the-deliverable means STOP, not advance** — earlier "yes" is scoped to one step. §5 rule applies to every paid bulk batch.

## What's next (open questions, not roadmap)

- **Empirical 3-day topic carry-over** — Task 4 (yesterday-keyword bias) signed off 2026-05-17, but the measurement requires 3 more daily snapshots. First measurable after 2026-05-20. If carry-over is still <50%, the fuzzy fallback in `predict.py` is doing more work than expected and the bias prompt needs sharpening.
- **Wave 5 production cost** — first daily run with Perplexity + Grok live needs cost-observed. Target: <50¢/day total. If higher, tighten `top_k` or rate-limit.
- **Comets cache freshness** — operator-scheduled means it goes stale if no one runs the refresh. Open: does this need a weekly reminder, or wire VidIQ into an external scheduler?
- **Outdated `docs/AUDIT_AND_ROADMAP.md`** — the 2026-05-14 fan-out audit is a historical snapshot. Waves 1-6 are all shipped. Future audits should be new documents, not updates to this one. Decide: archive it or annotate it with a "Status: closed" header.

## What we're not doing

- AI-adoption tracker for enterprises (niche lock)
- Multi-user dashboard with auth (single-creator product)
- Mobile-first redesign (desktop is the primary surface)
- Real-time streaming (daily snapshot cadence is the contract)
- More algorithm sophistication for its own sake — every new score must clear "would a creator change a decision based on this?"
