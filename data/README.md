# `data/` — pipeline data inventory

What lives in this folder, what writes it, and what reads it. Files split
into three categories: **config** (curated, committed), **corpus caches**
(write-through accumulation per source, local), and **state files**
(rolling pipeline state).

If you add a new fetcher, decide upfront whether its cache belongs in
the *digg-style* or *generic* corpus pattern (see below) and pick one
— don't invent a third shape.

---

## Config (curated, committed)

| File | Purpose | Owner |
|---|---|---|
| `bluesky_handles.json` | List of Bluesky handles whose every post enters the SQLite cache regardless of keyword. Currently `[]`. | operator |
| `bluesky_keywords.json` | Short AI keyword list the Jetstream subscriber filters on (and the orchestrator queries SQLite by, after [B.2](#)). 13 terms: `llm`, `claude`, `agent`, etc. | operator |
| `newsletters.json` | RSS feeds for cross-mention aggregation. `{name, feed_url}` pairs. | operator |
| `reddit_subreddits.json` | Subreddits scraped by `pipeline/fetch/reddit.py` (currently blocked by datacenter 403 wall). | operator |
| `term_aliases.json` | Normalized keyword aliases for matching. Currently `{}`. | operator |
| `youtube_keywords.json` | Search terms for `pipeline/fetch/youtube_outliers.py`. | operator |
| `youtube_outliers.json` | **Processed** YouTube outlier cache. Refreshed by `scripts/refresh_youtube_outliers.py` (operator). Read by `run.py`. | committed snapshot |

## Corpus caches (write-through, **local-only**)

Two distinct shapes coexist. Both serve the same purpose — "don't lose
fetch work" — but accumulate different per-observation detail.

### Shape A — generic (timestamps-only)

**Files:** `arxiv_corpus.json`, `hackernews_corpus.json`, `github_corpus.json`, `huggingface_corpus.json`, `perplexity_corpus.json`

**Writer:** `pipeline.persist.update_corpus(source, items, id_field=...)`

**Reader:** `pipeline.persist.load_recent_corpus(source, lookback_days=N)` → list of `data` payloads; also `pipeline.run._hydrate_from_corpus(source, ModelCls, ...)` rehydrates into Pydantic models when a live fetch returns empty.

**Shape:**
```json
{
  "<doc_id>": {
    "first_seen": "2026-05-16T12:00:00+00:00",
    "observations": ["2026-05-16T12:00:00+00:00", "2026-05-17T12:00:00+00:00"],
    "data": { "...verbatim fetcher payload..." }
  }
}
```

Each repeat fetch appends a timestamp to `observations` and refreshes `data` to the latest payload. No per-observation detail beyond when we saw it.

### Shape B — rich (rank trajectory)

**File:** `digg_ai_corpus.json` (committed — historical exception, predates the generic pattern)

**Writer:** `pipeline.fetch.digg.update_corpus(stories)`

**Reader:** `pipeline.fetch.digg.load_recent_corpus_stories(lookback_days=N)` → list of full corpus entries.

**Shape:**
```json
{
  "created_at": "<first-ever fetch>",
  "last_refresh": "<most recent fetch>",
  "story_count": 23,
  "stories": {
    "<slug>": {
      "story_id": "<slug>",
      "title": "...",
      "excerpt": "...",
      "story_url": "https://digg.com/ai/<slug>",
      "first_seen": "2026-05-16T12:00:00+00:00",
      "last_seen": "2026-05-17T12:00:00+00:00",
      "observations": [
        {"ts": "...", "rank": 3, "views": "55.1k", "timestamp_relative": "2h", "snapshot_date": "May 16, 2026"}
      ]
    }
  }
}
```

The per-observation array carries `rank` and `views` at each fetch — preserves trajectory so a "Digg rank velocity" analysis can be added later without re-scraping history.

### Why two shapes

Shape B was implemented first (for Digg) and is appropriate where the
*ranking* itself is the signal — rank-3-today, rank-12-tomorrow is meaningful.
Shape A was added second (for arxiv/HN/GH/HF/Perplexity) where the payload
is a fact that doesn't have a ranking dimension we care about (an arXiv
paper is the same paper; HN points drift but we don't track per-day points
yet).

If a future fetcher needs rank trajectory, extend `pipeline/persist.py`
with an optional `observation_fields` arg rather than copying the digg shape.

### Read-fallback semantics

When a free fetcher (arxiv/HN/GH/HF) returns `[]` from its live call,
`run.py` calls `_hydrate_from_corpus(source, ModelCls, lookback_days=...)`
and uses the hydrated list for the rest of the run. `fetch_health[source]`
is set to `True` so the source still counts toward `MIN_OK_SOURCES`.
Digg has its own equivalent (`load_recent_corpus_stories`) wired the
same way.

`bluesky_mentions.sqlite` is its own world (high-volume firehose, denser
schema, dedicated reader path).

## State files

| File | Purpose |
|---|---|
| `bluesky_mentions.sqlite` | Cumulative Bluesky firehose cache. Written by the standalone subscriber CLI (`python -m pipeline.fetch.bluesky`), read by `run.py` via `bluesky.read_mention_counts`. **gitignored** — local-only. |
| `corpus_centroid_60d.npy` | 60-day rolling centroid of cluster embeddings. Refreshed by `pipeline/novelty.py`. **gitignored**. |
| `predictions.jsonl` | Append-only log of trend predictions. Each daily run loads + updates verdicts + appends new (deduped on `keyword + target_lifecycle`). **Committed** so verdict history survives clones. |
| `.batch_state.json` | Claude Batch API state cursor. **gitignored**. |

## What's gitignored

See `.gitignore`. Summary:
- `data/*_corpus.json` (with `!data/digg_ai_corpus.json` exception — committed for now)
- `data/bluesky_mentions.sqlite`
- `data/corpus_centroid_60d.npy`
- `data/replicate_run_counts.json`
- `data/.batch_state.json`
- `data/youtube_outliers_raw.json`

Everything else in `data/` is committed.
