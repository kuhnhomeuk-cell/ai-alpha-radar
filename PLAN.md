# AI Alpha Radar — Unified Implementation Plan

*Synthesized from 5 parallel research reports · Locked stack · Ship-ready · 2026-05-13*

---

## 1. Executive Summary

**AI Alpha Radar** is a Bloomberg-terminal-meets-cyberpunk dashboard that surfaces AI trends **3–6 weeks before they hit mainstream**, translated into ready-to-shoot content angles for YouTube Shorts creators. It fuses four leading-signal sources (arXiv, GitHub, Hacker News, Semantic Scholar) with a custom velocity + saturation + lifecycle scoring engine, then runs every detected trend through Claude Haiku 4.5 to generate creator-ready summaries, hook angles, and risk flags. The entire pipeline runs on GitHub Actions cron → Cloudflare Worker proxy → static GitHub Pages frontend → JSON-in-repo storage. **Total cost: ~$1.80/month.** The product wedge is not "we have more sources" — it's **cross-signal convergence detection paired with a creator-translation AI layer**, which no competitor (Exploding Topics, Alpha Signal, HF Daily) currently does.

---

## 2. The Differentiation Thesis

Two wedges, ordered. Lead with #1 in the demo. #2 is the moat that makes #1 defensible.

| # | Wedge | Why this one |
|---|---|---|
| **1** | **Creator translation layer** — Claude turns raw arXiv/GitHub/HN signal into a hook, a contrarian take, and a tutorial angle, scoped to the creator's stated niche. | Alpha Signal has the data and zero translation. Exploding Topics has translation and zero leading data. We're the only point in the matrix that does both. Instantly demoable. |
| **2** | **Cross-signal convergence alerts** — when arXiv + HN + a niche subreddit fire on the same concept within 72h, that's a leading indicator that mainstream picks it up 2–4 weeks later. | This is the actual scoring moat. It takes 2–3 months of tuning to replicate. Exploding Topics cannot add this in a sprint — they don't have the source mix. |

What we are explicitly **not** leading with: "more dashboards" / "another trending feed" / "AI newsletter." The demo line is the timestamp delta — we flagged this 3 weeks ago, Google Trends caught it today.

---

## 3. The 60-Second Competition Demo

| Time | Beat | What's on screen |
|---|---|---|
| 0–10s | **The problem.** "Every trend dashboard shows you what's already big. By the time it's on Google Trends, the content window is closed." | Side-by-side: Exploding Topics card vs. its Google Trends curve already peaked. |
| 10–25s | **The insight.** "Trends start in research papers, GitHub repos, and niche forums — 3 to 6 weeks before anyone searches for them. We watch those." | The Radar bubble chart, animated. Bubbles emerging from the lower-left "Whisper" zone. |
| 25–45s | **The proof.** Click a card AI Alpha Radar flagged 3 weeks ago. Show the convergence event (arXiv + HN + r/LocalLLaMA in a 48h window). Then pull up Google Trends today — the curve is just starting to climb. **The timestamp delta is the product.** | Signal Proof page with the dated event log. Then split-screen with today's Google Trends. |
| 45–60s | **The close.** "arXiv + GitHub + Hacker News + Semantic Scholar, scored by velocity and convergence, translated by Claude into three content angles tailored to your niche. That's AI Alpha Radar." Click "Deep Dive" → live Claude call → three hook variants render. | Content Intelligence card with the three angles streaming in. |

The judges should walk away remembering one phrase: **"the timestamp delta is the product."**

---

## 4. Recommended Final Stack

One row, one pick. No "or."

| Layer | Choice | Why this won |
|---|---|---|
| Pipeline runner | **GitHub Actions cron** (06:00 UTC daily) | Free 2,000 min/mo, logs in repo, no separate infra. |
| Pipeline language | **Python 3.12** | Best client libs for arXiv/HN/GitHub; pandas for scoring. |
| CORS + AI proxy | **Single Cloudflare Worker** | Free 100k req/day, holds `ANTHROPIC_API_KEY` as secret, proxies arXiv + Semantic Scholar + Claude calls in one place. |
| AI model | **claude-haiku-4-5** for cards, **claude-sonnet-4-6** for Daily Movers + on-demand Deep Dive | Haiku for the 75 daily cards (~$1.50/mo). Sonnet only fires on user click and once per day for the briefing. |
| Frontend build | **Vite + Vanilla TypeScript** | Static output, GitHub Pages compatible, no React tax. |
| Reactive UI | **Alpine.js (~15KB)** | All the reactivity we need for filters, modals, card state. |
| Charts | **ECharts** (tree-shaken, ~200KB) | Native bubble, timeline, sparklines, network graph, dark theme built in. D3 only if we need a custom convergence-event visual. |
| Fonts | **JetBrains Mono + IBM Plex Sans** via Bunny Fonts | Mono for all numbers (TradingView convention). Free, GDPR-clean. |
| Storage | **JSON in repo** — `public/data.json` (current) + `public/snapshots/YYYY-MM-DD.json` (history) | Git history = free versioning. Enables the "we flagged this 3 weeks ago" demo line. |
| Hosting | **GitHub Pages** | Free, auto-deploy on commit, perfect for a static SPA. |
| Embeddings | **`sentence-transformers/all-MiniLM-L6-v2`** (run in GH Action) | 80MB, free, good enough for clustering 200–500 daily terms. |
| Clustering | **UMAP → HDBSCAN**, precomputed nightly | Output: term→cluster JSON written into `data.json`. |
| **Total monthly cost** | | **~$1.80** |

---

## 5. Data Sources — Final Pick (Day-1 Ship List)

Start narrow. Four sources, one proxy, $0 in source costs. We are deliberately **not** shipping with Reddit, YouTube, or Google Trends on day 1 — they add friction (OAuth, quota, scraping fragility) and they're lagging signals anyway. We add the lagging ones in week 4 only if time allows; they're for the "saturation" denominator, not the discovery engine.

| # | Source | What it contributes | Auth | CORS | Notes |
|---|---|---|---|---|---|
| 1 | **arXiv API** | The earliest signal. 6–18 month lead time on terminology. Drives the "Whisper" stage. | None | **Blocked → CF Worker** | XML/Atom parse. ~1 req / 3s. Daily query: `cs.AI`, `cs.LG`, `cs.CL` categories last 24h. |
| 2 | **GitHub Search API** | Practitioner adoption. Star velocity = builders are building. Drives "Builder" stage. | PAT (free) | Native | 30 req/min search. We diff today vs. yesterday snapshots to compute star velocity. |
| 3 | **Hacker News Algolia API** | Engineer chatter — the bridge from research to builder mainstream. | None | **Native** | 1k hit cap per query, ~9h indexing lag. Repo archived but live. |
| 4 | **Semantic Scholar API** | Citation velocity on top of arXiv. Filters out papers that get published but never cited. | Free key (get one) | Unknown → assume Worker | 1 RPS unauth; the free key unlocks more. Used to enrich arXiv signal. |

**Deferred sources (week 4 stretch, or post-competition):**
- **Reddit** — OAuth + Pushshift dead + CORS blocked. Adds saturation signal but heavy lift. Defer.
- **YouTube Data API** — only 100 searches/day on free tier. Useful as a *saturation* indicator (if it's already on YouTube, we're late). Add in week 4 with a tight quota budget.
- **Google Trends** — pytrends is unreliable, SerpAPI is $50/mo. **Skip entirely.** Use YouTube view counts as the proxy for "mainstream search interest."
- **Papers With Code** — nice-to-have implementation-velocity layer on top of arXiv. Add in week 4 if it doesn't require auth gymnastics.
- **Product Hunt / Substack / Patents** — all skipped. Wrong cadence or wrong signal.

**Where reports disagreed:** Report 1's "MVP four-source stack" wins over a maximalist source list. Rationale: convergence scoring needs *clean* signals, not more signals. Four reliable feeds let us tune the algorithm; ten flaky feeds means we spend the competition window debugging fetchers.

---

## 6. The Scoring Model

Math we ship. No optional pieces — every formula below is in `pipeline/score.py` on day 1.

### 6.1 Velocity (per term, per source)
```
velocity_score = mentions_7d / max(mentions_30d / 30 * 7, 1)
```
- EMA smoothing on the daily series, alpha = 0.3.
- **Floor:** ignore any term with `mentions_30d < 10` — kills the low-count inflation failure mode flagged in Report 2.
- Acceleration = `velocity_this_week − velocity_last_week` (sign matters; negative acceleration = peaking).

### 6.2 Saturation (0–100, relative score)
Per-source percentile rank of the term, then weighted average. With only four day-1 sources we rebalance Report 2's weights:
```
saturation = 0.35*github + 0.30*hn + 0.20*arxiv + 0.15*semantic_scholar
```
(Reddit/Google Trends weights drop in when those sources land. Document this clearly in the UI: "Saturation is relative to tracked sources.")

### 6.3 Hidden Gem Score (0–1)
```
hidden_gem = 0.40 * velocity_norm
           + 0.35 * (1 - saturation/100)
           + 0.25 * builder_signal

builder_signal = normalize(github_new_repos_7d + github_stars_7d)
velocity_norm  = min(velocity_score, 10) / 10   # clip at 10x
```

### 6.4 Lifecycle Stage (rule-based, 5 stages)

| Stage | Rule | TBTS lifecycle weight |
|---|---|---|
| **Whisper** | `arxiv_30d > 0 AND github_repos < 3 AND saturation < 20 AND velocity > 1.5` | 0.20 |
| **Builder** | `github_repos >= 3 AND saturation < 35 AND builder_signal > 0.5` | **0.50 ← target zone** |
| **Creator** | `saturation 35–60 AND velocity > 1.2 AND hn_points_7d > threshold` | 0.80 |
| **Hype** | `saturation > 60 AND velocity > 2.0` | 0.40 |
| **Commodity** | `saturation > 75 AND velocity < 1.1 AND github_repos > 100` | 0.10 |

Note: our target buyer is the creator looking for Whisper→Builder transition trends, so the demo highlights Builder-stage cards. Creator-stage cards still surface; they're labeled "Window closing."

### 6.5 Cross-Platform Convergence (the moat)
For each term, look at the timestamp of its first appearance per source over the last 14 days. **Convergence event = the term appears in ≥3 of the 4 sources within a 72-hour window.** Flag these on the card with a `convergence_event: true` field and the timestamps. This is the scoring layer that powers the Signal Proof page.

### 6.6 Noise Filtering (Pre-Scoring)
1. SHA256 hash dedupe.
2. TF-IDF spike filter — term in >60% of today's docs but <5% historical = news event, not a trend. Drop.
3. Bot/low-signal filter — GitHub repos with <10 stars + >7d old; HN posts with 0 comments and <5 points.

### 6.7 Semantic Clustering
Run nightly inside the GH Action:
1. Extract ~200–500 candidate terms (n-grams from titles + repo names).
2. Embed with `all-MiniLM-L6-v2`.
3. UMAP → 10D → HDBSCAN, `min_cluster_size=3`.
4. Cluster label = highest-velocity term in cluster.
5. Write `cluster_id` and `cluster_label` into each card.

### 6.8 Composite "Trend Before Trend Score" (TBTS)
```
TBTS = 0.35 * velocity_norm
     + 0.30 * hidden_gem
     + 0.20 * lifecycle_weight
     + 0.15 * cross_platform_lead
```
Displayed 0–100 (multiply by 100, round). **Saturation is shown as a separate field, never folded in.**

---

## 7. The Claude Insight Layer

### 7.1 Models
- **claude-haiku-4-5** — all 75 daily trend cards. Batched.
- **claude-sonnet-4-6** — once daily for the Movers Briefing; on-demand when user clicks "Deep Dive."

### 7.2 The Four Prompts

**Shared system prompt** (cached, `cache_control: ephemeral`, 1-hour extended cache):
```
You are a trend-analysis engine for a YouTube Shorts creator dashboard.
Return ONLY valid JSON matching the schema provided. No prose. No markdown fences.
If you have low confidence about a trend, set "confidence": "low" and keep fields brief.
Never fabricate specific people, numbers, products, or dates.
The user's content niche is: {{user_niche}}. Tailor angles to this niche when possible.
```

**Prompt A — One-line summary**
```
Trend keyword: {{keyword}}
Cluster context: {{cluster_label}}; related terms: {{related_terms}}
Signal data: arxiv_papers_7d={{a}}, github_repos_7d={{g}}, hn_posts_7d={{h}}

Task: Write a single-sentence summary of this trend in plain English, max 18 words.
No jargon. A smart non-engineer should understand it.

Return JSON: {"summary": string, "confidence": "high"|"medium"|"low"}
```

**Prompt B — Creator angles (3 hooks)**
```
Trend keyword: {{keyword}}
Summary: {{summary}}
Creator niche: {{user_niche}}

Generate three YouTube Shorts angles. Each must be a standalone-titleable hook (≤12 words).
- "hook": the most clickable framing
- "contrarian": the unpopular-take framing
- "tutorial": the how-to framing

Return JSON: {"hook": string, "contrarian": string, "tutorial": string}
```

**Prompt C — Risk / breakout probability**
```
Trend keyword: {{keyword}}
Lifecycle stage: {{stage}}
Velocity: {{velocity_score}}; Saturation: {{saturation}}; Convergence event: {{convergence}}

Estimate:
- breakout_likelihood: "low" | "medium" | "high" | "breakout"
- peak_estimate_days: integer (days until mainstream peak; 0 if already peaked)
- risk_flag: short string ("none" | "may be hype cycle" | "regulatory risk" | "single-source signal" | other)
- rationale: ≤25 words

Return JSON with all four fields.
```

**Prompt D — ELI-Creator**
```
Trend keyword: {{keyword}}
Technical summary: {{summary}}

Explain this trend using one analogy a YouTube viewer would get instantly.
Max 40 words. No jargon at all.

Return JSON: {"eli_creator": string}
```

### 7.3 Caching, Batching, Structured Outputs
- **L1 (KV):** `SHA256(keyword + date_bucket)` → Cloudflare KV, 24h TTL. Re-runs of the same day are free.
- **L2 (Anthropic prompt cache):** Shared system prompt cached with 1h extended TTL. Hit rate target ≥70%.
- **Batch API:** Daily run uses Anthropic's Batch API → ~50% cost cut. Latency irrelevant for cron.
- **Structured Outputs:** Use `anthropic-beta: structured-outputs-2025-11-13`. Each prompt has a JSON schema. Fallback: parse → retry with `{` prefill → null card (never crash the page).
- **Cost guardrails:** Daily spend cap $0.30 tracked in KV. Per-request ceiling: input 600 / output 300 tokens.

### 7.4 The Two WOW Additions

**WOW #1 — Daily Movers Briefing** (the hero element on the homepage)
- One Sonnet call after all cards generate, ~150 words.
- Format: "What moved · What died · What's emerging."
- Cost: ~$0.09/mo.
- Renders in a glassmorphic top banner with a typewriter animation on load.

**WOW #2 — Personalized Niche Angles**
- User enters their niche once on first visit (e.g. "AI tools for video editors") → stored in localStorage.
- That string is injected into every Prompt B as `{{user_niche}}`.
- Zero extra API cost.
- The angles instantly feel hand-crafted. This is the moment in the demo where the judges go "oh."

### 7.5 Anti-Hallucination
Already in the system prompt: "Never fabricate specific people, numbers, products, or dates." The pipeline never asks Claude to verify whether a trend is real — that's what the scoring engine is for.

---

## 8. Dashboard UX — Page-by-Page

Four pages. Top nav, dense, dark, mono numerics. All four reachable in ≤1 click from anywhere.

### Page 1: **Radar** (homepage, default route `/`)
- **Top banner:** Daily Movers Briefing (typewriter animation, refreshes daily).
- **Main viz:** ECharts bubble chart. X = saturation (0–100), Y = velocity (log scale), bubble size = TBTS, color = lifecycle stage. Lower-right quadrant = Whisper/Builder = the gold zone.
- **Right sidebar:** ranked list of top 20 TBTS cards. Each row: keyword · TBTS · stage chip · velocity arrow · click → modal.
- **Filter row:** niche selector, lifecycle stage chips, source toggles.
- **Aesthetic:** TradingView screener pattern. JetBrains Mono on all numbers. RAG dots for stage. Bloomberg-tight padding.

### Page 2: **Hidden Gems** (route `/gems`)
- Sorted by `hidden_gem` score descending.
- Card grid (3-wide on desktop, dense). Each card: keyword, hidden_gem score, sparkline of last 14d velocity, "Why it's hidden" one-liner (Prompt A output), three angle hooks (Prompt B), Deep Dive button.
- Empty state if no gems above threshold today: "No Whisper-stage trends today. Check Radar for Builder candidates."

### Page 3: **Signal Proof** (route `/proof`)
- The demo-defining page. Past flagged trends with the timestamp delta.
- Table: keyword · first flagged date · stage at flag · Google Trends crossover date · delta (days).
- Drill-in: each row opens a timeline visual — arXiv first appearance, GitHub spike, HN posts, then mainstream curve.
- This page is what makes the 60-second demo land. Builds with `public/snapshots/*.json` from week 2 onward.

### Page 4: **Content Intelligence** (route `/content`)
- Niche input box (persists in localStorage).
- For each top-N trend, render the three Prompt B angles in title-card style — designed to be screenshot-able.
- "Copy as title" button per angle.
- Calendar view stretch: 14-day window with one trend per day, sorted by "peak estimate."

### Global UI principles
- Density is respect for the user's intelligence.
- Mono font for every number. Sans for prose.
- Glassmorphism only on overlays/modals — never on dense data panels.
- Subtle scanline/grid background overlay for the cyberpunk hit.
- All keyboard navigable (`j/k` to move down the list, `enter` to open).

---

## 9. End-to-End Pipeline Diagram

```
┌────────────────────────────────────────────────────────────────────┐
│  GitHub Actions cron (06:00 UTC daily)                             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  pipeline/fetch.py                                           │  │
│  │   ├─ arXiv      (via CF Worker proxy)                        │  │
│  │   ├─ GitHub     (native; PAT in GH secret)                   │  │
│  │   ├─ HN Algolia (native CORS)                                │  │
│  │   └─ S. Scholar (via CF Worker proxy; free key)              │  │
│  │                                                              │  │
│  │  pipeline/score.py                                           │  │
│  │   ├─ dedupe + noise filter                                   │  │
│  │   ├─ embed (all-MiniLM-L6-v2) → UMAP → HDBSCAN clusters      │  │
│  │   ├─ velocity / saturation / hidden_gem / lifecycle / TBTS   │  │
│  │   └─ cross-platform convergence detection                    │  │
│  │                                                              │  │
│  │  pipeline/summarize.py                                       │  │
│  │   ├─ Anthropic Batch API → Haiku 4.5 × 4 prompts/card        │  │
│  │   ├─ Sonnet 4.6 × 1 → Daily Movers Briefing                  │  │
│  │   └─ KV cache lookup before each call                        │  │
│  │                                                              │  │
│  │  write public/data.json  +  public/snapshots/YYYY-MM-DD.json │  │
│  │  git commit + push                                           │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                  ┌────────────────────────┐
                  │  GitHub Pages          │
                  │  (auto-deploy on push) │
                  └───────────┬────────────┘
                              │
                              ▼
   ┌──────────────────────────────────────────────────────┐
   │  Browser: Vite + Vanilla TS + Alpine.js + ECharts    │
   │   ├─ fetch /data.json on load                        │
   │   ├─ render Radar / Gems / Proof / Content pages     │
   │   └─ on "Deep Dive" click:                           │
   │        → POST CF Worker /api/deep-dive               │
   │        → Worker calls Sonnet 4.6 with cached system  │
   │        → stream response back into modal             │
   └──────────────────────────────────────────────────────┘

   Cloudflare Worker (single deployment)
   ├─ /proxy/arxiv      → arxiv.org/api/query
   ├─ /proxy/s2         → api.semanticscholar.org
   ├─ /api/deep-dive    → api.anthropic.com  (ANTHROPIC_API_KEY secret)
   └─ /api/spend        → KV-backed daily budget tracker
```

---

## 10. Build Plan — Week-by-Week

**Assumption:** 3.5 weeks to competition demo.

### Week 1 — Scaffolding & Skeleton
- [ ] Init repo, Vite + Vanilla TS, GitHub Pages deploy of a "Hello Radar" page.
- [ ] Deploy Cloudflare Worker with `/proxy/arxiv` route. Hit it from the browser. Confirm CORS works.
- [ ] Add `ANTHROPIC_API_KEY` and GitHub PAT as secrets (Worker + GH Actions).
- [ ] Stub `pipeline/fetch.py` that pulls arXiv + GitHub + HN and writes a fake `data.json`.
- [ ] Wire GitHub Actions cron with a manual `workflow_dispatch` trigger.
- [ ] Drop in JetBrains Mono + IBM Plex via Bunny Fonts. Build the dark/glass shell of the Radar page.
- **Deliverable:** Static page loads, shows fake data, daily cron runs end-to-end with stub data.

### Week 2 — Real Data Pipeline
- [ ] Implement real arXiv parsing (Atom XML), GitHub Search with star-velocity diffing, HN Algolia query.
- [ ] Add Semantic Scholar via Worker proxy with free key.
- [ ] Implement noise filter (dedupe + TF-IDF spike + bot filter).
- [ ] Implement embeddings + UMAP + HDBSCAN clustering in the Action.
- [ ] Write `public/snapshots/YYYY-MM-DD.json` daily.
- [ ] Stand up the bubble chart on Radar with real data.
- **Deliverable:** Real trends rendering on Radar. Snapshot history starts accumulating.

### Week 3 — Scoring + Claude
- [ ] Implement velocity / saturation / hidden_gem / lifecycle / TBTS in `pipeline/score.py`.
- [ ] Implement cross-platform convergence detection. Test against last week's snapshots.
- [ ] Wire Anthropic Batch API call with all 4 prompts + structured outputs + L1 KV cache.
- [ ] Wire Daily Movers Briefing (Sonnet, 1 call/day).
- [ ] Add niche input + localStorage. Pipe through to Prompt B.
- [ ] Build Hidden Gems page and Content Intelligence page.
- [ ] On-demand Deep Dive endpoint on Worker, streaming Sonnet response into a modal.
- **Deliverable:** Full daily pipeline produces real cards with summaries + angles. Three of four pages live.

### Week 4 — UI Polish + Demo Rehearsal
- [ ] Build Signal Proof page using accumulated snapshots. Find at least 2–3 trends we can demo with a real timestamp delta.
- [ ] Polish glassmorphism, scanline overlay, RAG dots, sparklines, keyboard nav.
- [ ] (Stretch) Add YouTube Data API as a lagging saturation signal.
- [ ] (Stretch) Add Papers With Code.
- [ ] Set daily $0.30 budget cap in Worker; alert if hit.
- [ ] Rehearse the 60-second demo 5+ times. Time it. Cut anything that doesn't land.
- [ ] Prepare backup screencap in case live demo wifi fails.
- **Deliverable:** Demo-ready dashboard. Rehearsed pitch. Backup video.

**Verification rule:** Before the first paid Batch API run with all 75 trends, fire ONE card end-to-end, open the rendered modal in the browser, confirm summary + 3 angles + risk flag look right. Only then unleash the batch.

---

## 11. Cost Summary

| Line item | Monthly cost |
|---|---|
| GitHub Actions (well under 2,000 free min) | $0.00 |
| GitHub Pages hosting | $0.00 |
| Cloudflare Worker (well under 100k req/day) | $0.00 |
| Cloudflare KV | $0.00 |
| arXiv / GitHub / HN / Semantic Scholar APIs | $0.00 |
| Bunny Fonts | $0.00 |
| Claude Haiku 4.5 (75 trends/day × 4 prompts, batched + cached) | ~$1.50 |
| Claude Sonnet 4.6 — Daily Movers Briefing (1/day) | ~$0.09 |
| Claude Sonnet 4.6 — On-demand Deep Dive (estimate 20/day usage) | ~$0.20 |
| **TOTAL** | **~$1.79/mo** |

The $0.30/day budget cap in the Worker is the hard guardrail.

---

## 12. Risks & Open Questions

### Risks
| Risk | Mitigation |
|---|---|
| **arXiv proxy gets rate-limited mid-Action.** | Hard 1 req / 3s pacing in `fetch.py`. Cache last-good response in KV with 25h TTL. |
| **HN Algolia repo archived Feb 2026 — API may degrade.** | Monitor. Fallback to official HN Firebase API ready as a one-day spike. |
| **GitHub star-velocity needs T0/T1 snapshots — week 1 has no T0.** | Bootstrap by storing snapshots from day 1 of week 2. Surface a "warming up" badge for the first 7 days. |
| **Embedding model adds ~30s to the Action.** | 80MB model, fine. If runtime bloats, move to a weekly-updated precomputed term vocabulary. |
| **Alpha Signal ships a dashboard before competition.** | Doesn't matter for the JAX competition. Long-term: cross-signal convergence + creator translation is the moat. |
| **Claude API key leaks from the Worker.** | Only stored as a Cloudflare secret. Daily budget cap = blast radius limit. |
| **Demo wifi fails.** | Pre-recorded 60s screencap as backup. |

### Open Questions for the User
1. **Niche default.** What niche should localStorage default to for first-time visitors during the demo?
2. **Domain name.** Custom domain on GitHub Pages, or `username.github.io/ai-alpha-radar`?
3. **Reddit decision — final call.** Confirming we skip Reddit on day 1?
4. **YouTube Data API key.** Set up the Google Cloud project in week 1 even if we don't use it until week 4?
5. **Repo public or private?** Public makes GitHub Pages free and leans into the open-source story.
6. **Demo machine.** Demoing live on a laptop you control? If so, we cache `data.json` locally for offline fallback.

---

**Next Step:** Create the repo, deploy a one-route Cloudflare Worker that proxies arXiv, and confirm a browser fetch from `username.github.io` to `worker.your-subdomain.workers.dev/proxy/arxiv` returns parsed paper titles. That single proof-of-life unblocks every downstream piece of Week 1.
