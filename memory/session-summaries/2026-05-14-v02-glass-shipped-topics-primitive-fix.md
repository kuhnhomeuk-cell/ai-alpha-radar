# Session — 2026-05-14 (v0.2 glass shipped, topics-primitive correction)

## Topic
v0.2 frontend completed (10/10 commits including Liquid Glass pass), then surfaced and scoped a foundational v0.1.1 fix: replace n-gram primitive with Claude-extracted topics.

## What we worked on
- Reviewed v0.2 Commit 3 (cross-filter wiring). Agent surfaced an honest scope decision: source chips should hide trends with zero fetched data, NOT recompute saturation client-side (would need per-source breakouts not in snapshot). Chip group renamed to "Source presence" to avoid false promise. Approved.
- Reviewed outstanding v0.1 deployment items: rotate two credentials exposed in earlier session, set repo secrets, deploy Cloudflare Worker, enable GH Pages, prompt-tuning pass + `--claude` flip, README "How to add a new source".
- Explored Liquid Glass design language via Apple's CSS recipe (lucasromerodb/liquid-glass-effect-macos). Built `mockup-glass.html` showing both Tier 1 (pure CSS — backdrop-filter + tint + shine + heavy shadow) and Tier 2 (SVG displacement filter `feTurbulence` + `feDisplacementMap`) side-by-side on the Radar page. Concluded six elements get glass: top bar, daily-movers banner, filter chip pills, side detail panel, convergence modal, source-health footer. Bubble chart and dense cards do NOT.
- Decided **Tier 1 only** for the glass pass.
- Wrote /47-style prompt for Commit 10. Agent shipped it: `7043c15 feat(global): liquid-glass pass on radar (tier-1 css)`. All ten v0.2 commits now in.
- Inspected screenshot at `./.claude/worktrees/priceless-spence-011578/screenshots/v0.2-commit-10-glass.png`. Glass infrastructure landed (18 backdrop-filter occurrences in index.html, substrate gradients on line 99). BUT screenshot revealed the upstream data bug: leaderboard top-11 keywords are "propose", "framework", "tasks", "work", "experiments", "like", "introduce" — single common words leaking through `pipeline/normalize.py`. Bubble chart degenerate (velocity ≈ 10 for everything because `--claude` flag off → no real velocity scores).
- Wrote (then killed) a normalize.py stopword-tightening prompt. Dean's pushback caught my mistake: this isn't a stopword problem, it's an architectural one. The pipeline scores **n-grams as if they were topics**. They're not. The fix is replacing the primitive entirely.
- Wrote /47-style prompt for v0.1.1 topics fix: new `pipeline/topics.py` module, single Claude call (Haiku 4.5, one-shot, not Batch) reads papers+posts+repos and emits 30–50 named topics with aliases + source_doc_ids. Score.py, cluster.py adapt to operate on topics. Frontend unaffected (Trend.keyword field name stays, just populated with real names).
- Communication breakdown mid-session: Dean got frustrated with the volume of prompts/text I was producing ("10,000 words eating into my credits"). Re-grounded — only one prompt matters right now, the topics-fix prompt, pasted cleanly at end of session for one-shot copy.

## Decisions made
- v0.2 done. 10/10 commits shipped. Frontend functional pass complete.
- Liquid Glass: Tier 1 only. No SVG displacement. Six elements maximum.
- Source chips honestly do "presence filtering" not axis re-derivation. Per-source saturation breakouts deferred to v0.3 as a backend addition.
- The leaderboard problem is NOT a normalize.py fix. It's a primitive swap. Killed the stopword-tightening prompt.
- v0.1.1 = topics-pipeline replacement. ~4–6 hr agent work. ~$0.30/day extra Claude cost. Tagged v0.1.1.
- Communication rule reinforced: when Dean asks a simple question, give a simple answer. Don't paste long prompts unless he's asking for one to copy. Don't dump status boards on simple status questions.

## Key insights
- I made the spec-phase mistake at `BACKEND_BUILD.md §7 Step 6` by defining normalize as n-gram extraction. That set the wrong primitive for everything downstream. The fix should have surfaced before v0.1 shipped.
- Glass design can't be fairly evaluated until both upstream blockers clear: `--claude` flag flipped AND topics primitive fixed. The page is structurally complete but visually unevaluable on placeholder data.
- /47 + self-contained spec is a clean handoff pattern when it works, but Dean's chat-app credits cost real money — verbose prose in chat burns the budget regardless of code quality. Bias toward terse delivery; only paste full prompts when explicitly requested.
- When Dean says "I have no idea, just fix it" → that's a redirect to decide and execute, NOT to present more options. He hired me to make calls.

## Open items / Next steps
1. **Hand the v0.1.1 topics-fix prompt to a fresh Claude Code session.** That prompt is in the final code block of this session's transcript.
2. **Rotate the two exposed credentials** (PAT + Anthropic key) and set repo secrets at `github.com/kuhnhomeuk-cell/ai-alpha-radar/settings/secrets/actions`.
3. **Deploy the Cloudflare Worker**, enable GitHub Pages, trigger the cron once.
4. **Prompt-tuning pass** on the four trend prompts in `pipeline/summarize.py` — one-card inspections at ~$0.01 each — then flip workflow to `--claude`.
5. After v0.1.1 ships + `--claude` flag flips: fresh Radar screenshot. Only then is the Liquid Glass design honestly evaluable.
6. Parked v0.3: compare-two-trends, export-as-tweet, onboarding for niche, `Trend.saturation_by_source` backend field for honest source-recompute.

## Tags
ai-alpha-radar, frontend-v0.2-shipped, liquid-glass, design-direction, topics-pipeline, n-gram-vs-topics, primitive-correction, prompt-handoff, communication-style, opus-prompts, competition-jax
