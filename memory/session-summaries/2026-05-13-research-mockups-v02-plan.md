# Session — 2026-05-13 (research, mockups, v0.2 plan)

## Topic
AI Alpha Radar — research fan-out, three full mockups (A/B/C), strategic x-factor reframe, BACKEND_BUILD.md handoff spec, and v0.2 frontend plan after v0.1 backend shipped.

## What we worked on
- Fan-out/fan-in research: 5 Sonnet researchers + 1 Opus 4.7 synthesizer covered data sources, scoring algorithms, tech stack/automation, competitive landscape, and the Claude insight layer. Synthesized into [PLAN.md](../../PLAN.md). Stack locked at ~$1.80/mo: Python 3.12 + Cloudflare Worker + GH Actions cron + JSON-in-repo + Claude Haiku 4.5 (batched) + Sonnet 4.6 for daily briefing and on-demand deep dives. Day-1 sources: arXiv, GitHub, Hacker News, Semantic Scholar.
- Three full mockup pages, all served at http://localhost:8765:
  - [mockup.html](../../mockup.html) — Bloomberg/cyberpunk dark dense terminal. Dean's pick for Radar + Hidden Gems.
  - [mockup-b.html](../../mockup-b.html) — Observatory/constellation with `<canvas>` star map, Cormorant serif, champagne gold. Dean's pick for Demand Clusters / Star Log / Almanac.
  - [mockup-c.html](../../mockup-c.html) — Brutalist printer-paper, Times Roman 132px, hot red, OKLch palette extracted from `nexu-io/open-design`'s `directions.ts`. Dean rejected: "not a fan, A and B is better."
- Strategic x-factor conversation: reframed the product from "trend dashboard" to **unmet-demand detector**. Hero unit becomes Demand Clusters (Claude clusters comments/replies into question-shapes like "How do I run Claude Code on a 16GB Mac?"). Plus an accountability layer — every flagged trend carries a dated prediction with a public hit-rate scoreboard. Grok API approved as the X/Twitter signal source (native X grounding solves the $200/mo Basic-tier API problem).
- Source access reality per platform — agreed: VidIQ MCP (already in stack) for YouTube + Instagram, Grok for X, TikTok Creative Center for hashtags; skip LinkedIn and Facebook entirely.
- Explored `nexu-io/open-design` — decided NOT to clone (it's a meta-tool for orchestrating coding agents to generate design artifacts; wrong altitude). Extracted only `apps/daemon/src/prompts/directions.ts` — 5 OKLch design directions — as inspiration for mockup-c.
- Wrote [BACKEND_BUILD.md](../../BACKEND_BUILD.md) — self-contained 15-step spec for a fresh implementing agent. Pydantic data contract, locked decisions, prerequisites, verification per step, $5/mo hard cap, explicit anti-scope.
- Wrote a `/47`-style prompt to hand BACKEND_BUILD.md to Opus 4.7 in Claude Code CLI.
- **Backend v0.1 shipped between sessions** — 23 commits, 117 tests green at https://github.com/kuhnhomeuk-cell/ai-alpha-radar. 30 trends in `public/data.json`, 13 past predictions seeded, mock demand clusters pending `--claude` prompt-tuning flip. See companion session summary `2026-05-13-backend-steps-0-15-shipped.md`.
- Reviewed the 5-page set (Radar / Hidden Gems / Demand / Star Log / Almanac) — minimal overlap, intentional supply-side vs demand-side vs accountability split.
- Identified six gaps in the original plan: cluster/theme overlay on Radar, cross-page bridges (Radar↔Demand, Hidden Gems↔Star Log), convergence-event timeline modal, watch list (localStorage), cross-page search, source-health footer.
- Wrote a `/47`-style prompt for a fresh Claude Code session to execute the v0.2 frontend (9 commits, single file `public/index.html`, pure UI — all data already exists).

## Decisions made
- v0.2 style: **mixed (Option A)** — Radar + Hidden Gems rebuilt in mockup-A's dark-Bloomberg; Demand Clusters / Star Log / Almanac stay in mockup-B's observatory. Shared design-token layer is the only thing keeping the mix coherent.
- v0.2 is **pure frontend** in `public/index.html`. Backend is locked. No `pipeline/` or `worker/` changes.
- **Sub-agent strategy:** do NOT fan out v0.2 build. Single sequential agent for the 9 commits. Fan-out was right for the research phase (5 heterogeneous lanes, one synthesizer) — wrong for solo-scale building (coordination overhead > parallel speedup). Optional one parallel agent for an isolated infra task (Worker) but Worker already shipped.
- No automated tests for pure UI wiring — visual inspection in browser is the right verification gate per global §5.
- v0.3 parked: compare-two-trends, export-as-tweet, onboarding flow for niche.

## Key insights
- *"By the time it trends on Google, the star has already collapsed"* — the timestamp delta is the product.
- The hero unit is **unmet audience demand**, not trending topics. Trends are evidence; demand clusters are the asset.
- Sub-agent fan-out fits **heterogeneous research with a single synthesis head**. It does not fit **homogeneous build work with shared types**, where coordination overhead dominates.
- BACKEND_BUILD.md + `/47` was a clean handoff pattern — produced v0.1 (23 commits, 117 tests green) in one fresh session. Repeat the pattern for v0.2.
- Brutalist (mockup-c) was creatively defensible but commercially polarizing; Dean's "not a fan" was the correct read — for a school competition, polish > provocation.

## Open items / Next steps
1. Hand the v0.2 `/47` prompt to a fresh Claude Code session in the v0.1 repo. 9 commits in demo-value order: restyle preflight → cross-filter wiring → cluster overlay → convergence timeline → demand bridge in radar panel → inline predictions on Hidden Gems → watchlist → search + source-health footer.
2. Outstanding v0.1 §12 DoD items: rotate the two exposed credentials (PAT + Anthropic key both pasted into chat in the prior session), set repo secrets, enable GH Pages, deploy Cloudflare Worker, trigger the cron once to validate, prompt-tuning pass (then flip workflow to `--claude`), README "How to add a new source" section.

## Tags
ai-alpha-radar, frontend-planning, mockups, backend-shipped, demand-detection, design-direction, sub-agent-strategy, opus-prompts, slash-47-skill, cross-filter, cluster-overlay, observatory-design, bloomberg-design, brutalist-design, competition-jax
