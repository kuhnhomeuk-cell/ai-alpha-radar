# 2026-05-17 — Star Log regression fix + daily refresh + lint unblock

**TL;DR** Pushed 11 commits of UI polish + a fresh 2026-05-17 snapshot to live Vercel, diagnosed a regression where Claude's daily topic paraphrasing silently emptied the Star Log, shipped an embedding-fallback fix end-to-end (PR #9 merged), and unblocked a pre-existing lint failure on main along the way.

## What we discussed
- Whether the 9 → 0 drop in `past_predictions` between yesterday's and today's snapshots was a transient pipeline glitch or a structural problem.
- Root cause traced to `update_all_verdicts` doing exact keyword lookup against today's trends, while Claude Haiku rephrases the 30 topics every run (today's 30 vs. yesterday's 30 had zero shared keywords).
- Whether to fix via aliases, cluster_id, or direct embedding similarity. Cluster ids ALSO drift day-over-day (the centroid-canonicalization threshold of 0.2 wasn't met across the topic-set churn), so embeddings were the only stable surface.
- Whether to bump the lint baseline 24 → 27, properly replace the new innerHTML sites, or open a separate baseline PR — Dean picked the bump-with-rationale path.
- Four residual flags worth pursuing: demand_clusters schema drift, summarize batch dropping list-shaped responses, lint as required check on main, and topic vocabulary instability (the upstream cause of today's Star Log issue).

## What we decided
- **Predictions fix lives in `predict.build_lifecycle_lookup`** (new function). Exact-keyword match first; cosine-similarity fallback at distance < 0.4 using the same `sentence-transformers/all-MiniLM-L6-v2` model the clustering step already loads. `encode_fn` injectable so tests don't pay model-load cost.
- **Threshold 0.4** chosen via distance-histogram inspection on today's 92 historical predictions vs. 30 current trends — gap sits cleanly between genuine paraphrases (3 hits, distances 0.328–0.383) and noise stems (`llms`, `ai`, `hn`, distances 0.4+).
- **Lint baseline bumped 24 → 27** on main (commit e1142d5) with rationale documenting the 3 new sites (zones legend, leaderboard-empty banner, bubble tooltip) and their escaping posture.
- **PR #9 merged via squash** as commit 34802e1 after rebase-via-merge (no force-push needed).
- **Next-actions plan** written to `memory/next-actions.md` with explicit per-task sign-off blocks for the receiving agent.

## What we built
- [PR #9](https://github.com/kuhnhomeuk-cell/ai-alpha-radar/pull/9) — `fix(predict): fuzzy-match verdict lookup to survive topic paraphrasing` — 189 lines net, 5 new tests, full suite 446 passed.
- `chore(lint): bump no-innerHTML baseline 24 → 27` (e1142d5) — admin commit unblocking CI on main.
- `data: refresh daily trend snapshot` (6c9b147) — today's 2026-05-17 snapshot live on Vercel.
- `polish(ui): finalize radar dashboard usability` (a143657) — 928-line UI polish bundle.
- `memory/next-actions.md` — full four-task implementation plan with sign-off rule.

## What's next
- See [memory/next-actions.md](../next-actions.md) for the canonical punch list.
- **Highest priority:** Task 4 (topic vocabulary stability) — same family as today's regression on a different surface, and reduces future load on the embedding fallback.
- **Same risk family flagged but unaddressed:** demand_clusters schema drift (Task 1) and summarize list-response drop (Task 2).
- **Admin:** make `lint` a required check on main (Task 3) so the silent-red-CI situation that hid the baseline drift can't recur.
- **Empirical validation:** tomorrow's 06:00 UTC `daily-snapshot` is the first run that exercises the embedding fallback. Confirm `past_predictions > 0` after it lands.

## Notes for future me
- The Karpathy "Verify the Deliverable" rule paid off mid-session: I checked the empirical impact of the embedding fallback against the real predictions corpus before merging — found it adds only 3 historical matches (modest), which surfaced that the deeper fix is Task 4 (vocabulary stability) and not just the safety net.
- The classifier blocked both push-to-main attempts and the force-push despite question-UI confirmations — explicit chat-text approval with the action verb was required each time. Worth keeping in mind for future workflows: don't expect the AskUserQuestion UI to substitute for chat-text approval on destructive git ops.

## Post-merge tail (later same day)
- A parallel agent picked up `memory/next-actions.md` and shipped all four tasks: 75a8fe4 (demand schema drift), 5fc2604 (summarize list unwrap), 6073f63 (yesterday-keyword bias in topic extraction), plus branch protection set via `gh api` to require `lint` + `test` on main with admin bypass retained.
- Two unplanned bonus commits also landed: fbba547 (`fix(demand): restore "inferred" source tag for synthesized clusters`) and ee9cbf5 (`polish(ui): tighten topbar density — single-row nav + controls` — the CSS pass that briefly surfaced as an unrecognized 206-line working-tree diff).
- Sign-off blocks in next-actions.md filled in (commit 9e71ff3). All four tasks complete. Only open checkpoint: Task 4's 3-day keyword-carryover measurement, first observable after the 2026-05-20 daily snapshot.
- Suite at end of day: 452 passed, 2 xfailed. Tree clean, in sync with origin/main.
