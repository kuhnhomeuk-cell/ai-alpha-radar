# AI Alpha Radar — Backend Build Spec

*For: a fresh implementation agent (Claude Code, Codex, etc.) building from zero.*
*Date: 2026-05-13 · Owner: Dean Kuhn · Project: `/Users/deankuhn/Desktop/AI Trends/`*

> **You (the implementing agent) have not seen this conversation.** Read this entire document before writing code. Every locked decision below was made deliberately — do not re-litigate them. Ask only the questions in §13.

---

## 1 · What you are building

A daily-refresh trend-intelligence backend that detects emerging AI topics **before they go mainstream**, then enriches each trend with creator-ready insight via Claude. Output is a single JSON file (`public/data.json`) committed to a public GitHub repo, served via GitHub Pages, consumed by a static HTML frontend.

**The frontend already has working mockups** at `mockup.html` and `mockup-b.html` — your job is to make their fake data real.

**The product wedge** (two ideas, both must be implemented):

1. **Cross-signal convergence detection** — when the same concept appears in ≥3 leading sources within a 72-hour window, flag it as a leading indicator. Mainstream typically follows 2–4 weeks later.
2. **Demand Clusters** — mine comments/replies across YouTube, Reddit, X, HN. Cluster them with Claude into question-shapes ("How do I run X on a Y?"). Each cluster surfaces: asker count, top quotes, source mix, open-window estimate, creator brief.

The backend has TWO output streams:
- The **trend cards** (classic trend-tracking: velocity, saturation, lifecycle stage, Claude-written angles)
- The **demand clusters** (the unique unmet-demand layer)

---

## 2 · Locked decisions — do not change

| Decision | Locked value | Why |
|---|---|---|
| Architecture | Static HTML frontend + serverless proxy + daily-cron pipeline | Zero ops, $0 hosting, demo-friendly |
| Pipeline runner | **GitHub Actions cron** (06:00 UTC daily) | Free 2,000 min/mo, logs in repo |
| Pipeline language | **Python 3.12** | Best client libs for arXiv/HN/GitHub |
| CORS + AI proxy | **Single Cloudflare Worker** | Free 100k req/day, holds API keys as secrets |
| Storage | **JSON files in GitHub repo** | `public/data.json` (current) + `public/snapshots/YYYY-MM-DD.json` (history). Git = free versioning. |
| Hosting | **GitHub Pages** | Free, auto-deploys on commit |
| AI model (cards) | **claude-haiku-4-5** via Anthropic Batch API | ~$1.50/mo at 75 trends/day |
| AI model (briefing/deep-dive) | **claude-sonnet-4-6** | Once daily + on-demand only |
| X/Twitter data | **xAI Grok API** (Grok has native X grounding) | Solves the $200/mo Twitter API problem |
| YouTube + IG data | **VidIQ MCP** (already wired into the user's Claude Code) | Unfair advantage; competitors won't have this |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | 80MB, free, runs inside GH Action |
| Clustering | UMAP → HDBSCAN, precomputed nightly | Defensible, simple, no streaming infra |
| **Target monthly cost** | **~$1.80** | Hard cap: $5 |

**Day-1 source list (start here, do NOT widen prematurely):**
- arXiv (categories: `cs.AI`, `cs.LG`, `cs.CL`)
- GitHub Search API (AI-relevant repos by topic/language)
- Hacker News Algolia API
- Semantic Scholar (citation enrichment on arXiv papers)

**Deferred to week 4 (only if time allows):**
- YouTube Data API (lagging saturation signal + comment mining)
- Reddit (saturation signal — needs OAuth proxy)
- TikTok Creative Center (trending hashtags — public, scrapeable)
- X / Grok (demand mining)
- VidIQ MCP integration (YouTube outliers + IG reels)

---

## 3 · Final tech stack — exact versions

### Python (pipeline)
```
python = "3.12.*"
httpx = "^0.27"
pydantic = "^2.7"
pandas = "^2.2"
numpy = "^1.26"
feedparser = "^6.0"          # arXiv Atom XML
PyGithub = "^2.3"            # GitHub API
sentence-transformers = "^3.0"
hdbscan = "^0.8.33"
umap-learn = "^0.5.6"
pymannkendall = "^1.4"
anthropic = "^0.40"          # Anthropic SDK
openai = "^1.50"             # xAI Grok uses OpenAI-compat
scikit-learn = "^1.5"
python-dotenv = "^1.0"
pytest = "^8.2"
```

### Cloudflare Worker
- **Wrangler CLI** (`npm install -g wrangler@latest`)
- **Worker runtime**: standard CF Workers (no extra packages)
- **KV namespace** for budget tracking + L1 cache
- Language: **TypeScript** (not JavaScript — for type safety on the small surface)

### GitHub Actions
- `ubuntu-latest` runner
- `actions/checkout@v4`
- `actions/setup-python@v5` with `python-version: '3.12'`
- `actions/cache@v4` for pip + sentence-transformer model cache
- Uses `GITHUB_TOKEN` (auto-provided) for committing back to repo

### Local dev tooling
- **Poetry** for Python dep management (`brew install poetry`)
- **uv** acceptable substitute if Poetry fails
- **pre-commit** with ruff + black

---

## 4 · Repo structure

Create this exact layout. Do not invent additional folders.

```
ai-alpha-radar/
├── .github/
│   └── workflows/
│       └── daily-snapshot.yml      # the cron pipeline
├── pipeline/
│   ├── __init__.py
│   ├── config.py                   # all tuning constants (velocity floors, weights)
│   ├── models.py                   # Pydantic models for Trend, DemandCluster, etc.
│   ├── fetch/
│   │   ├── __init__.py
│   │   ├── arxiv.py
│   │   ├── github.py
│   │   ├── hackernews.py
│   │   ├── semantic_scholar.py
│   │   ├── grok.py                 # week 4
│   │   ├── youtube.py              # week 4
│   │   └── reddit.py               # week 4
│   ├── normalize.py                # dedupe, n-gram extraction, term canonicalization
│   ├── score.py                    # velocity, saturation, hidden_gem, lifecycle, TBTS, convergence
│   ├── cluster.py                  # embeddings + UMAP + HDBSCAN
│   ├── demand.py                   # comment mining → demand clusters (Claude-powered)
│   ├── summarize.py                # the four Claude prompts + briefing
│   ├── predict.py                  # dated prediction generation + verdict checking
│   ├── snapshot.py                 # write data.json + snapshots/YYYY-MM-DD.json
│   └── run.py                      # orchestrator — the daily pipeline entry point
├── worker/
│   ├── src/
│   │   └── index.ts
│   ├── wrangler.toml
│   ├── package.json
│   └── tsconfig.json
├── public/
│   ├── data.json                   # current snapshot (the thing the dashboard reads)
│   ├── snapshots/
│   │   └── .gitkeep                # YYYY-MM-DD.json files land here daily
│   └── (frontend HTML, CSS, JS — assume team is handling separately)
├── tests/
│   ├── conftest.py
│   ├── fixtures/                   # cached API responses for offline tests
│   ├── test_fetch_arxiv.py
│   ├── test_fetch_github.py
│   ├── test_fetch_hackernews.py
│   ├── test_normalize.py
│   ├── test_score.py
│   ├── test_cluster.py
│   └── test_e2e_pipeline.py
├── data/
│   └── tracked_terms.json          # seed vocabulary; pipeline updates this
├── docs/
│   └── DATA_CONTRACT.md            # auto-generated from models.py
├── pyproject.toml
├── poetry.lock
├── .env.example
├── .gitignore
├── README.md
└── BACKEND_BUILD.md                # this file
```

---

## 5 · Prerequisites — what to set up before coding

### Accounts the user needs to create (ask the user, do not assume any of these exist)
- [ ] **GitHub** account (likely exists — `kuhnhomeuk@gmail.com`)
- [ ] **GitHub PAT** (Personal Access Token, `repo` + `read:org` scope) — for higher search rate limit
- [ ] **Cloudflare** account (free) — for the Worker + KV
- [ ] **Anthropic API** account with billing enabled — for Claude
- [ ] **xAI / Grok API** account with billing — for X/Twitter signal (week 4)
- [ ] **Semantic Scholar API key** (free, fill a form at semanticscholar.org)

### Local tools
```bash
# install Poetry
curl -sSL https://install.python-poetry.org | python3 -
# install Wrangler
npm install -g wrangler@latest
# verify
python3.12 --version  # must be 3.12.x
wrangler --version
```

### Secrets to configure
- **In GitHub repo secrets:** `ANTHROPIC_API_KEY`, `XAI_API_KEY`, `GH_PAT`, `SEMANTIC_SCHOLAR_KEY`
- **In Cloudflare Worker secrets:** `ANTHROPIC_API_KEY`, `XAI_API_KEY` (via `wrangler secret put`)
- **In `.env.local` (gitignored):** all the above for local pipeline runs

---

## 6 · The data contract — build this first

**Before writing any pipeline code**, finalize `pipeline/models.py`. The frontend team works in parallel against the schema below; if you change it later, the frontend breaks.

```python
# pipeline/models.py
from datetime import date, datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field

LifecycleStage = Literal["whisper", "builder", "creator", "hype", "commodity"]
SourceName = Literal["arxiv", "github", "hackernews", "semantic_scholar",
                     "youtube", "reddit", "grok_x", "tiktok"]
PredictionVerdict = Literal["pending", "tracking", "verified", "verified_early", "wrong"]

class SourceCounts(BaseModel):
    arxiv_30d: int = 0
    github_repos_7d: int = 0
    github_stars_7d: int = 0
    hn_posts_7d: int = 0
    hn_points_7d: int = 0
    semantic_scholar_citations_7d: int = 0
    youtube_videos_7d: int = 0
    reddit_mentions_7d: int = 0
    x_posts_7d: int = 0

class ConvergenceEvent(BaseModel):
    detected: bool
    sources_hit: list[SourceName]
    window_hours: int  # 72 if convergence; null otherwise
    first_appearance: dict[SourceName, datetime]

class CreatorAngles(BaseModel):
    hook: str
    contrarian: str
    tutorial: str
    eli_creator: str  # the explain-like-I'm-a-creator analogy

class RiskFlag(BaseModel):
    breakout_likelihood: Literal["low", "medium", "high", "breakout"]
    peak_estimate_days: Optional[int]
    risk_flag: str
    rationale: str

class Prediction(BaseModel):
    text: str
    filed_at: date
    target_date: date
    verdict: PredictionVerdict
    verdict_text: Optional[str] = None
    verified_at: Optional[date] = None

class Trend(BaseModel):
    # Identity
    keyword: str
    canonical_form: str   # normalized: lowercase, hyphenated
    cluster_id: int
    cluster_label: str    # e.g. "Autonomous Reasoning"

    # Raw counts
    sources: SourceCounts

    # Computed metrics
    velocity_score: float
    velocity_acceleration: float
    saturation: float          # 0-100
    hidden_gem_score: float    # 0-1
    builder_signal: float      # 0-1
    lifecycle_stage: LifecycleStage
    tbts: int                  # 0-100, the composite score
    convergence: ConvergenceEvent

    # Claude outputs
    summary: str               # 1-line plain English
    summary_confidence: Literal["high", "medium", "low"]
    angles: CreatorAngles
    risk: RiskFlag
    prediction: Prediction

    # Display
    sparkline_14d: list[int]   # 14 daily values for the frontend chart

class DemandQuote(BaseModel):
    text: str
    source: str                # human-readable, e.g. "YouTube · Claude Code workflows"
    raw_url: Optional[str] = None

class DemandCluster(BaseModel):
    question_shape: str        # the canonical question form
    askers_estimate: int
    quotes: list[DemandQuote]  # top 3-5 representative
    sources: list[SourceName]
    weekly_growth_pct: float
    open_window_days: int      # estimated days before someone fills it
    creator_brief: str         # Claude-written, 2-3 sentences
    related_trends: list[str]  # canonical_form references to Trend.canonical_form

class DailyBriefing(BaseModel):
    text: str                  # ~150 words, markdown
    moved_up: list[str]        # canonical_forms
    moved_down: list[str]
    emerging: list[str]
    generated_at: datetime

class HitRate(BaseModel):
    rate: float                # 0-1
    verified: int
    tracking: int
    verified_early: int
    wrong: int

class Snapshot(BaseModel):
    snapshot_date: date
    generated_at: datetime
    trends: list[Trend]
    demand_clusters: list[DemandCluster]
    briefing: DailyBriefing
    hit_rate: HitRate
    past_predictions: list[Prediction]  # last 90 days, for the Star Log page
    meta: dict                          # source health, pipeline runtime, etc.
```

**Commit `models.py` + a regenerated `docs/DATA_CONTRACT.md` (with example JSON) on day 1.** Frontend team uses this. Do not break it.

---

## 7 · Build order — step by step

Each step has: **goal · what to build · how to verify · how long.** Do not move to the next step until the verification passes.

### Step 0 · Repo & Poetry init (30 min)
**Goal:** working `poetry install` and `pytest` baseline.

1. `git init && gh repo create ai-alpha-radar --public --source=.`
2. `poetry init -n --python "^3.12"` then add all deps from §3.
3. Set up `pyproject.toml` with `tool.ruff` + `tool.black` + `tool.pytest.ini_options`.
4. `mkdir -p pipeline pipeline/fetch tests public/snapshots data docs`
5. Add `.gitignore` (Python defaults + `.env*` + `.od/` + `.venv/`).
6. Write a `tests/test_smoke.py` that asserts `1 == 1`. Run `poetry run pytest` — green.
7. Commit: `chore: init repo`.

**Verify:** `poetry run pytest -v` exits 0.

---

### Step 1 · Models + data contract (1 hr)
**Goal:** the schema is locked.

1. Write `pipeline/models.py` exactly per §6.
2. Write `tests/test_models.py` — for each model, build an example, dump to JSON, re-parse, assert equality.
3. Write a tiny script `scripts/gen_contract.py` that emits an example `Snapshot` to `docs/DATA_CONTRACT.md` as a JSON code block. Run it.

**Verify:** `docs/DATA_CONTRACT.md` exists and contains valid JSON parseable by `Snapshot.model_validate_json`.

---

### Step 2 · arXiv fetcher (2 hr)
**Goal:** pull last-24h AI papers reliably.

1. `pipeline/fetch/arxiv.py` — `fetch_recent_papers(categories: list[str], lookback_days: int) -> list[Paper]`.
2. Use `feedparser` against `http://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL&start=0&max_results=200&sortBy=submittedDate&sortOrder=descending`.
3. Honor rate limit: **1 request per 3 seconds**. Use `time.sleep(3)` between calls. Do not parallelize arXiv.
4. Return Paper objects: `id, title, abstract, authors, published_at, primary_category, url`.
5. Cache responses to `tests/fixtures/arxiv_*.xml` on first run so tests are offline.

**Verify:** `poetry run python -m pipeline.fetch.arxiv` prints ≥10 papers from the last 24h with non-empty abstracts.

---

### Step 3 · Hacker News fetcher (1 hr)
**Goal:** posts + comment metadata via Algolia.

1. `pipeline/fetch/hackernews.py` — `fetch_ai_posts(lookback_days: int) -> list[HNPost]`.
2. Query: `https://hn.algolia.com/api/v1/search?tags=story&query=AI+OR+LLM+OR+GPT+OR+Claude+OR+model&numericFilters=created_at_i>{ts}`.
3. **Native CORS**, no proxy needed.
4. Return: `id, title, url, points, num_comments, created_at, story_text, author`.
5. For posts with comments and `num_comments > 5`, also fetch top comments via `/api/v1/items/{id}` (for demand mining later).

**Verify:** prints ≥30 posts with non-zero points from last 7 days.

---

### Step 4 · GitHub fetcher (2 hr)
**Goal:** AI-relevant repos with star velocity.

1. `pipeline/fetch/github.py` — uses PyGithub + `GH_PAT`.
2. Two queries: (a) `repos created in last 7 days with topic:ai OR topic:llm OR topic:agents` sorted by stars desc; (b) for repos already in `data/tracked_repos.json`, fetch current star count.
3. **Star velocity** = compare today's star count vs. yesterday's (read previous snapshot). On day 1, mark velocity as `None` with a `"warming_up"` flag.
4. Cap at 30 req/min — sleep if needed.
5. Return: `full_name, description, stars, stars_7d_delta, language, topics, created_at, pushed_at, html_url`.

**Verify:** outputs ≥20 repos with valid star counts.

---

### Step 5 · Semantic Scholar fetcher (1 hr)
**Goal:** citation enrichment on arXiv papers.

1. `pipeline/fetch/semantic_scholar.py` — `enrich_papers(arxiv_ids: list[str]) -> dict[str, CitationInfo]`.
2. POST to `https://api.semanticscholar.org/graph/v1/paper/batch` with arXiv IDs prefixed `ARXIV:`.
3. Use `SEMANTIC_SCHOLAR_KEY` header; honor 1 RPS.
4. Return: `{ arxiv_id: { citation_count, influential_citation_count, references_count } }`.

**Verify:** at least 70% of arXiv IDs from step 2 get a citation entry.

---

### Step 6 · Normalization (2 hr)
**Goal:** extract clean candidate terms across all sources.

1. `pipeline/normalize.py` — `extract_candidate_terms(papers, posts, repos) -> list[Term]`.
2. Pipeline:
   - Tokenize titles + abstracts.
   - Extract n-grams (1-3 tokens) with `scikit-learn` `CountVectorizer`.
   - Drop stopwords + numeric-only + tokens < 3 chars.
   - Canonicalize: lowercase, hyphenate spaces ("GPT-4" → "gpt-4", "world model agents" → "world-model-agents").
   - Apply known aliases map from `data/term_aliases.json` (e.g. "GPT-4o" ≡ "gpt4o" ≡ "gpt-4o").
   - Hash-dedupe.
3. Apply the **noise filter** (§7.3 of the spec):
   - TF-IDF spike filter: term in >60% of today's docs but <5% historical → drop.
   - Floor: `mentions_30d < 10` → drop.
   - Bot/low-signal: GitHub repos <10 stars + >7d old, HN posts with 0 comments + <5 points → drop.

**Verify:** test on a fixed fixture set; assert known noise (e.g. "ChatGPT" mentioned in every doc) is filtered, real signals are preserved.

---

### Step 7 · Scoring math (3 hr)
**Goal:** all metrics in §6 of the models, deterministic.

Implement in `pipeline/score.py` exactly these functions. Pure functions with type hints. **TDD: write the test first for each.**

```python
def velocity(mentions_7d: int, mentions_30d: int) -> float:
    """v = mentions_7d / max(mentions_30d / 30 * 7, 1). Floor mentions_30d at 10."""

def saturation(per_source_percentiles: dict[SourceName, float]) -> float:
    """Weighted: 0.35*github + 0.30*hn + 0.20*arxiv + 0.15*s2. Returns 0-100."""

def hidden_gem(velocity_score: float, saturation_pct: float, builder_signal: float) -> float:
    """0.40*velocity_norm + 0.35*(1-sat/100) + 0.25*builder_signal. Clip velocity at 10x."""

def lifecycle_stage(t: Trend) -> LifecycleStage:
    """Rule-based; see PLAN.md §6.4."""

def tbts(t: Trend) -> int:
    """0.35*velocity_norm + 0.30*hidden_gem + 0.20*lifecycle_weight + 0.15*convergence. 0-100."""

def detect_convergence(first_appearances: dict[SourceName, datetime]) -> ConvergenceEvent:
    """≥3 sources within 72h."""

def mann_kendall_confidence(daily_series: list[int]) -> float:
    """Z-score from pymannkendall; >1.96 = 95% confident upward trend."""
```

**Verify:** for each function, write a unit test with hand-computed values. All green.

---

### Step 8 · Semantic clustering (2 hr)
**Goal:** group related terms into themes.

1. `pipeline/cluster.py` — `cluster_terms(terms: list[str]) -> dict[str, ClusterAssignment]`.
2. Load `sentence-transformers/all-MiniLM-L6-v2` (cached via `actions/cache@v4` in CI).
3. Encode all terms → 384-D vectors.
4. UMAP reduce to 10-D (`metric='cosine'`, `n_neighbors=15`, `min_dist=0.0`).
5. HDBSCAN (`min_cluster_size=3`, `metric='euclidean'`).
6. Cluster label = the term in the cluster with the highest current velocity score.
7. Return `{term: (cluster_id, cluster_label)}`. Unclustered terms (label -1) get `cluster_id=-1, cluster_label="Unclustered Emerging"`.

**Verify:** seed test with known clusters (e.g. ["MCP", "browser agents", "tool calling"] should cluster together).

---

### Step 9 · Claude integration — the four prompts (3 hr)
**Goal:** generate summary, angles, risk, ELI-creator per trend, batched.

1. `pipeline/summarize.py` — uses Anthropic SDK.
2. **Use the Batch API** (`anthropic.messages.batches.create`). Latency irrelevant for a daily cron; saves ~50%.
3. **Prompt caching:** the shared system prompt (verbatim from PLAN.md §7.2) is marked with `cache_control={"type": "ephemeral"}`. 1-hour extended TTL.
4. **Structured outputs:** beta header `anthropic-beta: structured-outputs-2025-11-13`, each call passes a JSON schema. Parse with Pydantic; on parse failure, retry once with `{` prefill; on second failure, return `None` for that field (do not crash the run).
5. **L1 cache:** before any call, check Cloudflare KV: `key = sha256(canonical_form + date_bucket)`. If hit, skip Claude. (For local dev, fallback to SQLite or just skip the cache.)
6. **Cost guardrail:** per-day spend tracker. Hard ceiling $0.30/day. Read/write KV `spend:YYYY-MM-DD`. If hit ceiling, log and skip remaining calls.
7. The four prompts (A/B/C/D) are exactly as in `PLAN.md §7.2` — copy them verbatim.
8. The 5th call is the **Daily Movers Briefing** — one Sonnet call with all trends as input, ~150 word output.
9. Each prompt also generates a **dated prediction** (Step 10).

**Verify:**
- One end-to-end call against the real Anthropic API with ONE trend produces a valid `Trend` object with all fields populated.
- **Before unleashing the batch run with 75 trends, manually inspect this one card.** This is the §5 verification rule from `~/.claude/CLAUDE.md`.

---

### Step 10 · Dated predictions + verdict tracking (2 hr)
**Goal:** the accountability layer.

1. `pipeline/predict.py`.
2. **At flag time** (when a trend first crosses into Whisper or Builder), call Claude with a prompt that returns:
   - `prediction_text` — 1-sentence statement with a target date
   - `target_date` — ISO date
3. Persist predictions in `data/predictions.jsonl` (append-only log).
4. **Verdict checking** (run daily): for each pending prediction past its target date, check whether the trend has:
   - Crossed its saturation/stage target → `verified`
   - Crossed early → `verified_early`
   - Failed to cross → `wrong`
   - Otherwise → `tracking`
5. Compute `HitRate` from the predictions log.
6. **For the day-1 demo: backtest predictions against the last 90 days of snapshots** — write a one-shot script `scripts/backtest_predictions.py` that generates retroactive predictions and verdicts so the Star Log page is populated on day 1.

**Verify:** `data/predictions.jsonl` exists with ≥10 entries, `HitRate.rate` is a sensible 0–1 value.

---

### Step 11 · Demand Clusters (the wedge — 4 hr)
**Goal:** mine comments across platforms, cluster into question-shapes.

1. `pipeline/demand.py`.
2. **Comment sources** (week 1 ships with HN only; week 4 adds the rest):
   - **HN comments** — already fetched in step 3.
   - **YouTube comments** (week 4) — via VidIQ MCP `vidiq_video_comments` tool: for each trend's top 5 YouTube videos, pull top 20 comments.
   - **Reddit comments** (week 4) — via the Reddit OAuth proxy in the Worker.
   - **X replies** (week 4) — via Grok API: prompt = "Find the top 20 critical reply threads on AI X today complaining about or asking about {trend}. Return as JSON."
3. **The clustering prompt** — single Claude (Sonnet) call per trend:
   ```
   Given these N comments about {trend_keyword}, identify the top 3 question-shapes —
   recurring questions, pain points, or unmet needs. For each, return:
   - question_shape (canonical form of the question)
   - askers_estimate (how many comments express this — be conservative)
   - quotes: 2-3 verbatim representative quotes with their source citation
   - weekly_growth_pct (estimate based on the comment timestamps provided)
   - open_window_days (your estimate of how long until someone fills the void)
   - creator_brief: 2-3 sentence content brief for a creator who wants to answer this

   Return ONLY valid JSON matching the DemandCluster schema.
   ```
4. **Dedupe across trends** — two trends may produce overlapping demand clusters. Merge by cosine similarity > 0.85 on the question_shape embedding.
5. Output: top 6–12 DemandCluster objects in the snapshot.

**Verify:** for one trend with real HN comments, the output contains ≥1 plausible question-shape with real quotes.

---

### Step 12 · Snapshot writer + orchestrator (2 hr)
**Goal:** the daily run, end-to-end.

1. `pipeline/snapshot.py` — `write_snapshot(snapshot: Snapshot) -> None`. Writes both `public/data.json` and `public/snapshots/{snapshot_date}.json`.
2. `pipeline/run.py` — orchestrator:
   ```python
   def main():
       # 1. fetch
       papers = arxiv.fetch_recent_papers(...)
       posts  = hackernews.fetch_ai_posts(...)
       repos  = github.fetch_trending_repos(...)
       citations = semantic_scholar.enrich_papers([p.id for p in papers])

       # 2. normalize
       terms = normalize.extract_candidate_terms(papers, posts, repos)

       # 3. score
       trends = [score.compute_metrics(t, ...) for t in terms]

       # 4. cluster
       clusters = cluster.cluster_terms([t.canonical_form for t in trends])
       for t in trends: t.cluster_id, t.cluster_label = clusters[t.canonical_form]

       # 5. Claude — summary/angles/risk/eli/prediction (Batch API)
       trends = summarize.enrich_with_claude(trends)

       # 6. predictions verdict update
       hit_rate, past_preds = predict.update_verdicts(...)

       # 7. demand clusters
       demand = demand.mine_clusters(trends, posts, ...)

       # 8. briefing
       briefing = summarize.daily_briefing(trends)

       # 9. write
       snap = Snapshot(...)
       snapshot.write_snapshot(snap)
   ```
3. Idempotent — if `data.json` for today already exists, overwrite with newer.
4. Log every step's runtime + record-count to stdout (GH Actions logs).

**Verify:** running `poetry run python -m pipeline.run` locally produces a valid `public/data.json` parseable as `Snapshot`.

---

### Step 13 · Cloudflare Worker (3 hr)
**Goal:** CORS proxy + Claude proxy + KV-backed budget/cache.

1. `cd worker && wrangler init` (TypeScript).
2. Routes:
   - `GET /proxy/arxiv?query=...` → forwards to arXiv, strips CORS.
   - `GET /proxy/s2?...` → forwards to Semantic Scholar with key from env.
   - `GET /proxy/reddit?...` → OAuth-handshakes Reddit (week 4).
   - `POST /api/deep-dive` → on-demand Claude call from frontend (Sonnet, streaming).
   - `GET /api/spend?date=YYYY-MM-DD` → returns today's spend (for dashboard meta panel).
3. **Allowlist origins** to prevent abuse (`*.github.io` + localhost).
4. KV namespace `RADAR_KV`:
   - `spend:YYYY-MM-DD` → cumulative cents spent today
   - `cache:sha256(...)` → Claude JSON responses, 24h TTL
5. `wrangler secret put ANTHROPIC_API_KEY` and `XAI_API_KEY`.
6. Deploy: `wrangler deploy`. Confirm URL in terminal.

**Verify:** from a browser, `fetch('https://<worker>.workers.dev/proxy/arxiv?query=cs.AI')` returns parsed XML/JSON without CORS errors.

---

### Step 14 · GitHub Actions cron (1 hr)
**Goal:** automate everything.

`.github/workflows/daily-snapshot.yml`:

```yaml
name: Daily Snapshot

on:
  schedule:
    - cron: '0 6 * * *'   # 06:00 UTC daily
  workflow_dispatch:       # manual trigger button

jobs:
  snapshot:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Cache Poetry deps
        uses: actions/cache@v4
        with:
          path: ~/.cache/pypoetry
          key: ${{ runner.os }}-poetry-${{ hashFiles('poetry.lock') }}

      - name: Cache HuggingFace model
        uses: actions/cache@v4
        with:
          path: ~/.cache/huggingface
          key: ${{ runner.os }}-hf-MiniLM-L6-v2

      - run: pip install poetry==1.8.3
      - run: poetry install --no-interaction --no-root

      - name: Run pipeline
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
          GH_PAT: ${{ secrets.GH_PAT }}
          SEMANTIC_SCHOLAR_KEY: ${{ secrets.SEMANTIC_SCHOLAR_KEY }}
          WORKER_URL: ${{ vars.WORKER_URL }}
        run: poetry run python -m pipeline.run

      - name: Commit snapshot
        run: |
          git config user.name "radar-bot"
          git config user.email "radar-bot@users.noreply.github.com"
          git add public/data.json public/snapshots/ data/predictions.jsonl
          git diff --cached --quiet || git commit -m "snapshot: $(date -u +%Y-%m-%d)"
          git push
```

**Verify:** trigger via `workflow_dispatch`. Job goes green. `public/data.json` updated in repo.

---

### Step 15 · End-to-end test + frontend handoff (2 hr)
**Goal:** the data the frontend already mocks is now real.

1. Open the existing `mockup.html` or `mockup-b.html` in a browser; redirect its data source from in-code `STARS` constants to `fetch('/public/data.json')`.
2. Verify the bubble chart / star map / leaderboard / demand cards / star log all render against real data.
3. **The single hardest test:** does the **Signal Proof / Star Log** page show real timestamp deltas? This is the demo-defining feature. If yes, you're done.

---

## 8 · The data contract (the JSON the frontend reads)

Top-level shape of `public/data.json`:

```json
{
  "snapshot_date": "2026-05-13",
  "generated_at": "2026-05-13T06:14:22Z",
  "trends": [ { Trend }, ... 50-75 of them ],
  "demand_clusters": [ { DemandCluster }, ... 6-12 of them ],
  "briefing": { DailyBriefing },
  "hit_rate": { HitRate },
  "past_predictions": [ { Prediction }, ... last 90 days ],
  "meta": {
    "pipeline_runtime_seconds": 142,
    "sources": {
      "arxiv": { "fetched": 187, "ok": true },
      "github": { "fetched": 42,  "ok": true },
      "hackernews": { "fetched": 64, "ok": true },
      "semantic_scholar": { "fetched": 180, "ok": true }
    },
    "claude_cost_usd": 0.043,
    "claude_cache_hit_rate": 0.78
  }
}
```

Snapshots in `public/snapshots/YYYY-MM-DD.json` use the same shape.

---

## 9 · Cost & operational guardrails

| Guardrail | Where | Value |
|---|---|---|
| Daily Claude spend cap | Worker KV | $0.30 |
| Per-request token cap | Worker → Anthropic | input 600, output 300 |
| arXiv rate | `pipeline/fetch/arxiv.py` | 1 req / 3 sec, hard sleep |
| GitHub rate | PyGithub config | 30 req/min search, sleep on 429 |
| Semantic Scholar rate | `pipeline/fetch/semantic_scholar.py` | 1 RPS |
| HF model cache | GH Actions `actions/cache` | model not re-downloaded daily |
| Snapshot retention | `public/snapshots/` | unbounded; git history is the audit log |
| Cache TTL | Worker KV `cache:` keys | 24h |

If the daily spend cap is hit, the pipeline logs `BUDGET_EXCEEDED` and serves stale-cached Claude outputs for remaining trends. Never crashes the snapshot.

---

## 10 · Testing strategy

- **Fixture-based unit tests** for every fetcher (no real network in `pytest`).
- **Property-based tests** with `hypothesis` for the scoring math (velocity floor, saturation bounds 0–100, lifecycle exhaustive).
- **One end-to-end test** that runs the full pipeline against frozen fixtures and asserts the resulting `Snapshot` validates.
- **Pre-commit hook** runs ruff + black + pytest. CI must be green before merge.

Coverage target: 70% on `pipeline/score.py` and `pipeline/normalize.py` (the deterministic core). Less elsewhere is fine.

---

## 11 · Open questions — ASK THE USER, do not guess

1. **GitHub PAT** — does the user have one with `repo` scope? If not, walk them through generating it at `github.com/settings/tokens`.
2. **Anthropic account** — does it have billing enabled? Check at `console.anthropic.com/settings/billing` before any Batch API call.
3. **xAI account** — does the user have one? If not, defer Grok integration to week 4.
4. **Repo name** — `ai-alpha-radar` assumed. Confirm.
5. **Repo public/private** — public makes GitHub Pages free. Confirm.
6. **Demo niche default** — what string goes into the niche field when there's no localStorage value? Suggested: `"AI tools for solo creators"` (matches user's context).
7. **Frontend repo: same or separate?** Default: same repo, `public/` contains both `data.json` and HTML/CSS/JS. Confirm.

---

## 12 · Definition of done

You are done when ALL of these are true:

- [ ] `poetry run python -m pipeline.run` executes locally without error
- [ ] Output `public/data.json` validates against `Snapshot` schema
- [ ] At least 30 trends in the snapshot with non-null summaries + angles + predictions
- [ ] At least 4 demand clusters with real quotes from real HN comments
- [ ] At least 10 historical predictions in `past_predictions` (from backtest script)
- [ ] HitRate displays a real, non-zero value
- [ ] GitHub Actions cron has run successfully ≥1 time and committed an updated `data.json`
- [ ] Cloudflare Worker `/proxy/arxiv` and `/api/deep-dive` both return 200s
- [ ] Frontend `mockup-b.html` renders against real `data.json` with no crashes
- [ ] Daily Claude spend in the meta block is under $0.10
- [ ] README has a "How to add a new source" section, ≤200 lines

When all green, commit a tag: `git tag v0.1.0 && git push --tags`.

---

## 13 · What NOT to build

- ❌ No user accounts, no login, no auth
- ❌ No database (JSON files in repo are the database)
- ❌ No realtime websockets — daily cron is the only refresh
- ❌ No Docker, no Kubernetes, no Terraform — Cloudflare + GH Actions is the infra
- ❌ No "configurable scoring weights via UI" — the weights are in `pipeline/config.py`, change with a PR
- ❌ No support for Google Trends, Facebook, LinkedIn — explicitly excluded
- ❌ No React, Vue, Svelte on the backend side — frontend is a separate concern
- ❌ No GraphQL — REST + a single JSON file is plenty
- ❌ No image generation, no video generation — text only

---

## 14 · References (the user has these locally)

- **`PLAN.md`** — the full strategic plan with all source research, math derivations, Claude prompts, and the 4-week schedule. Read it after this file if you want depth.
- **`mockup.html`** and **`mockup-b.html`** — working frontend mockups with the exact UI the data drives.
- **`Downloads/AI_Alpha_Radar_Research.md`** + **`Downloads/trend-finder-research.md`** — the original ideation docs that informed the product wedge.

---

**You are done reading. Start at §7 Step 0.**
