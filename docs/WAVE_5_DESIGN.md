# Wave 5 — design decisions

Locked-in choices for Addition A (Perplexity pain-points) and Addition B (YouTube outliers).
All decisions made inline against the niche lock: **"AI tools for solo creators"**.

## Addition A — Perplexity Sonar pain-point enrichment

### `PainPoint` schema

```python
class PainPoint(BaseModel):
    text: str            # the question / complaint, ~1 sentence
    source_url: str      # URL of evidence
    source_title: str    # page title (for display chip)
    rank: int = 0        # 1-based; 1 = most important per Sonar's own ranking
```

No `confidence` field. Sonar's order IS the signal; an additional LLM-derived confidence would be noisy and unverifiable. `rank` defaults to 0 so old snapshots round-trip.

### Sonar prompt template

```
What are solo creators struggling with around "{topic}"? List 3-5 unanswered
questions, missing tutorials, or recurring complaints from the past 30 days.
Return JSON only (no prose) as an array of objects with these exact fields:
"text" (the question or complaint, one sentence), "source_url" (URL of
evidence), "source_title" (page title). Cite real, recent sources only.
Order the array from most important to least important.
```

Structured JSON keeps parsing trivial. "solo creators" narrows scope to our niche.

### API details

- Endpoint: `POST https://api.perplexity.ai/chat/completions`
- Model: `sonar` (the cheap online-search model — $0.001/call ballpark for short pain-point queries)
- `response_format: {"type": "json_schema", "json_schema": ...}` if supported, else parse from text fallback
- Synchronous `httpx.Client`, wrapped with `@with_retry(attempts=3, base_delay=1.0, max_delay=30.0)`
- Auth: `Authorization: Bearer ${PERPLEXITY_API_KEY}`
- Reads key via `os.environ.get("PERPLEXITY_API_KEY")`; raises `RuntimeError` if missing (run.py catches and skips enrichment with a logged warning rather than crashing the snapshot)

### Cost tracking

`fetch_pain_points` returns `(list[PainPoint], cost_cents)`. The pipeline-side loop aggregates `cost_cents`, compares against remaining budget in `--max-cost-cents`, and aborts further calls if exhausted. Approximation: ~$0.001 per call × 30 trends = $0.03 = 3 cents/day. Folded into existing 50¢/day budget without strain.

### §5 single-call gate

Before the bulk loop runs over the top-30 trends:

1. Call `fetch_pain_points(topic=trends[0].keyword)` synchronously.
2. Print the raw response JSON, the parsed `PainPoint` objects, and the cost.
3. Halt. Wait for explicit "go" before the bulk loop.

This is non-negotiable per the project owner's §5 rule.

## Addition B — YouTube outliers

### `YoutubeOutlier` schema

```python
class YoutubeOutlier(BaseModel):
    video_id: str
    title: str
    channel_name: str
    view_count: int
    channel_baseline_views: int
    outlier_multiple: float
    published_at: datetime
    thumbnail_url: str
    key_topics: list[str] = []
```

`outlier_multiple = view_count / channel_baseline_views`. Higher = more anomalous.

### VidIQ MCP calls

Primary: `vidiq_outliers(keyword=kw)` per keyword in `data/youtube_keywords.json`.
Fallback: if `vidiq_outliers` is missing data or schema-different, fall back to `vidiq_trending_videos` and synthesize `outlier_multiple` from channel-stat baselines.
`vidiq_keyword_research` is not used in this wave (it's for keyword discovery, not video discovery).

Top-N cap: **30 videos** after dedupe and ranking. Matches the existing top-30 trend cap and keeps the dashboard view glanceable.

### Dedup strategy

Across keywords, the same video can match multiple keywords. On collision, keep the instance with the **highest `outlier_multiple`** (because different keyword filters can produce different baseline calculations from the same MCP). Rank by `outlier_multiple` descending, cap at 30.

### Keywords (seed, kept as brief specified)

```json
[
  "AI tools for creators",
  "ChatGPT tutorial",
  "Claude AI",
  "ComfyUI",
  "AI video",
  "AI agents",
  "Cursor IDE",
  "AI for solo creators",
  "n8n automation"
]
```

### Single-keyword gate

Before fanning out to all 9 keywords:

1. Fire ONE `vidiq_outliers` call against `"AI tools for creators"`.
2. Capture the raw JSON to `tests/fixtures/vidiq_outliers_sample.json`.
3. Print parsed `YoutubeOutlier` objects.
4. Halt. Wait for explicit "go" before the bulk fan-out.

### Nav + dashboard

**6th nav label: "Comets"** — matches celestial / observatory metaphor (Radar, Hidden Gems, Demand Clusters, Star Log, Almanac, **Comets**). Single word. Evokes "fast-moving anomaly" — exactly what an outlier video is.

Route path: `data-page="outliers"` (internal identifier; nav label is "Comets").

**Card layout** — clone `.gems-grid` / `.gem-card` pattern:

- `.outliers-grid { grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); }`
- Each `.outlier-card`:
  - 16:9 thumbnail at top (with `loading="lazy"`)
  - Title (serif, 2-line clamp)
  - Channel name (small-caps, gold accent)
  - Bottom row: view count + outlier-multiple badge (e.g. "12.4× baseline")

**Modal on click** — clone `.conv-scrim` / `.conv-modal` infra:

- Title
- Channel + view-count + outlier-multiple
- "Key topics" chips
- "Use as topic seed" button — copies `title` to clipboard via `navigator.clipboard.writeText`
- "Watch on YouTube" link — `https://www.youtube.com/watch?v=${video_id}`

All interpolations via `escapeHtml()` / `escapeAttr()`. No new `.innerHTML =` sites — use `document.createElement` + `appendChild` exclusively for outliers code.
