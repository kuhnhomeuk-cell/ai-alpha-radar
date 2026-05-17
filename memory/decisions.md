---
name: decisions
description: Long-form decision log for ai-trends. Mirrors Section D of CLAUDE.md with rationale.
type: project
---

Format: YYYY-MM-DD — decision — why · evidence (commit / PR / session).
Most recent at top.

---

## 2026-05-17

### Win condition reframed: shortest path from trend → published video
Why: A7 competitive scan in the 10-agent fan-out audit (`docs/AUDIT_AND_ROADMAP.md`) surfaced that the trend-detection category drifts upmarket because B2B pays — Exploding Topics / TrendHunter / CB Insights are all $200–$1000+/mo enterprise tools. Solo creators are systematically under-served. The win isn't "more accurate detection" (every competitor claims that); it's collapsing the time-to-script. Guides every Wave 5+ design choice (Pain Points + Comets are both trend-to-script accelerators, not detection improvements).
Evidence: this session 2026-05-17 wrap-up; `docs/AUDIT_AND_ROADMAP.md` competitive-scan section.

### Niche lock: AI tools for solo creators only
Why: explicit `Don't-Do` boundary against AI-adoption / medical / legal / finance / enterprise scope creep. Project niche was always notional ("solo creators") but never formalized as a rejection rule. Now it is.
Evidence: this session 2026-05-17.

### Comets as the 5th nav label
Why: matches the existing celestial / observatory metaphor (Sky Map, Demand Clusters, Star Log, Almanac). Outliers feel like comets — bright, fast, transient — and the metaphor reinforces creator framing.
Evidence: commit `acfce3d feat(youtube-outliers): Wave 5 — VidIQ-backed Comets nav route`.

### Wave 6 — xAI Grok X Search for `x_posts_7d`
Why: closes the longest-standing data-contract gap. `SourceCounts.x_posts_7d` has been hardcoded to 0 since v0.1.0 (audit flagged this as part of "the Big Lie" theme). Grok's `search_parameters` give per-trend X mention counts with reported cost ticks that fold into `--max-cost-cents`.
Evidence: commits `9bedb38 feat(grok): merge Wave 6 xAI X Search fetcher + run.py wiring`, `0fd805d fix(grok): correct ticks→cents conversion (was 100× over-reporting)`.

### Wave 5 — Perplexity Sonar + YouTube outliers ship pattern
Why: Perplexity is cheap, scheduled, pipeline-side (~$0.03/day for 30 trends), folds into the existing cost-cap. YouTube outliers via VidIQ MCP must be operator-scheduled (VidIQ tools only available inside Claude Code sessions, not CI). Architecture mirrors `pipeline/fetch/bluesky.py`: pure parse/dedupe/rank helpers + disk-backed cache (`data/youtube_outliers.json`) + the pipeline only reads.
Evidence: commits `9ba85da feat(perplexity)`, `acfce3d feat(youtube-outliers)`.

### DiggAI as cross-reference consensus signal
Why: Digg (relaunched May 2026 by Kevin Rose + Alexis Ohanian) ranks AI stories by engagement from ~2,000 curated X influencers — a fixed-graph "crowd" that's a different sample than HN/arxiv. Useful as triangulation: HN+arxiv+Digg = consensus boost; Digg-only = "media-driven / X-bubble"; HN-only = "technical / practitioner". No Digg API exists; access via Firecrawl `/v2/scrape` with `json` format. Cumulative cache at `data/digg_ai_corpus.json` keyed by story slug, accumulates observations for velocity analysis later.
Evidence: commit `59fd5c0 feat(digg): add Digg AI as cross-reference consensus source`.

### Branch protection on `main` with admin bypass
Why: GROUP A radar fixes (commit `19292c9`) added an `innerHTML` site without bumping the lint baseline. CI on `main` failed for hours until anyone noticed because `lint` wasn't a required check. Made `lint` + `test` required. `enforce_admins: false` retained — solo dev, an emergency hotfix that can't bypass protection is worse than the protection itself.
Evidence: `gh api ... repos/kuhnhomeuk-cell/ai-alpha-radar/branches/main/protection` showing `"contexts":["lint","test"]`; sign-off in `memory/next-actions.md` Task 3.

### Embedding-cosine fuzzy verdict lookup in `predict.py`
Why: Claude Haiku rephrases the 30 topics every day ("test-time compute scaling" → "inference-time compute scaling"). `update_all_verdicts` was doing exact-keyword lookup, so a prediction filed under yesterday's wording never matched today's. Result on 2026-05-17: Star Log went 9 → 0. Fix is `predict.build_lifecycle_lookup` composing an exact map + embedding cosine fallback at 0.4 threshold. Reuses the existing `sentence-transformers/all-MiniLM-L6-v2` model the clustering step already loads — no new heavy deps. Empirical: exact alone covered 30/122; fuzzy added 3 genuine paraphrases (RL → inverse RL, continual pre-training → class-incremental forgetting). Threshold 0.4 sits in a clean gap; 0.5+ admits stems like "llms"/"ai".
Evidence: PR #9 (commit `34802e1 fix(predict): fuzzy-match verdict lookup`). 5 new tests, full suite 446 → still green.

### Bias topic extraction with yesterday's keywords
Why: complementary to the fuzzy lookup. If today's extractor receives yesterday's 30 keyword labels and is told "reuse them verbatim when concepts overlap, otherwise mint fresh," the exact-match path covers more verdicts and the fuzzy fallback becomes less load-bearing. Prompt-bias not post-process — post-processing would override Claude's fresh extraction, which destroys the value of fresh extraction.
Evidence: commit `6073f63 feat(topics): bias topic extraction toward yesterday's keywords`. Backwards compatible — `previous_keywords` defaults to None.

### Single-element-only unwrap in summarize batch parser
Why: per-trend summarize prompt sometimes elicits `[{...card...}]` instead of `{...card...}`. Dropping these as "expected dict, got list" loses an enrichment that's actually valid. Unwrap only when `len == 1` and the element matches the card schema. Multi-element lists and lists with non-card-shaped elements still drop with the existing warning. Conservative — avoids accepting arbitrary shapes.
Evidence: commit `5fc2604 fix(summarize): unwrap single-element list responses from Claude`.

### Alternate-key map for demand cluster Sonnet responses
Why: Sonnet sometimes returns `question` / `pain_point` / `prompt` instead of `question_shape`. `_coerce_cluster_list` was silently dropping rows. Whitelist-only mapping for those three keys → `question_shape`. Refused arbitrary key acceptance. The empty demand wedge in production was hiding this — log was at `info`, escalated to `warning` when `len(demand_clusters) == 0` so an empty wedge is never invisible again.
Evidence: commit `75a8fe4 fix(demand): tolerate alternate Sonnet key shapes; warn on empty wedge`. Test: `test_synthesize_tags_clusters_with_inferred_source` in `tests/test_demand.py`.

### Added `inferred` to SourceName enum
Why: synthesized demand clusters were temporarily tagged `sources=["hackernews"]` to clear Pydantic validation. That worked but lost the dashboard's visual distinction between HN-mined and Sonnet-synthesized demand. Extending the Literal enum is the right fix — the synthesizer generates from the trend list, not from any single source, so `"inferred"` is the semantically correct tag. Dashboard already ignores demand-cluster sources for rendering, so the new tag is safe on the frontend.
Evidence: commit `fbba547 fix(demand): restore "inferred" source tag for synthesized clusters`. Test: `tests/test_demand.py::test_synthesize_tags_clusters_with_inferred_source`.

---

## 2026-05-15

### PR-only integration model
Why: All major work merges via `gh pr create` against `main`. No direct push. 5 PRs landed this date (#1-5: integration-topics-audit, reddit-rss, xfail-fix, briefing-md, cron-push-rebase). Locks in code review even for solo-dev.
Evidence: GH PR history.

### Wave 1-4 audit closure
Why: completed all 38 audit items from `docs/AUDIT_AND_ROADMAP.md` plus two residual concerns flagged in synthesis: escapeHtml needed actual unit tests (not just lint-only enforcement), and `.batch_state.json` needed to be cached across GH Actions runs or cross-run retries would double-spend the Anthropic Batch API.
Evidence: commits `3f3fca2 test(security): unit-test escapeHtml against XSS payloads`, `d78eba9 ci(daily-snapshot): cache .batch_state.json across runs`. Both landed via integration branch and merged via PR #1.

### Daily snapshot flipped to `--claude`
Why: The original `.github/workflows/daily-snapshot.yml` ran without `--claude` because prompt tuning wasn't complete. The Potemkin-data finding in the audit (every summary/angles/risk field said `"(awaiting Claude enrichment)"`) forced the flip. Cost gate via new `--max-cost-cents 50` CLI flag.
Evidence: commits `5609f53 feat(pipeline): add cost cap gate`, `672fef6 chore(ci): enable Claude enrichment in daily snapshot`.

---

## 2026-05-14

### Topic primitive switch — n-grams → Claude-extracted topics
Why: the original n-gram normalization produced garbage keywords (`llms`, `ai`, `hn`, `claude`) and missed semantic groupings. Claude Haiku extracts named topics from the daily corpus — slower (one extra API call) but the resulting cluster_labels are usable and the predictions tied to keywords are meaningful. v0.1.1 release.
Evidence: commit `b1622b0 fix(pipeline): replace n-gram normalization with Claude-extracted topics (v0.1.1)`. New module `pipeline/topics.py`, new tests `tests/test_topics.py`.

---

## 2026-05-13

### Stack locked at ~$1.80/mo
Why: fan-out research (5 Sonnet researchers + Opus 4.7 synthesis) into `PLAN.md` evaluated 10+ stacks. Chose: Python 3.12 + Cloudflare Worker (free tier) + GH Actions cron (free) + JSON-in-repo (no DB) + Claude Haiku 4.5 batched (per-card enrichment) + Sonnet 4.6 for daily briefing + on-demand deep dive. Hits the cost target with margin.
Evidence: `PLAN.md`, `memory/session-summaries/2026-05-13-research-mockups-v02-plan.md`.

### Data contract locked at `pipeline/models.py`
Why: every consumer (frontend, gen_contract.py, tests, snapshot writer) reads from one Pydantic source. Schema changes require an explicit frontend coordination note. `extra="forbid"` (added later, 2026-05-17 refactor) catches typos at validation time.
Evidence: commit `16e117d feat(models): lock data contract`.

### Day-1 sources: arXiv, GitHub, HN, Semantic Scholar
Why: highest signal-to-noise, all have stable free APIs, all parseable into the same `SourceCounts` shape. Newer sources (HuggingFace, Reddit, Bluesky, ProductHunt, Replicate, Newsletters, Perplexity, Grok, Digg) were added in Waves 3a, 5, 6 once the day-1 pipeline was stable.
Evidence: BACKEND_BUILD.md Steps 1-4.

### Dark-bloomberg / celestial aesthetic
Why: design pass on radar + hidden-gems used a dark navy background, serif headings (PT Serif), small-caps metadata labels, gold accents. Reads as a serious observatory. Maps thematically to "watching the night sky for new stars" — and the nav labels (Sky Map, Star Log, Almanac, Comets) all reinforce.
Evidence: commits `1681ddb refactor(public): shared design-token layer + rebuild radar in dark-bloomberg style`, `4198c54 refactor(public): rebuild hidden-gems page in dark-bloomberg style`.

---

## Earlier decisions
*Pre-2026-05-13 decisions are captured in `BACKEND_BUILD.md` and `PLAN.md` — those documents are the frozen spec.*
