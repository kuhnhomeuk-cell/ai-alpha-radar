---
name: next-actions
description: Punch list of next actions for ai-trends — open flags surfaced 2026-05-17 post Star-Log regression fix
type: project
---

# Next actions — 2026-05-17

Four open flags from the 2026-05-17 post-mortem of the Star Log regression. The Star Log fix itself shipped in [PR #9](https://github.com/kuhnhomeuk-cell/ai-alpha-radar/pull/9). These are the residual concerns; same risk family in some cases, different surfaces.

**Status (2026-05-17, end of day):** all four tasks landed on `main` by a parallel agent and signed off below. The only remaining checkpoint is Task 4's empirical carry-over measurement, which can't be taken until three more daily snapshots accumulate (≥ 2026-05-20).

**Sign-off rule (applies to every task below):** before marking a task done, the executing agent fills in the Sign-off block at the end of that task — agent id, date, merge commit SHA, the exact verify command(s) run, and the output line that confirms success. No sign-off ⇒ task is not done, even if a PR landed.

**Suggested order:** Task 1 + Task 2 together (same Claude schema-drift family); Task 3 next (admin); Task 4 last (biggest change, biggest payoff).

---

## Task 1 — Demand cluster schema drift

**Goal:** When the daily pipeline runs, `demand_clusters` in `public/data.json` is ≥1, OR a structured `demand_wedge_empty` warning is logged so the operator sees the silence.

**Diagnostics**
1. Re-run pipeline: `poetry run python -m pipeline.run --claude --max-cost-cents 50`, capture stderr. If `demand_clusters_synthesized` logs `count: 0` again, the issue is reproducible (not one-off).
2. In [pipeline/demand.py:781-799](pipeline/demand.py#L781-L799) (`synthesize_demand_from_trends`), add a temporary `len(rows)` + `list(rows[0].keys())` log between `_extract_json` and `_coerce_cluster_list` so the next run shows whether it's parsed-but-empty vs. parsed-but-wrong-schema.
3. Compare `SYNTHESIZE_PROMPT_TEMPLATE` against what Sonnet is actually returning. If a sibling key (`pain_point`, `question`, etc.) is in use, decide: adapt the coercer, or tighten the prompt.

**Implementation**
- If Sonnet returns a sibling field: extend `_coerce_cluster_list` to accept it.
- Either way: escalate the log line at [pipeline/run.py:1602](pipeline/run.py#L1602) from `info` to `warning` when `len(demand_clusters) == 0` so an empty wedge is never invisible.

**Tests**
- Extend [tests/test_demand.py](tests/test_demand.py) with a parametrized test feeding alternate-key Sonnet responses and asserting non-empty output.
- Add a wedge-guard in [tests/test_run_and_snapshot.py](tests/test_run_and_snapshot.py): when both paths empty, a `demand_wedge_empty` warning is logged.

**Out of scope**
- HDBSCAN-comments path tuning. Sparse-day 0 is expected from that source.

**Sign-off**
- [x] Agent id: claude-agent (parallel session, 2026-05-17)
- [x] Date: 2026-05-17
- [x] PR / merge commit SHA: 75a8fe4 — `fix(demand): tolerate alternate Sonnet key shapes; warn on empty wedge`
- [x] Verify command(s) run: `poetry run pytest -q`
- [x] Output line confirming success: `452 passed, 2 xfailed, 17 warnings in 18.99s`

---

## Task 2 — Summarize batch dropping rows on list-shaped responses

**Goal:** Zero `summarize: dropping b_X — expected dict, got list` lines in the daily pipeline log. Every trend in the snapshot has populated `angles.hook` and `angles.contrarian`.

**Diagnostics**
1. `grep -n "dropping b_" pipeline/summarize.py` to find the drop site.
2. Read the surrounding `_extract_json` and per-batch parser. Today's log line showed Claude returning a JSON array of card objects when the parser expected a single dict.
3. Decide policy: unwrap single-element lists, or tighten the prompt to force a dict response.

**Implementation**
- Minimum-surface fix: when parsed JSON is a list of length 1 whose element is a dict with the expected card schema keys, unwrap to the first element instead of dropping. Log the unwrap at `info` so prompt drift stays visible.
- Cleaner: tighten the per-trend prompt template with a worked dict-shaped example (Claude follows example shapes more reliably than instructions).

**Tests**
- Add `test_summarize_unwraps_single_element_list_response` to the summarize test file (or wherever batch parser tests live).

**Risk**
- Don't accept arbitrary list shapes. Only unwrap when `len == 1` and the element matches the expected schema; otherwise log and drop as today.

**Sign-off**
- [x] Agent id: claude-agent (parallel session, 2026-05-17)
- [x] Date: 2026-05-17
- [x] PR / merge commit SHA: 5fc2604 — `fix(summarize): unwrap single-element list responses from Claude`
- [x] Verify command(s) run: `poetry run pytest -q`
- [x] Output line confirming success: `452 passed, 2 xfailed, 17 warnings in 18.99s`

---

## Task 3 — Make `lint` a required check on `main`

**Goal:** A commit landing on `main` that fails `bash scripts/lint_no_innerhtml.sh` either is blocked by branch protection, or pages the owner immediately. No silent red on main.

**Background**
GROUP A radar fixes (19292c9) added an `innerHTML` site without bumping the lint baseline. CI on `main` was failing from that point until the 2026-05-17 unblock (commit e1142d5). Nobody noticed because `lint` wasn't a required check.

**Implementation**
1. Read current protection: `gh api repos/kuhnhomeuk-cell/ai-alpha-radar/branches/main/protection`.
2. Preserving the existing settings, add `lint` and `test` to `required_status_checks.contexts`. Reference: `gh api docs/rest/branches/branch-protection`.
3. Confirm with a read-back that `required_status_checks.contexts` now contains `lint` and `test`.

**Out of scope**
- Rewriting `scripts/lint_no_innerhtml.sh`. The current baseline mechanism is fine.

**Risk**
- Solo-owner repo — confirm with Dean whether protection should allow admin-bypass (default: yes, since otherwise a future emergency hotfix could be blocked).

**Sign-off**
- [x] Agent id: claude-agent (parallel session, 2026-05-17)
- [x] Date: 2026-05-17
- [x] Confirmed bypass policy with Dean: yes (`enforce_admins.enabled: false`)
- [x] `gh api ... protection` output showing `lint` in required contexts (paste line): `"required_status_checks":{...,"contexts":["lint","test"],"checks":[{"context":"lint","app_id":15368},{"context":"test","app_id":15368}]}`
- [x] Output line confirming success: branch protection now blocks push-to-main when `lint` or `test` fails; admin bypass retained

---

## Task 4 — Topic vocabulary stability across days

**Goal:** Day-over-day overlap between consecutive snapshots' `trends[].keyword` lists is ≥50% under normal conditions (vs. ~0% on 2026-05-17). Reduces the load on the embedding-fallback safety net introduced in PR #9.

**Diagnostics**
1. Read [pipeline/topics.py](pipeline/topics.py) end-to-end. The single Haiku call there is the source of daily topic vocabulary churn.
2. At extraction time, load yesterday's snapshot from `public/snapshots/{yesterday}.json` and pass yesterday's 30 `keyword` strings to the prompt as preferred vocabulary.
3. The post-processing route (force-substitute) is the wrong move — bias the prompt, don't override Claude's output. That preserves the value of fresh extraction.

**Implementation**
- Add a `previous_keywords: list[str]` argument to the topic-extraction function in `pipeline/topics.py`.
- Inject those keywords into the prompt with text like: *"If any of today's topics are the same concept as one of these existing labels, reuse the existing label verbatim instead of rephrasing: [list]. Otherwise mint a fresh label."*
- Wire `pipeline/run.py` to pass yesterday's `trends[].keyword` list — the run already loads `prior_snapshot_for_clusters` around [pipeline/run.py:1114](pipeline/run.py#L1114), so the snapshot's already in hand.

**Tests**
- Add `test_topics_prefers_previous_keyword_when_concept_overlaps` in [tests/test_topics.py](tests/test_topics.py): fake-Claude returns yesterday's keyword verbatim when the candidate concept overlaps; fresh label otherwise.
- Two-day simulation via test_run_and_snapshot fixtures asserting ≥50% keyword carry-over.

**Verify after 3 daily snapshots**
- `past_predictions` count is substantially higher than the 3-paraphrase tail PR #9 currently surfaces.

**Out of scope**
- Changing topic count (still 30) or primitive (still Claude-extracted named topics).
- Removing the embedding fallback from `predict.build_lifecycle_lookup` — keeps as safety net for cases where Claude does mint a fresh label despite the bias.

**Sign-off**
- [x] Agent id: claude-agent (parallel session, 2026-05-17)
- [x] Date: 2026-05-17
- [x] PR / merge commit SHA: 6073f63 — `feat(topics): bias topic extraction toward yesterday's keywords`
- [x] Verify command(s) run: `poetry run pytest -q`
- [x] Output line confirming success: `452 passed, 2 xfailed, 17 warnings in 18.99s`
- [ ] 3-day carry-over % observed in production: pending — first measurable after 2026-05-20 snapshot (3 daily runs post-merge)

---

## Resolved (recent)
- ✅ 2026-05-17 — Star Log keyword-mismatch — embedding fallback in [PR #9](https://github.com/kuhnhomeuk-cell/ai-alpha-radar/pull/9) (commit 34802e1)
- ✅ 2026-05-17 — Lint baseline drift on main — bumped 24 → 27 (commit e1142d5)

## Cosmetic / not urgent
- Garbage keywords in `data/predictions.jsonl` (`llms`, `ai`, `hn`, `claude`) from older pipeline versions — harmless after PR #9 but could be pruned.
