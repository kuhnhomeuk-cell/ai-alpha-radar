# AI Alpha Radar — Audit & Roadmap

**Date:** 2026-05-14
**Method:** 10-agent fan-out (6 in-repo audits, 1 runtime stress test, 3 external research) → Opus 4.7 synthesis
**Working tree:** `awesome-solomon-4781e8`

---

## TL;DR — The Honest State of the Project

The architecture is right. The math is mostly right. **The current public-facing output is a Potemkin dashboard.** A runtime probe found that today's shipped `public/data.json` (2026-05-13) has:

- **2 of 4 sources dead** (GitHub + Semantic Scholar both `ok: false`)
- **All 30 trends with `velocity_score = 0.0`** and **all 30 in `lifecycle_stage = "whisper"`** — no differentiation
- **All Claude-generated fields placeholdered** (`"(awaiting Claude enrichment)"`) because the CI workflow defaults to `use_claude=False`
- **Predictions drift** (15 rows on disk, 13 in the snapshot)
- **Only one snapshot in `public/snapshots/`** — velocity math is structurally impossible until ≥2 snapshots exist, but `read_prior_snapshot()` is orphaned anyway (defined, never called)

Tests pass (117/117), code imports cleanly, the schema is contract-compliant. The pipeline is *technically working* and *practically empty*. **Fix that before anything else.**

---

## AUDIT — Cross-cutting findings

### Theme 1 — The Big Lie (advertised ≠ implemented)

The data contract promises more than the code delivers:

| Advertised | Reality | Evidence |
|---|---|---|
| `reddit_mentions_7d`, `x_posts_7d`, `youtube_videos_7d` | Hardcoded `0`. No fetchers exist. | `models.py:33-35`, `run.py:103` |
| `velocity_score`, `velocity_acceleration` | Always `0.0` because `read_prior_snapshot()` exists but is never called from `run.py` | `snapshot.py:40-47` orphaned |
| `sparkline_14d` | Hardcoded `[]` even on day 14+ | `run.py:197` |
| Mann-Kendall trend test | Defined at `score.py:191-201`, never called | — |
| `builder_signal` (per PLAN.md `normalize(repos + stars)`) | Formula undefined in code; runtime value is `github_mentions / max_github` heuristic | `run.py:320-325` |
| Claude `summary`/`angles`/`risk` in daily snapshot | Default CI run is `use_claude=False`; production `data.json` ships placeholders | `.github/workflows/daily-snapshot.yml:45` |
| Semantic Scholar source | Imported in `run.py:33`, never called | Ruff-flagged dead import |

**This is the single biggest credibility risk.** The frontend shows fields users expect to be real. They aren't.

### Theme 2 — Cold-start blindness (the early-detection thesis fails on day-1)

The product is "catch trends before they pop." Day-1 of a brand-new trend is exactly when the system should fire. Today, day-1 produces:

- `velocity_score = 0` (no prior snapshot to diff against)
- `velocity_acceleration = 0` (same reason)
- `saturation` via percentile rank that's non-stationary across days
- `convergence` window collapses to ~0h on day-1 → it's just an "≥3 sources present today" check, not causality
- `lifecycle_stage` defaults to `whisper` for everything

The thesis runs on one wheel. The fix is **Beta-Binomial smoothing with an empirical prior** to kill single-mention false positives, plus **Kleinberg burst detection** to give a real velocity signal even on sparse early-window data.

### Theme 3 — LLM outputs are uncalibrated

`summary_confidence`, `peak_estimate_days`, `risk_flag` are all **LLM self-ratings without evidence-grounding**:

- `summary_confidence: "high"` can fire on a trend with `arxiv_papers_7d=1, hn_posts_7d=0`
- `peak_estimate_days` is asked of Haiku with no horizon, no historical anchor, no validation
- All 16 currently-seeded predictions target the same day (`filed_at + 30`), zero have resolved → **zero calibration history**, despite `scripts/backtest_predictions.py` existing

**Brier score + reliability diagrams** are the standard fix. Heuristic-clamp `summary_confidence` to `total_signal_count` thresholds before the LLM ever sees it.

### Theme 4 — Cluster IDs reshuffle every day

UMAP is stochastic without a seed; HDBSCAN has no `random_state` argument at all in `cluster.py:90-94`. There's no canonical mapping in `snapshot.py` between yesterday's `cluster_id=3` and today's `cluster_id=3`. The frontend's watchlist, deep-links, and prediction-tracking all depend on stable identity.

Fix: seed HDBSCAN input order + canonicalize cluster IDs by hash of cluster_label + nearest-centroid matching across snapshots.

### Theme 5 — Silent failures everywhere

- **Fetcher fail → empty array → zero signal** indistinguishable from "no real activity." Today's GitHub+S2 failures shipped a snapshot anyway.
- **No retry/backoff on any fetcher** — a single transient 503 = "no data today."
- **`demand.py:140` swallows the Anthropic exception** — frontend shows "no demand clusters" identically to "Claude call failed."
- **No `if: failure()` handler in `daily-snapshot.yml`** — stale `data.json` from yesterday silently keeps serving forever if today's run dies. No staleness badge in frontend.
- **Predictions on-disk count drifts from snapshot count** (15 vs 13). No reconciliation check.
- **`snapshot.py:32-34` `Snapshot.model_validate_json()`** has no try/catch — one corrupt prior snapshot crashes the chain.

### Theme 6 — Worker security & cost

- **`RADAR_KV` namespace is placeholder** in `wrangler.toml:8-11` → `/api/deep-dive` will crash on the spend-tracking read in production
- **`/api/deep-dive` has no request-body size limit, no input-token estimate** → an attacker with any `*.github.io` page can drain the 30¢/day cap with 1-2 oversized POSTs. Cap resets at UTC midnight so it's drainable indefinitely.
- **`/proxy/arxiv` and `/proxy/s2` have no rate limits**
- **Batch retries duplicate spend** — re-running after a partial failure re-submits the whole Batch API call (~$0.15 per retry, no orphan tracking)
- **No request sanitization in error handlers** — `run.py:268-289` bare `print(f"... {e}")` could leak partial keys if SDK exceptions echo them

### Theme 7 — Source mix is wrong for the niche

The current 4 sources (arxiv, GH, HN, S2) are biased toward **research+infrastructure trends**. The product's stated niche is **"AI tools for solo creators."** Gap-scan:

| High-leverage missing source | Lead time vs current | Effort |
|---|---|---|
| Hugging Face Hub trending (models/spaces/datasets) | 1-3 weeks ahead of GH/arxiv | XS — no auth, ~40 LoC |
| Reddit (`r/LocalLLaMA`, `r/comfyui`, `r/aivideo`, `r/Saas`) | 5-10 days ahead of HN | S — PRAW, free non-commercial |
| Bluesky Jetstream firehose | The X replacement for AI research | S — free, unlimited |
| Product Hunt GraphQL | 1-2 days ahead of HN for creator-tool launches | S |
| Replicate trending (run-count delta) | 1-2 weeks (model → tool gap) | XS |
| **Niche AI-newsletter RSS shortlist** (Ben's Bites, Latent Space, Future Tools, Creator Science…) | 1-2 weeks ahead of HN for creator-niche tools | S — pure RSS |

**Avoid:** X direct API (cost), TikTok/Reels (ToS hostile), Pushshift (dead 2023), Papers with Code (dead July 2025).

### Theme 8 — Where AAR already wins

Competitive scan surfaced two real moats:

1. **Explicit lifecycle stages** (`idea/builder/...`) — every competitor either hides this or buckets it 3-ways. None target creators.
2. **Multi-source convergence with first-appearance timestamps** — "arxiv → GH → HN with measurable lead/lag" is genuinely differentiated. Nobody else surfaces it.

**Contrarian take:** the category gravity pulls upmarket because B2B pays. AAR's solo-creator niche is a deliberate gap. The win condition is *shortest path from trend → published video*, not "more accurate detection." That should govern feature priority.

---

## ROADMAP — Prioritized waves

Three waves of feature work, plus a parallel hardening wave. Wave 1 fixes the credibility crater. Wave 2 restores the thesis. Wave 3 adds leverage. Wave 4 is production hardening.

### WAVE 1 — Restore truthfulness (P0, ship this week)

**Goal: stop lying through the data contract.** Until this is done, no new features.

| # | Item | Effort | Files |
|---|---|---|---|
| 1.1 | Wire `read_prior_snapshot()` into `run.py` so velocity/acceleration/sparkline actually compute on day 2+ | S | `snapshot.py:40`, `run.py:161-162,197` |
| 1.2 | Flip CI to `use_claude=True` OR clearly badge placeholder content in the frontend | S | `.github/workflows/daily-snapshot.yml:45` |
| 1.3 | Wire `semantic_scholar.enrich_papers()` into the orchestrator or remove the field from the contract | S | `run.py:33`, 263 |
| 1.4 | Either implement Reddit/X/YouTube fetchers OR remove their fields from the schema | M (impl) or S (remove) | `models.py:33-35`, `DATA_CONTRACT.md` |
| 1.5 | Fix GH+S2 fetch failures + add minimum-source assertion | S | `run.py:262-290` |
| 1.6 | Add `if: failure()` handler + frontend staleness badge | S | `.github/workflows/daily-snapshot.yml`, `public/index.html:2082` |
| 1.7 | Configure `RADAR_KV` namespace in `wrangler.toml` | XS | `worker/wrangler.toml:8-11` |
| 1.8 | Reconcile predictions.jsonl drift (15 on disk vs 13 in snapshot) | XS | `predict.py:41`, `run.py:350` |

### WAVE 2 — Restore the early-trend thesis (P1, next 2 weeks)

**Goal: catch trends on day 1, not day 8.**

| # | Item | Effort |
|---|---|---|
| 2.1 | Beta-Binomial cold-start prior with empirical Bayes fit on prior 90 days | M |
| 2.2 | Kleinberg burst detector as a column alongside velocity | S-M |
| 2.3 | PELT changepoint detection (`ruptures`) replaces hardcoded `velocity_acceleration` | S |
| 2.4 | Calibrate `summary_confidence` against evidence count *before* the LLM call | S |
| 2.5 | Constrain `peak_estimate_days` to per-lifecycle horizon ranges | S |
| 2.6 | Seed HDBSCAN + canonicalize cluster IDs across snapshots | M |
| 2.7 | Retry+backoff wrapper in `pipeline/fetch/__init__.py` | S |
| 2.8 | Chunk S2 batch by 500 instead of raising ValueError | XS |
| 2.9 | Activate Mann-Kendall (defined but never called) | XS |

### WAVE 3 — Add leverage (P2, weeks 3-6)

**Goal: niche-specific signal + creator-grade UX.**

#### 3a — Sources

| # | Source | Effort | Lead time |
|---|---|---|---|
| 3.1 | Hugging Face Hub trending fetcher | XS | 1-3 weeks ahead of GH/arxiv |
| 3.2 | Niche AI-newsletter RSS shortlist | S | 1-2 weeks ahead of HN for creator-niche |
| 3.3 | Reddit fetcher (narrow subreddit shortlist via PRAW) | S | 5-10 days ahead of HN |
| 3.4 | Product Hunt GraphQL | S | Coincident-to-lead vs HN for creator tools |
| 3.5 | Replicate trending models (run-count delta) | XS | 1-2 weeks |
| 3.6 | Bluesky Jetstream firehose | S | X replacement for AI research |

#### 3b — Algorithms

| # | Item | Effort |
|---|---|---|
| 3.7 | Reciprocal Rank Fusion replaces hand-weighted composite | S |
| 3.8 | Granger / Transfer-Entropy lead-lag detection | M |
| 3.9 | Brier score + reliability diagrams for prediction calibration | M |
| 3.10 | Diachronic embedding novelty score | M |

#### 3c — UX

| # | Feature | Effort |
|---|---|---|
| 3.11 | Sparkline + absolute volume on every card | S |
| 3.12 | Channel-mix breakdown ("60% Reddit, 25% HN, 15% arxiv") | S |
| 3.13 | Meta-Trends parent narrative clustering | M |
| 3.14 | Question mining from Reddit/HN comments | M |
| 3.15 | Forecast curve with confidence band | S |

### WAVE 4 — Production hardening (P2, parallelizable)

| # | Item | Effort |
|---|---|---|
| 4.1 | Request-body size cap + token estimate on worker `/api/deep-dive` | S |
| 4.2 | Batch job ID tracking in KV → idempotent retries | M |
| 4.3 | Structured stderr logging across `run.py`, `demand.py`, `summarize.py` | M |
| 4.4 | `try/except` around `Snapshot.model_validate_json()` | XS |
| 4.5 | Real HTTP-layer tests for fetchers | M |
| 4.6 | Refactor `public/index.html` to forbid `.innerHTML` for LLM data | M |

---

# IMPLEMENTATION PLAN

Per-item: **files touched**, **key change**, **tests**, **commit msg**, **effort**, **deps**. Sized for per-task Conventional Commits with multi-line bodies. TDD-friendly — tests listed first where they should drive implementation.

## Conventions

- Effort: XS (<2h) · S (2-4h) · M (1 day) · L (2-3 days)
- Files use repo-relative paths
- "Dep: X.Y" means waits on item X.Y landing first
- Where a step touches `summarize.py`'s paid Claude path, **§5 rule applies**: render ONE card sync + visual inspect before flipping the bulk path

---

## WAVE 1 — Restore truthfulness (~5 dev days)

**Definition of done:** `data.json` produced by the daily workflow has populated `velocity_score`, `sparkline_14d`, real Claude content; lifecycle distribution spans ≥2 stages; failure mode produces a frontend staleness badge rather than a silent stale serve.

### 1.1 — Wire `read_prior_snapshot()` into `run.py`

The single highest-leverage fix. Unblocks 1.4, 2.1, 2.2, 2.3, 2.6, 2.9, 3.11, 3.15.

- **Files:** `pipeline/run.py` (around line 161-197), `pipeline/snapshot.py:40`
- **Change:**
  1. At top of `main()`: `prior = snapshot.read_prior_snapshot(public_dir, today_date)` and load `today_date - 7d` snapshot if present
  2. Build `prior_trends_by_keyword = {t.keyword: t for t in (prior.trends if prior else [])}`
  3. Replace `velocity_score=0.0` with `score.velocity(today_count, prior_count_30d)`
  4. Replace `sparkline_14d=[]` with `_compose_sparkline(public_dir / "snapshots", keyword, days=14)` — walks the 14 prior snapshot files
  5. For corrupt/missing prior: wrap in try/except, default to 0 + emit `meta.cold_start: true`
- **Tests:** `tests/test_run_with_history.py` — fixture with 14d of snapshots → assert non-zero velocity, populated sparkline, acceleration ≠ 0
- **Commit:** `feat(pipeline): wire read_prior_snapshot for velocity/acceleration/sparkline`
- **Effort:** M

### 1.2 — Enable Claude enrichment in CI (with §5 gate first)

- **Files:** `.github/workflows/daily-snapshot.yml:45`
- **Pre-step (§5):** run `poetry run python scripts/inspect_one_card.py --keyword "<top trend>"` locally; visually inspect the rendered card before flipping CI
- **Change:** `poetry run python -m pipeline.run` → `poetry run python -m pipeline.run --claude --max-cost-cents 50`
- **Add to `run.py`:** `--max-cost-cents` CLI arg that aborts the batch if estimated cost exceeds the cap (consult `summarize.py` token estimates)
- **Tests:** integration — `test_run_aborts_over_budget` mocks cost estimator returning > cap → assert abort
- **Commit:** `chore(ci): enable Claude enrichment in daily snapshot with cost cap`
- **Effort:** S
- **Dep:** §5 pre-step

### 1.3 — Wire Semantic Scholar into orchestrator

- **Files:** `pipeline/run.py:33` (kill dead import → real use), `pipeline/run.py:262-290`, `pipeline/fetch/semantic_scholar.py` (combine with 2.8)
- **Change:**
  1. After arxiv fetch: `arxiv_ids = [p.arxiv_id for p in papers if p.arxiv_id]`
  2. `s2_data = semantic_scholar.enrich_papers(arxiv_ids, api_key=os.environ.get("SEMANTIC_SCHOLAR_KEY"))`
  3. `fetch_health["semantic_scholar"] = True if s2_data else False`
  4. In `_build_source_counts()`, sum `s2_data[arxiv_id].citation_count_7d` across papers tagged with the term
- **Tests:** `test_run_and_snapshot.py` — extend smoke fixture with S2 stub; assert `meta.sources.semantic_scholar.ok == True`
- **Commit:** `feat(pipeline): activate semantic scholar enrichment`
- **Effort:** S

### 1.4 — Remove unimplemented multi-source fields from schema

Choose the honest minimum: drop `youtube_videos_7d`, `reddit_mentions_7d`, `x_posts_7d` until 3.1-3.6 ship them.

- **Files:** `pipeline/models.py:33-35`, `pipeline/run.py:103`, `docs/DATA_CONTRACT.md`, `scripts/gen_contract.py`, `public/index.html` (anywhere reading these fields)
- **Change:** delete fields from `SourceCounts`, regenerate contract via `python scripts/gen_contract.py`
- **Tests:** `test_models.py` — `test_source_counts_minimum_fields` updated to assert removal
- **Commit:** `feat(schema)!: remove unimplemented multi-source fields` (note the `!` for breaking)
- **Effort:** S
- **Note:** This is a breaking change. Mention coordination in the body of the commit.

### 1.5 — Minimum-source assertion + fix today's GH+S2 failures

- **Files:** `pipeline/run.py:262-290`, `.env.local` (check `GH_PAT`)
- **Diagnose GH:** Run pipeline locally with `GH_PAT` set; capture exception. Likely PAT-scope or expiration. Refresh PAT with `repo, read:org`.
- **Change in run.py** (just before `snapshot.write_snapshot`):
  ```python
  ok_sources = sum(1 for h in fetch_health.values() if h)
  if ok_sources < MIN_OK_SOURCES:  # constant = 3
      print(f"FATAL: {ok_sources}/4 sources ok; aborting", file=sys.stderr)
      sys.exit(2)
  ```
- **Tests:** `test_run_aborts_on_multi_source_failure` — patch fetchers to fail; assert SystemExit(2) and no write to `public/data.json`
- **Commit:** `feat(pipeline): abort snapshot write when ≥2 sources fail`
- **Effort:** S

### 1.6 — Staleness handling (failure marker + frontend badge)

- **Files:** `.github/workflows/daily-snapshot.yml` (add `if: failure()` step), `pipeline/snapshot.py` (write `data_freshness_status`), `pipeline/models.py` (`Snapshot.data_freshness_status: Literal["live","stale","error"] = "live"`), `public/index.html` (banner near `snapshot_date` render at line ~2082)
- **Change in workflow:**
  ```yaml
  - name: Mark stale on failure
    if: failure()
    run: |
      echo "{\"failed_at\":\"$(date -Iseconds)\"}" > public/.stale
      git add public/.stale && git commit -m "chore: mark stale" && git push
  ```
- **Frontend logic:** on load, if `(now - snapshot_date) > 24h` OR `data_freshness_status !== "live"` OR `.stale` exists → render warning banner
- **Tests:** `test_models.py` — Snapshot serializes with new field default; frontend snapshot test if you have one (else manual)
- **Commit:** `feat(reliability): staleness detection and frontend badge`
- **Effort:** S

### 1.7 — Configure Cloudflare RADAR_KV namespace

- **Files:** `worker/wrangler.toml:8-11`
- **Steps (manual):**
  ```bash
  cd worker
  npx wrangler kv:namespace create RADAR_KV
  npx wrangler kv:namespace create RADAR_KV --preview
  # paste the two returned IDs into wrangler.toml
  npx wrangler deploy
  curl https://<worker>.workers.dev/api/spend?date=2026-05-14
  ```
- **Tests:** manual smoke (verify 200 response from /api/spend endpoint)
- **Commit:** `chore(worker): configure RADAR_KV namespace bindings`
- **Effort:** XS

### 1.8 — Reconcile predictions.jsonl drift

- **Files:** `pipeline/predict.py:41`, `pipeline/run.py:350-356,386`
- **Investigate:** the 15-vs-13 gap. Possibilities: parse error on 2 rows; the `[-90:]` slice happens to drop 2 (unlikely with N=15); a write-after-read race.
- **Change:** in `load_predictions`, after parse, `print(f"loaded {len(preds)} predictions", file=sys.stderr)`. Add post-`update_all_verdicts` assertion: `assert len(updated) == len(past_predictions), f"prediction count drift: {len(updated)} vs {len(past_predictions)}"`
- **Tests:** `test_predictions_load_consistency` — write file with mixed valid/invalid lines, assert exception or recovery
- **Commit:** `fix(predict): assert predictions count consistency`
- **Effort:** XS

**Wave 1 PR strategy:** 8 commits, sequence `1.1 → 1.3 → 1.5 → 1.6 → 1.4 → 1.7 → 1.2 → 1.8`. Reasoning: 1.1 unblocks math; 1.3 fills the 3rd source so 1.5's minimum-source check has slack; 1.5+1.6 give safe-failure semantics before 1.2 turns on paid Claude; 1.4 is a breaking schema change best done before external eyes hit the data; 1.7 is independent.

---

## WAVE 2 — Restore the early-trend thesis (~8 dev days)

**Definition of done:** day-1 of a new trend produces a defensible signal (not zero); LLM confidence + peak estimates are evidence-grounded; cluster IDs survive across days; fetchers don't silently lose data on transient errors.

### 2.1 — Beta-Binomial cold-start prior

- **Files:** new `pipeline/cold_start.py`, `pipeline/score.py` (velocity call site)
- **Change:**
  - `compute_empirical_prior(historical_counts: list[int]) -> tuple[float, float]` — method-of-moments fit of Beta(α, β) on prior 90 days of daily new-term counts
  - `smoothed_rate(today_count: int, alpha: float, beta: float, n_days: int) -> float` — posterior mean
  - In `score.velocity()`: when `today_count < 3`, use `smoothed_rate` instead of raw
- **Tests:** `test_cold_start.py` — known prior (α=2, β=10); single-mention term posterior is much smaller than naive
- **Ref:** [BayesCNS paper](https://arxiv.org/html/2410.02126)
- **Commit:** `feat(score): beta-binomial smoothing for cold-start terms`
- **Effort:** M
- **Dep:** 1.1

### 2.2 — Kleinberg burst detector

- **Files:** new `pipeline/burst.py`, `pipeline/models.py` (add `burst_score: float`), `pipeline/score.py`, `DATA_CONTRACT.md`
- **Change:** port [nmarinsek/burst_detection](https://github.com/nmarinsek/burst_detection) (single file, ~120 LoC). Run it on the 30d daily-count series per term. Surface `burst_score` (max state weight) alongside `velocity_score`.
- **Tests:** synthetic step+spike series → burst detected with expected start/end indices
- **Commit:** `feat(score): kleinberg burst detector as velocity augment`
- **Effort:** S-M
- **Dep:** 1.1

### 2.3 — PELT changepoint → velocity_acceleration

- **Files:** new `pipeline/changepoint.py`, `pipeline/score.py`, `pyproject.toml` (add `ruptures`)
- **Change:** wrap `ruptures.Pelt(model="rbf").fit(sparkline_14d).predict(pen=3)` → returns breakpoint indices. Define `velocity_acceleration = (today_count - count_at_last_breakpoint) / days_since_breakpoint`
- **Tests:** known step function fixture → correct breakpoint location ±1 day
- **Commit:** `feat(score): pelt changepoint detection replaces hardcoded acceleration`
- **Effort:** S
- **Dep:** 1.1

### 2.4 — Pre-LLM calibration of summary_confidence

- **Files:** `pipeline/summarize.py:94` (Prompt A), `pipeline/summarize.py:200-210` (post-processor)
- **Change:**
  - Compute `total_signal = arxiv_papers_7d + github_repos_7d + hn_posts_7d + s2_citations_7d`
  - Pre-LLM: inject into prompt: `"You MAY only return confidence='high' if total_signal>10 AND convergence_detected. If total_signal<3, return 'low'."`
  - Post-LLM: hard clamp — `if total_signal < 3 and llm_confidence != "low": llm_confidence = "low"` (and log the override)
- **Tests:** `test_summarize_clamps_confidence` — mock LLM returning "high" with total_signal=1 → output is "low"
- **Commit:** `feat(summarize): calibrate summary_confidence against signal evidence`
- **Effort:** S

### 2.5 — Constrain peak_estimate_days to lifecycle-horizon table

- **Files:** `pipeline/summarize.py:117-120` (Prompt C), post-processor
- **Lookup table** (`pipeline/lifecycle_horizons.py`):
  ```python
  HORIZONS = {"whisper": (14, 30), "builder": (30, 60), "creator": (21, 45), "hype": (7, 21), "commodity": (None, None)}
  ```
- **Change in Prompt C:** "peak_estimate_days MUST be within [{min}, {max}] for stage '{stage}'. If you cannot estimate, return null."
- **Post-LLM clamp:** if out of range, clamp to nearest bound + log override
- **Tests:** mock LLM returning 200 for "whisper" → clamped to 30
- **Commit:** `feat(summarize): clamp peak_estimate_days to lifecycle horizon ranges`
- **Effort:** S

### 2.6 — Stable cluster IDs across snapshots

- **Files:** `pipeline/cluster.py:75-94` (seeding), new `pipeline/cluster_identity.py`, `pipeline/snapshot.py` (persist centroids)
- **Change:**
  - Sort `canonical_forms` before embedding (deterministic input order)
  - Add `umap.UMAP(..., random_state=42, n_jobs=1)` already done per A3 — verify
  - HDBSCAN doesn't expose `random_state` but is deterministic given fixed input order → confirm
  - New `canonicalize_cluster_id(label: str, prior_centroids: dict[int, np.ndarray], new_centroid: np.ndarray) -> int`:
    - Compute cosine distance to each prior centroid
    - If `min_distance < 0.2`: reuse prior cluster_id
    - Else: hash(label) → new stable int
  - Persist `cluster_centroids: dict[int, list[float]]` in `Snapshot` model
- **Tests:** 2-day fixture run → assert ≥80% of cluster_ids are identical day-to-day for the same labels
- **Commit:** `feat(cluster): stable cluster IDs via centroid matching across snapshots`
- **Effort:** M
- **Dep:** 1.1

### 2.7 — Retry+backoff wrapper for fetchers

- **Files:** new `pipeline/fetch/_retry.py`, apply to all 4 fetchers + S2 enricher
- **Change:**
  ```python
  @with_retry(attempts=3, base_delay=1.0, retry_on={429, 500, 502, 503, 504})
  def fetch_recent_papers(...): ...
  ```
  Parse `Retry-After` header; jittered exponential backoff; final failure raises with logged context (no silent empty list)
- **Tests:** mocked `respx` — transient 503 → succeeds on retry; persistent → exhausts and raises; 429 with Retry-After → respects header
- **Commit:** `feat(fetch): exponential backoff retry wrapper for all fetchers`
- **Effort:** S
- **Dep used by:** 4.5

### 2.8 — Chunk S2 batch by 500

- **Files:** `pipeline/fetch/semantic_scholar.py:73-104`
- **Change:** replace ValueError on >500 with:
  ```python
  results = {}
  for chunk in chunked(arxiv_ids, 500):
      results.update(_post_batch(chunk, api_key))
      time.sleep(S2_REQUEST_INTERVAL_SECONDS)
  return results
  ```
- **Tests:** 600 IDs → 2 batches, results merged
- **Commit:** `fix(fetch): chunk semantic_scholar batches to avoid hard cap`
- **Effort:** XS

### 2.9 — Activate Mann-Kendall

- **Files:** `pipeline/score.py:191-201`, `pipeline/models.py` (add `velocity_significance: float`), `DATA_CONTRACT.md`
- **Change:** call `mann_kendall(sparkline_14d)` → `velocity_significance = abs(z_score)`. Use as a gate in lifecycle classification: only mark "builder" if `velocity_significance > 1.96`
- **Tests:** monotonic series → significant; random → not
- **Commit:** `feat(score): wire mann-kendall significance into lifecycle gating`
- **Effort:** XS
- **Dep:** 1.1

---

## WAVE 3 — Add leverage (~12 dev days)

**Definition of done:** ≥3 new niche-aligned sources live; cluster output presents a parent-narrative ("Meta-Trends"); creator-relevant UX (sparkline, channel mix, question mining) ships.

### 3a — New data sources

#### 3.1 — Hugging Face Hub trending fetcher

- **Files:** new `pipeline/fetch/huggingface.py`, `pipeline/normalize.py`, `pipeline/models.py` (add `huggingface_likes_7d`, `huggingface_downloads_7d`, `huggingface_spaces_7d`), `DATA_CONTRACT.md`, `tests/fixtures/hf_sample.json`
- **Endpoints:** `GET huggingface.co/api/models?sort=trending&limit=100` (no auth) — same for `/datasets`, `/spaces`
- **Term extraction:** model ID prefix, top tags, pipeline_tag
- **Tests:** fixture parser tests (match existing pattern in `test_fetch_arxiv.py`)
- **Commit:** `feat(fetch): hugging face hub trending models/datasets/spaces`
- **Effort:** S

#### 3.2 — Newsletter RSS shortlist

- **Files:** new `pipeline/fetch/newsletters.py`, new `data/newsletters.json` (curated list), `pipeline/models.py` (add `NewsletterSignal` model), `DATA_CONTRACT.md`
- **Curated feeds (initial):** Ben's Bites, TLDR AI, Latent Space, Rundown AI, Creator Science, Future Tools, Bot Eat Brain, Interconnects, AI Daily Brief, Every — ~15 feeds
- **Logic:** feedparser → entries from past 14d → regex-extract URLs from HTML body → tally `(url, unique_newsletters_count, first_seen, last_seen)` → filter out URLs that are already in HN top-30 today
- **Surface:** new top-level `newsletter_signals: list[NewsletterSignal]` in Snapshot (also feed into normalize as candidate terms)
- **Tests:** fixture RSS files → known URL aggregation
- **Commit:** `feat(fetch): newsletter RSS cross-mention signal aggregator`
- **Effort:** S-M

#### 3.3 — Reddit fetcher (PRAW, narrow subreddit shortlist)

- **Files:** new `pipeline/fetch/reddit.py`, `pyproject.toml` (add `praw`), `.env.example` (add `REDDIT_*` vars), `data/reddit_subreddits.json`
- **Subreddits (initial):** r/LocalLLaMA, r/StableDiffusion, r/aivideo, r/ChatGPTPro, r/ClaudeAI, r/comfyui, r/Saas, r/MachineLearning, r/artificial
- **Auth:** Reddit script app (free tier, 60 RPM)
- **Logic:** top-of-week per sub; score = `(upvote_ratio * score * comment_count) / age_hours`; dedupe by post ID
- **Models:** re-add `reddit_mentions_7d` to `SourceCounts` (un-doing the removal in 1.4 once we have a fetcher); add `reddit_top_subreddit: str | None`
- **Tests:** fixture-based parser tests (mock PRAW client)
- **Commit:** `feat(fetch): reddit subreddit shortlist via praw`
- **Effort:** S-M

#### 3.4 — Product Hunt GraphQL v2

- **Files:** new `pipeline/fetch/producthunt.py`, `.env.example` (add `PRODUCT_HUNT_TOKEN`), `pipeline/models.py` (add `producthunt_launches_7d`)
- **Query:** GraphQL `posts(featured: true, postedAfter: <7d ago>) { name tagline votesCount topics }` filtered to AI/dev/productivity topics
- **Tests:** fixture GraphQL response → expected term extraction
- **Commit:** `feat(fetch): product hunt graphql trending launches`
- **Effort:** S

#### 3.5 — Replicate trending

- **Files:** new `pipeline/fetch/replicate.py`, `.env.example` (add `REPLICATE_API_KEY`), `pipeline/models.py`
- **Endpoint:** `GET /v1/models?search=...` paginated; persist `run_count` daily; compute 7d delta
- **Tests:** fixture
- **Commit:** `feat(fetch): replicate trending models run-count delta`
- **Effort:** XS

#### 3.6 — Bluesky Jetstream firehose

- **Files:** new `pipeline/fetch/bluesky.py`, `data/bluesky_handles.json`, `pyproject.toml` (add `websockets`)
- **Design choice:** Jetstream is a stream, not a request-response. Two options:
  - (a) Separate scheduled task accumulates mentions to a local SQLite; daily pipeline reads counts (recommended)
  - (b) Daily run subscribes for fixed duration (~5 min) and samples
- **Endpoint:** `wss://jetstream2.us-east.bsky.network/subscribe`
- **Filter:** `app.bsky.feed.post` records matching curated AI keywords OR from curated handle list
- **Tests:** mocked WebSocket fixture → expected mention counts
- **Commit:** `feat(fetch): bluesky jetstream firehose accumulator`
- **Effort:** M

### 3b — Algorithm upgrades

#### 3.7 — Reciprocal Rank Fusion (RRF)

- **Files:** new `pipeline/rrf.py`, `pipeline/score.py` (replace hand-weighted composite)
- **Formula:** for each term, for each source rank, `score = sum(1 / (k + rank))` with k=60 (standard)
- **Tests:** one source goes dark → ranking stays stable (regression test)
- **Commit:** `feat(score): reciprocal rank fusion replaces hand-weighted composite`
- **Effort:** S

#### 3.8 — Granger / Transfer-Entropy lead-lag gate

- **Files:** new `pipeline/leadlag.py`, `pipeline/models.py` (add `still_early_gate: bool`)
- **Method:** statsmodels `grangercausalitytests` on (arxiv_series, hn_series) and (hn_series, github_stars_series) — daily counts, first-differenced, max_lag=4
- **Surface:** for each term, if Granger says arxiv→HN with significance, compute `expected_hn_velocity` from the regression. Set `still_early_gate = True` if actual_hn_velocity < expected by 1+ stdev
- **Tests:** synthetic correlated series with known lag → gate fires correctly
- **Commit:** `feat(score): granger lead-lag gate for still-early signal`
- **Effort:** M
- **Dep:** 1.1 (needs sparkline history)

#### 3.9 — Brier score + reliability diagrams

- **Files:** `scripts/backtest_predictions.py`, new `public/reliability.png`
- **Change:** load `predictions.jsonl`, filter to resolved (verdict != "tracking"); compute Brier for binary verified-vs-wrong; plot reliability curve (deciles of predicted prob vs actual hit rate) via matplotlib → save PNG to public/
- **Meta:** write `prediction_calibration: {brier: float, n_resolved: int, last_computed: str}` into Snapshot meta
- **Tests:** known calibrated/miscalibrated mock → expected Brier
- **Commit:** `feat(backtest): brier score and reliability diagram for predictions`
- **Effort:** M

#### 3.10 — Diachronic embedding novelty

- **Files:** new `pipeline/novelty.py`, `pipeline/models.py` (add `novelty_score: float`)
- **Method:** maintain a rolling 60-day corpus embedding centroid (sentence-transformers all-MiniLM-L6-v2). For each new term, compute cosine distance from centroid. Higher distance = more novel.
- **Storage:** `data/corpus_centroid_60d.npy` updated each run
- **Tests:** stable corpus + alien term → high novelty score
- **Commit:** `feat(score): diachronic embedding novelty score`
- **Effort:** M

### 3c — UX (the trend→video shortest path)

#### 3.11 — Sparkline + absolute volume on every card

- **Files:** `public/index.html` (card render)
- **Change:** inline SVG sparkline from `sparkline_14d` (~20 LoC, no library); show `total_mentions_7d` + `vs_prior_week_pct`
- **Tests:** manual UI inspection; snapshot test if you have one
- **Commit:** `feat(ui): sparkline and absolute volume on trend cards`
- **Effort:** S
- **Dep:** 1.1

#### 3.12 — Channel-mix breakdown

- **Files:** `public/index.html`
- **Change:** for each trend, render stacked horizontal bar from `sources` field (e.g., "Reddit 60% · HN 25% · arxiv 15%"). Normalize by relative-mentions × source-weight so the bar is comparable across trends.
- **Commit:** `feat(ui): channel-mix breakdown per trend card`
- **Effort:** S

#### 3.13 — Meta-Trends parent narrative

- **Files:** new `pipeline/meta_trends.py`, `pipeline/run.py`, `pipeline/models.py` (add `meta_trend: str | None` to Trend), `pipeline/summarize.py` (Claude prompt for meta-trend label), `public/index.html` (group cards under meta-trend headers)
- **Method:** 2nd-pass HDBSCAN on cluster *centroids* (clusters of clusters), `min_cluster_size=2`. Label each meta-cluster with Claude (one call per meta-cluster: "Given these cluster labels, what's the parent narrative in 2-4 words?")
- **Tests:** fixture with known cluster centroids → expected meta-grouping
- **Commit:** `feat(meta-trends): parent narrative grouping over cluster centroids`
- **Effort:** M

#### 3.14 — Question mining (real hooks from comments)

- **Files:** new `pipeline/questions.py`, `pipeline/summarize.py` (replace synthetic `angles.tutorial`)
- **Method:** for each trend, pull associated HN + Reddit comments; regex-filter to question-shaped strings (`/^(how|what|why|can|should|when|is|does|do)\s.+\?/i`); embed; HDBSCAN-cluster; surface top 5 by frequency
- **Surface:** new field `top_questions: list[str]` on Trend
- **Update:** `angles.tutorial` becomes real-question seed instead of LLM-synthetic
- **Tests:** fixture comments → expected question clusters
- **Commit:** `feat(summarize): question mining replaces synthetic tutorial angles`
- **Effort:** M
- **Dep:** 3.3

#### 3.15 — Forecast curve with confidence band

- **Files:** `public/index.html`
- **Change:** extend the sparkline forward by `peak_estimate_days` days; shade confidence band using `prediction_calibration.brier` (wider band when calibration is worse)
- **Tests:** visual
- **Commit:** `feat(ui): forecast curve with calibrated confidence band`
- **Effort:** S
- **Dep:** 3.9, 3.11

---

## WAVE 4 — Production hardening (~5 dev days, parallelizable)

**Definition of done:** worker is not a free Claude proxy; batch retries don't double-spend; failures are observable; all error paths have tests.

### 4.1 — Worker request body cap + token estimate

- **Files:** `worker/src/index.ts:155-183`
- **Change:**
  ```typescript
  const MAX_BODY_BYTES = 5000;
  const contentLength = parseInt(request.headers.get('content-length') ?? '0', 10);
  if (contentLength > MAX_BODY_BYTES) return new Response('Payload too large', { status: 413 });
  const body = await request.json();
  const estimatedInputTokens = Math.ceil(JSON.stringify(body).length / 4);
  if (estimatedInputTokens > 200) return new Response('Request too large', { status: 413 });
  ```
- **Tests:** miniflare/wrangler dev integration test — oversize POST → 413
- **Commit:** `feat(worker): request size and token caps on /api/deep-dive`
- **Effort:** S

### 4.2 — Batch job idempotency via KV

- **Files:** `pipeline/summarize.py:243-259`, worker if KV is reachable from pipeline (or use a local file `.batch_state.json` if not)
- **Logic:**
  - Before submit: hash `(date, cards_set_hash)` → KV key `batch:<hash>`. If exists, poll that batch instead of resubmitting.
  - After submit: write batch_id with 24h TTL.
  - After completion: write results to KV (so a re-run of the same day reads cached results).
- **Tests:** mock KV; same-day re-run → no second submission
- **Commit:** `feat(summarize): idempotent batch submission via KV cache`
- **Effort:** M

### 4.3 — Structured logging

- **Files:** new `pipeline/log.py`, replace all `print(..., file=sys.stderr)` calls in `pipeline/run.py`, `pipeline/demand.py`, `pipeline/summarize.py`
- **API:** `log(event: str, level: str = "info", **fields)` → emits JSON line: `{"ts": "...", "event": "fetch_failed", "level": "warning", "source": "github", "error": "..."}`
- **Tests:** capture stderr, parse JSON, assert event fired
- **Commit:** `feat(observability): structured json logging`
- **Effort:** M (mechanical, many touch points)

### 4.4 — try/except around `Snapshot.model_validate_json`

- **Files:** `pipeline/snapshot.py:32-34`
- **Change:**
  ```python
  try:
      return Snapshot.model_validate_json(text)
  except (ValidationError, JSONDecodeError) as e:
      log("snapshot_parse_failed", path=str(path), error=str(e))
      return None
  ```
- **Tests:** corrupt JSON file → None returned (not exception)
- **Commit:** `fix(snapshot): graceful handling of corrupt prior snapshot`
- **Effort:** XS
- **Dep used by:** 1.1's edge case

### 4.5 — Real HTTP-layer tests for fetchers

- **Files:** `pyproject.toml` (add `respx` dev dep), `tests/test_fetch_*.py` (extend)
- **For each fetcher** (arxiv, github, hackernews, semantic_scholar, plus new ones):
  - Mock 200 → asserts parser correctly
  - Mock 429 with Retry-After → asserts retry honors header
  - Mock 500 → asserts retry exhaustion raises after N
  - Mock malformed body → asserts graceful empty return (after retries exhausted)
- **Commit:** `test(fetch): http-layer integration tests for all fetchers`
- **Effort:** M
- **Dep:** 2.7

### 4.6 — Refactor `.innerHTML` for LLM-sourced strings

- **Files:** `public/index.html` lines 1908, 2005, 2013, 2054, 2085, 2314, 2378, 2430
- **Change:** swap each `el.innerHTML = userContent` to `el.textContent = userContent`. Where HTML structure is needed (lists, tables), use `document.createElement` + `appendChild` rather than string concatenation.
- **Lint:** add a project-local script `scripts/lint_no_innerhtml.sh` that greps for `.innerHTML\s*=` in `public/index.html` and fails CI if present
- **Tests:** manual verification on cards; add CI lint
- **Commit:** `refactor(ui): replace innerHTML with textContent for llm-sourced data`
- **Effort:** M

---

## Cross-wave dependency graph

```
1.1 (read_prior_snapshot) ─────┬─→ 1.4 (sparkline decision)
                                ├─→ 2.1 (Beta-Binomial)
                                ├─→ 2.2 (Kleinberg)
                                ├─→ 2.3 (PELT)
                                ├─→ 2.6 (cluster IDs)
                                ├─→ 2.9 (Mann-Kendall)
                                ├─→ 3.11 (sparkline UI)
                                └─→ 3.8 (Granger)

1.3 (S2 wire) ──────────────────→ 2.8 (S2 chunking)  [natural pair]

2.7 (retry wrapper) ────────────→ 4.5 (HTTP tests)

3.3 (Reddit fetcher) ───────────→ 3.14 (question mining)

3.9 (Brier calibration) ────────→ 3.15 (confidence band)
3.11 ───────────────────────────→ 3.15
```

## Suggested execution order

| Week | Focus | Items |
|---|---|---|
| 1 | Wave 1 truthfulness | 1.1 → 1.3 → 1.5 → 1.6 → 1.4 → 1.7 → 1.2 → 1.8 |
| 2 | Wave 2 quick wins | 2.7, 2.8, 2.9, 2.4, 2.5 (independent, parallel) |
| 3 | Wave 2 deeper | 2.1, 2.2, 2.3, 2.6 (all need 1.1) |
| 4 | Wave 3a sources | 3.1, 3.5, 3.2 (quick), then 3.3 |
| 5 | Wave 3a continued + 3b | 3.4, 3.6, 3.7, 3.8 |
| 6 | Wave 3c UX + Wave 4 | 3.11, 3.12, 3.13, 3.14, 3.15; 4.1, 4.4 |
| 7 (buffer) | Wave 4 + polish | 4.2, 4.3, 4.5, 4.6, backtest 3.9, novelty 3.10 |

## Risks to watch

1. **1.1 has hidden complexity** — first time you read prior snapshots, you'll discover the schema has evolved silently (Pydantic optional fields added without migrations). Add a `schema_version` field on Snapshot now (set to 1) so future evolution is gracefully handled.
2. **2.6 cluster ID stability is harder than it looks.** UMAP+HDBSCAN can produce wildly different layouts day-to-day even with seeds, because the input *corpus* changes. Centroid distance threshold of 0.2 is a guess — be ready to tune empirically by running 2-day fixtures.
3. **3.6 Bluesky firehose is a stream, not a poll.** It will need a separate scheduled task or a sidecar process; don't try to cram it into the daily snapshot job.
4. **3.13 Meta-Trends quality depends on enough clusters per day.** With 30 trends → 5-10 clusters, the meta-pass may not produce useful groupings until you scale up TOP_N_TRENDS.
5. **4.5 HTTP integration tests** can become flaky if mocks drift from real API responses. Re-record fixtures quarterly against the real APIs.

## What to do in the next session

Open a PR that bundles Wave 1 — items 1.1, 1.3, 1.5, 1.6 as the minimum-viable-honesty bundle. Hold 1.2 (Claude flip) for a second PR after the §5 manual eyeball pass. The rest of Wave 1 can land in a third PR.

---

**Methodology footnote:** 10 agents ran in parallel. A1-A5 + A10 were static `Explore` agents; A6 was `general-purpose` with Bash to run the test suite + inspect artifacts; A7-A9 were `general-purpose` with WebSearch/WebFetch. No paid Anthropic/OpenAI/xAI calls were issued by any agent. All findings cite `file:line` in the underlying reports.
