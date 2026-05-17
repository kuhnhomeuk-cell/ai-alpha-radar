# Wave 6 (Grok x_search) — Cost Gate and Path C Lock

**TL;DR:** Built the xAI Grok x_search fetcher end-to-end and ran the §5 one-call gate. Real cost is $2.64/call × 30 trends = ~$79/day, 150× the 50¢ budget cap. A 5-agent research synthesis reframed X as a Phase 2 lagging signal. Locked **Path C — free multi-source stack first, X as a thin confirmation layer**. Wave 6 parked uncommitted.

## What we discussed
- xAI Responses API mechanics: `https://api.x.ai/v1/responses`, `tools: [{"type": "x_search"}]` (the deprecated `search_parameters` route returns 410 Gone), `usage.cost_in_usd_ticks` cost reporting (100M ticks = $1).
- Three live failures debugged in sequence: 410 (deprecated route) → 403 (zero credits) → 60s timeout → parser returning 0 mentions because citations live in nested `output[].content[].annotations[].url`, not at top level.
- Whether `grok-4.20-reasoning` vs `grok-4.3` would change the cost — verdict: same per-token price, but reasoning model burned reasoning tokens AND fired 4 x_search rounds. Switching to `grok-4.3` non-reasoning + `max_tool_calls: 2` dropped per-call cost from $4.87 to $2.64.
- 5-agent parallel research (Sonnet workers, Opus 4.7 synthesizer) covered: xAI pricing reality, x_search mechanics + return shape, X dev-API tiers, free alternatives, and architectural fit. Synthesis flagged $25/1K Live Search as a dead price tier, and Reddit dominating AI-search citations at 92.8%.

## What we decided
- **Path C confirmed.** Quote: *"Confirmed: Path C — free multi-source stack with X as a thin confirmation layer."* Free sources first (ArXiv RSS, HN Algolia, HF Daily Papers, Reddit, GitHub Trending, Semantic Scholar, Bluesky `getTrendingTopics`), X as Day-7 confirmation only.
- **Wave 6 parked**, not dropped. User answered "Let's wait" when asked to choose between top-5-only scope, audit-free-sources-first, or drop.
- The §5 cost gate is the right floor — one call surfaced the bulk-path blowout before any commit.

## What's next
- **Wave 6 worktree `.claude/worktrees/wave-6/` on branch `claude/wave-6-grok-search`** sits with 14 passing tests + uncommitted `pipeline/fetch/grok.py`, `tests/test_fetch_grok.py`, `tests/fixtures/grok_xsearch_sample.json`, `pipeline/run.py` (M), `.env.example` (M). 93 commits behind origin/main. Three options for when you unpark: (a) scope to `trends[:5]` and ship as the Day-7 confirmation layer (~$0.75/mo if weekly, ~$13/day if daily), (b) audit free-source gaps first (HF Daily Papers, Bluesky trending), (c) drop.
- **Main has 1 unpushed commit** (`8357387 docs(memory): append post-merge tail`) and 2 untracked session-summary files from earlier-today agent runs (`multi-agent-four-flags-shipped.md`, `radar-ui-bug-fixes-and-rhythm-rebuild.md`). Pending your call on commit/push.
- Free-stack audit candidates before any Wave 7: HuggingFace **Daily Papers** (vs Hub trending models that already exist) and Bluesky `getTrendingTopics` (current Bluesky fetcher uses Jetstream firehose for mentions, not the trending endpoint).
- Two locked agent worktrees still running.

## Reference numbers (locked from §5 gate)
- `grok-4.3` + `max_tool_calls=2`: **$2.64/call, ~17s latency, 2 x_search rounds, ~17 X-post URLs parsed**.
- Bulk 30 trends/day = ~$79/day = 150× the 50¢ cap.
- Replacement xAI price: $5 per 1K x_search tool calls (down from the dead $25/1K Live Search tier).
