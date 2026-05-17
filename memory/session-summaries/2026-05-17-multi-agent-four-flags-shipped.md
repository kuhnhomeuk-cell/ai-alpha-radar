# 2026-05-17 — Multi-agent fan-out shipped the four open flags + bonus follow-ups

**TL;DR** Three parallel implementation agents in worktrees closed Tasks 1/2/4 of the post-Star-Log punch list, branch protection (Task 3) went on `main` via `gh` API, an unplanned Pydantic-validation bug surfaced and got cleaned up via a spawned follow-up task, and Dean's in-progress header-density CSS pass landed too. Six commits to `main`, all pushed; test suite at 453 passing.

## What we discussed
- Dean handed me an implementation plan covering the four open flags from this morning's post-mortem (demand cluster schema drift, summarize list-shape drop, lint as required check, topic vocabulary stability). Asked me to oversee with a fan-out/fan-in pattern.
- Mid-session: a memory-doc commit (`ce88c23`) landed on `main` while my agents were running on worktrees branched off the older tip. Discussed cherry-pick vs merge and chose cherry-pick to avoid reverting that commit.
- Spawned-task agent (the "inferred" SourceName chip) ran, did the right work on its own branch but didn't auto-merge into `main`; Dean asked what was next, I diagnosed, merged.
- Dean confirmed the lingering `public/index.html` modification was intentional in-progress UI work and asked me to commit it.

## What we decided
- **Cherry-pick agent commits onto a moving `main`** when straight merge would revert unrelated work. The three agent commits applied cleanly with git auto-resolving the run.py overlap.
- **Branch protection on `main` with `enforce_admins: false`.** Lint + test required; admin bypass on so Dean (solo dev) can't lock himself out. Confirmed working: subsequent pushes show `Bypassed rule violations` in remote response.
- **Conservative alternate-key mapping in `_coerce_cluster_list`**: only `question`, `pain_point`, `prompt` → `question_shape`. No arbitrary key acceptance.
- **Unwrap single-element list responses in summarize**, drop multi-element or non-card-shape lists with existing warning.
- **Bias the topic-extraction prompt with yesterday's 30 keywords** rather than post-processing Claude's output. Preserves the value of fresh extraction.
- **Add `"inferred"` to `SourceName` enum** (clean fix) rather than keep the `"hackernews"` workaround. Restores the visual distinction between mined and synthesized clusters.

## What we shipped (commits, all pushed to `origin/main`)
- `5fc2604` — fix(summarize): unwrap single-element list responses from Claude
- `75a8fe4` — fix(demand): tolerate alternate Sonnet key shapes; warn on empty wedge
- `6073f63` — feat(topics): bias topic extraction toward yesterday's keywords
- `9e71ff3` — docs(memory): sign off all four next-actions tasks
- `fbba547` — fix(demand): restore "inferred" source tag for synthesized clusters
- `ee9cbf5` — polish(ui): tighten topbar density — single-row nav + controls

## Surprises worth remembering
- **Task 1's real bug was a swallowed Pydantic `ValidationError`.** `sources=["inferred"]` failed the `SourceName` `Literal[...]` enum, so every synthesized cluster died silently inside a `try/except Exception: continue`. The schema-drift hypothesis from the original brief would NOT have shipped clusters on its own.
- **`prior_snapshot_for_clusters` was loaded at line 1144 of `pipeline/run.py`, not 1114** as the brief said. Task 4 agent moved it up and deduplicated — net one fewer disk read.
- **A pre-existing test in `test_summarize.py` was asserting the old drop-everything contract.** Task 2 agent caught and replaced it rather than working around it.
- **Branch protection requires both context contexts to be reported as expected** — the `Bypassed rule violations` message says "2 of 2 required status checks are expected" even though CI hasn't run yet for the pushed commit. Admin bypass swallowed it cleanly.

## What's next
- **Wait until 2026-05-20** for the topic carry-over measurement (the only open sign-off checkbox on Task 4). Need 3 more daily snapshots to verify day-over-day keyword overlap ≥50%.
- **Optional cleanup:** garbage keywords in `data/predictions.jsonl` (`llms`, `ai`, `hn`, `claude`) from older pipeline versions. Harmless after PR #9.
- No active dev branches. Natural stopping point on this project.
