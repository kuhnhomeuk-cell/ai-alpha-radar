# AI Alpha Radar — Database Migration Plan

**Status:** DRAFT
**Date:** 2026-05-17
**Owner:** Dean Kuhn
**Reviewer:** (pending — Dean approves before any code lands)

---

## TL;DR

Migrate persistent state from `data/*.json` files in git to a Neon Postgres database. Five phases, each independently shippable, each with dual-writes so the live dashboard never breaks. Total work: 6–10 focused days, spread over 4–6 weeks at a comfortable pace.

**Why:** historical queries, structured store as the source of truth, durability for the gitignored corpus caches, simpler mental model.

**Tech:** Neon serverless Postgres + `asyncpg` Python client. Free tier covers current volume forever. Existing Cloudflare Worker becomes the query API for the dashboard.

**Risk:** Low at every phase. Dual-write means we never break the JSON read path until the DB read path is verified for ≥7 days.

---

## 1. Context

### Current state (today)

- **Schema:** Pydantic models in `pipeline/models.py` (14 classes — `Trend`, `Snapshot`, `Prediction`, `ConvergenceEvent`, `PainPoint`, `DemandCluster`, `YoutubeOutlier`, etc.) — typed, validated, well-defined.
- **Persistence:**
  - `data/*_corpus.json` — per-fetcher accumulating caches (arxiv, HN, GH, HF, Perplexity). **Most are gitignored.**
  - `data/digg_ai_corpus.json` — Digg corpus (Shape B with rank trajectory). **Committed.**
  - `data/bluesky_mentions.sqlite` — only real database in the project. **Gitignored, local-only.**
  - `data/predictions.jsonl` — append-only verdict log. **Committed.**
  - `data/corpus_centroid_60d.npy` — embedding centroid for novelty detection. **Gitignored.**
  - `public/data.json` — denormalized daily snapshot. **Committed.** Served by Vercel CDN. Read by dashboard via `fetch('./data.json')`.
- **Pipeline:** runs daily at 06:00 UTC via `.github/workflows/daily-snapshot.yml`. Reads JSON, computes scores, writes JSON, commits + pushes results back to `main`.
- **Dashboard:** static HTML/CSS/JS in `public/index.html`. Fetches `./data.json`, parses, filters in-memory.

### Pain points motivating this migration

| Concern | Concrete failure mode today |
|---|---|
| **Historical queries** | "Show me trends that were whisper-stage on 2026-04-10" — impossible without `git checkout` to that commit. The dashboard can only show today. |
| **Structured store on scrape** | Each fetcher writes its own JSON file. No transactional guarantees — a mid-run crash can leave partial state across files. |
| **Scale / loss / corruption** | Most corpus caches are **gitignored — local only**. If the laptop running the pipeline dies, those caches are gone. Some sources can't backfill (Reddit, Digg). |
| **Architecture clarity** | Two corpus shapes (A & B), one SQLite file, one JSONL, one NPY, one denormalized JSON. Each fetcher has its own write path. Mental load to keep straight. |

### What success looks like

1. Every fetched observation lands in a queryable structured store within seconds of being fetched.
2. The dashboard can query historical state (any trend's velocity / saturation / stage at any past date).
3. Pipeline runs are recoverable from a single source of truth — no laptop-local state.
4. The JSON snapshot (`public/data.json`) becomes a **cache** (regenerated nightly), not the source of truth.
5. Schema migrations are versioned and auditable.

---

## 2. Target stack

**Database:** Neon Postgres (https://neon.tech)
**Python client:** `asyncpg` (already battle-tested with this kind of pipeline)
**Migration tool:** raw SQL files in `migrations/` (no ORM, no Alembic — overkill for our scale)
**Query API:** existing Cloudflare Worker at `worker/src/index.ts` — adds `/api/trends`, `/api/leaderboard`, `/api/history`, etc.

### Why Neon over alternatives

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Neon** (chosen) | Real Postgres, free tier (0.5GB + 5h compute/mo) fits our shape forever, direct asyncpg writes from pipeline, point-in-time recovery, no vendor lock-in (export anytime) | New service to manage; cold starts on free tier (~1s wake) | ✅ Best fit |
| Cloudflare D1 | Already in CF ecosystem, native Worker bindings | SQLite limitations (no JSONB GIN indexes, no window functions in older versions), writes must go through Worker HTTPS hop | ❌ Wrong write-path shape for our Python pipeline |
| Supabase | Postgres + REST + auth | More than we need (we have no users), more setup | ❌ Overkill |
| Turso (libSQL) | Edge-replicated SQLite | Still SQLite limitations | ❌ Same as D1 |
| Stay on JSON files | $0/mo, simple | All the pain points above | ❌ Doesn't meet success criteria |

### Cost projection

- **Neon free tier:** 0.5 GB storage, 5 hours compute/mo, 90-day point-in-time recovery, automatic backups.
- **Our usage at current scale:** ~150 KB/day of new mentions × 365 days = ~55 MB/year. Compute is bursty (pipeline runs ~10 min/day → 5h/mo budget covers ~30 days of daily runs). **We fit free tier indefinitely.**
- **If we ever exceed free tier:** Neon Launch plan is $19/mo for 10 GB + autoscaling compute. We'd hit this after ~50 years at current scale.

---

## 3. Schema

Tables map closely to existing Pydantic models. Indexes target the expected query patterns (filter by date, by stage, by cluster_id).

### 3.1 `trends`

Current state of each tracked keyword. ONE row per keyword (UPSERT on daily run).

```sql
CREATE TABLE trends (
  id              BIGSERIAL PRIMARY KEY,
  keyword         TEXT NOT NULL UNIQUE,
  canonical_form  TEXT NOT NULL,
  cluster_id      INTEGER,
  cluster_label   TEXT,
  lifecycle_stage TEXT NOT NULL CHECK (lifecycle_stage IN ('whisper','builder','creator','hype','commodity')),
  saturation         REAL NOT NULL,
  velocity_score     REAL NOT NULL,
  velocity_acceleration REAL,
  tbts               REAL NOT NULL,
  hidden_gem_score   REAL NOT NULL,
  builder_signal     REAL NOT NULL,
  source_counts      JSONB NOT NULL,           -- SourceCounts payload
  convergence        JSONB NOT NULL,           -- ConvergenceEvent payload
  summary            TEXT,
  summary_confidence TEXT CHECK (summary_confidence IN ('high','medium','low')),
  angles             JSONB,                    -- CreatorAngles payload
  risk               JSONB,                    -- RiskFlag payload
  prediction         JSONB,                    -- Prediction payload
  sparkline_14d      JSONB,                    -- list of floats
  pain_points        JSONB,                    -- list of PainPoint payloads
  sources_confirming JSONB,                    -- list of SourceName strings
  consensus_ratio    REAL,
  first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX trends_stage_idx       ON trends (lifecycle_stage);
CREATE INDEX trends_cluster_idx     ON trends (cluster_id);
CREATE INDEX trends_tbts_idx        ON trends (tbts DESC);
CREATE INDEX trends_updated_idx     ON trends (last_updated_at DESC);
```

### 3.2 `trend_history`

Append-only daily snapshot of each trend's metrics. ENABLES historical queries.

```sql
CREATE TABLE trend_history (
  id              BIGSERIAL PRIMARY KEY,
  trend_id        BIGINT NOT NULL REFERENCES trends(id) ON DELETE CASCADE,
  snapshot_date   DATE NOT NULL,
  saturation      REAL NOT NULL,
  velocity_score  REAL NOT NULL,
  tbts            REAL NOT NULL,
  hidden_gem_score REAL NOT NULL,
  lifecycle_stage TEXT NOT NULL,
  source_counts   JSONB NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (trend_id, snapshot_date)
);

CREATE INDEX trend_history_date_idx ON trend_history (snapshot_date DESC);
CREATE INDEX trend_history_trend_idx ON trend_history (trend_id, snapshot_date DESC);
```

### 3.3 `mentions`

Per-source raw observations. Replaces the corpus JSON caches.

```sql
CREATE TABLE mentions (
  id            BIGSERIAL PRIMARY KEY,
  source        TEXT NOT NULL CHECK (source IN ('arxiv','github','hackernews','semantic_scholar','youtube','reddit','grok_x','tiktok','digg','huggingface','perplexity','bluesky','producthunt','replicate','newsletters')),
  doc_id        TEXT NOT NULL,                -- arXiv ID, HN post ID, repo full_name, etc.
  first_seen_at TIMESTAMPTZ NOT NULL,
  last_seen_at  TIMESTAMPTZ NOT NULL,
  observation_count INTEGER NOT NULL DEFAULT 1,
  payload       JSONB NOT NULL,               -- verbatim fetcher payload, latest version
  trajectory    JSONB,                        -- optional: array of {ts, rank, views, ...} for rank-tracking sources (Digg, Replicate)
  UNIQUE (source, doc_id)
);

CREATE INDEX mentions_source_seen_idx ON mentions (source, last_seen_at DESC);
CREATE INDEX mentions_payload_gin     ON mentions USING GIN (payload jsonb_path_ops);
```

### 3.4 `clusters`

UMAP+HDBSCAN cluster identity with centroid embedding.

```sql
CREATE TABLE clusters (
  id              INTEGER PRIMARY KEY,
  label           TEXT NOT NULL,
  centroid        REAL[] NOT NULL,            -- 384-d MiniLM embedding (Postgres array)
  member_count    INTEGER NOT NULL,
  first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 3.5 `predictions`

Append-only verdict log. Replaces `data/predictions.jsonl`.

```sql
CREATE TABLE predictions (
  id              BIGSERIAL PRIMARY KEY,
  keyword         TEXT NOT NULL,
  target_lifecycle TEXT NOT NULL,
  filed_at        TIMESTAMPTZ NOT NULL,
  target_date     DATE,
  verdict         TEXT NOT NULL CHECK (verdict IN ('pending','tracking','verified','verified_early','wrong')),
  verdict_at      TIMESTAMPTZ,
  rationale       TEXT,
  payload         JSONB,                       -- full Prediction model dump
  UNIQUE (keyword, target_lifecycle, filed_at)
);

CREATE INDEX predictions_keyword_idx ON predictions (keyword);
CREATE INDEX predictions_verdict_idx ON predictions (verdict);
```

### 3.6 `convergence_events`

When convergence is detected for a trend.

```sql
CREATE TABLE convergence_events (
  id              BIGSERIAL PRIMARY KEY,
  trend_id        BIGINT NOT NULL REFERENCES trends(id) ON DELETE CASCADE,
  detected_at     TIMESTAMPTZ NOT NULL,
  payload         JSONB NOT NULL              -- full ConvergenceEvent payload
);

CREATE INDEX convergence_trend_idx ON convergence_events (trend_id, detected_at DESC);
```

### 3.7 `sources_health`

Daily fetch_health snapshot per source.

```sql
CREATE TABLE sources_health (
  id              BIGSERIAL PRIMARY KEY,
  snapshot_date   DATE NOT NULL,
  source          TEXT NOT NULL,
  is_ok           BOOLEAN NOT NULL,
  observation_count INTEGER NOT NULL,
  error_message   TEXT,
  UNIQUE (snapshot_date, source)
);

CREATE INDEX sources_health_date_idx ON sources_health (snapshot_date DESC);
```

### 3.8 `outliers` (YouTube outliers / Comets)

Replaces `data/youtube_outliers.json`.

```sql
CREATE TABLE outliers (
  id              BIGSERIAL PRIMARY KEY,
  video_id        TEXT NOT NULL UNIQUE,
  title           TEXT NOT NULL,
  channel         TEXT,
  views           BIGINT,
  baseline_views  BIGINT,
  breakout_factor REAL,
  thumbnail_url   TEXT,
  payload         JSONB NOT NULL,             -- full YoutubeOutlier payload
  first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX outliers_breakout_idx ON outliers (breakout_factor DESC);
```

### 3.9 `daily_snapshots`

Full denormalized daily snapshot, archived. ENABLES replay + audit.

```sql
CREATE TABLE daily_snapshots (
  snapshot_date   DATE PRIMARY KEY,
  payload         JSONB NOT NULL,             -- the full Snapshot model dump (the same shape as public/data.json)
  trend_count     INTEGER NOT NULL,
  source_health   JSONB NOT NULL,
  generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 3.10 `audience_pain_points`

Perplexity-enriched pain points per trend.

```sql
CREATE TABLE audience_pain_points (
  id              BIGSERIAL PRIMARY KEY,
  trend_id        BIGINT NOT NULL REFERENCES trends(id) ON DELETE CASCADE,
  pain_text       TEXT NOT NULL,
  sources         JSONB,                       -- list of source URLs / IDs
  enriched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX pain_points_trend_idx ON audience_pain_points (trend_id);
```

### Foreign key + lifecycle notes

- `trend_history`, `convergence_events`, `audience_pain_points` cascade on trend deletion (we never expect trend deletion in practice, but it's correct).
- `daily_snapshots.payload` is the **denormalized cache** — same shape as `public/data.json` today. Useful for: replaying old dashboard states; verifying parity during migration; archive.

---

## 4. Migration phases

Each phase is independently shippable as a PR. Dashboard keeps working at every phase.

### Phase 0 — Setup (no code changes to pipeline)

- [ ] Sign up for Neon (or confirm Dean already has an account)
- [ ] Create project: `ai-alpha-radar`
- [ ] Get connection string → store as `DATABASE_URL` in:
  - GitHub Actions repo secret (for cron)
  - `.env.local` (for local pipeline runs)
- [ ] Add `asyncpg` to `pyproject.toml`, run `poetry lock && poetry install`
- [ ] Create `migrations/` directory + `migrations/001_initial_schema.sql` with all DDL from §3
- [ ] Create `pipeline/db.py` — `get_pool()` + `run_migrations()` helpers
- [ ] Run migration manually (or via a one-off CI workflow) to provision tables
- [ ] Verify: `SELECT table_name FROM information_schema.tables WHERE table_schema='public'` returns expected 10 tables

**Verify by running:** `poetry run python -c "import asyncpg, asyncio; asyncio.run(asyncpg.connect('$DATABASE_URL').close())"` → exits 0.

**Risk:** Zero (no pipeline code changes).
**Rollback:** Drop Neon project. Remove `asyncpg` from poetry.

### Phase 1 — Dual-write `daily_snapshots` (smallest table, biggest signal)

- [ ] In `pipeline/snapshot.py`, after the existing `public/data.json` write, also `INSERT INTO daily_snapshots (snapshot_date, payload, trend_count, source_health) VALUES (...) ON CONFLICT (snapshot_date) DO UPDATE SET payload = EXCLUDED.payload, ...`
- [ ] Add a single test in `tests/test_db_dual_write.py` that mocks asyncpg and asserts the INSERT is called
- [ ] Deploy. Daily cron runs.
- [ ] After 3 daily runs, verify row count = run count, and `payload::text` matches `public/data.json` for the corresponding date.

**Verify by running:** `SELECT snapshot_date, trend_count FROM daily_snapshots ORDER BY snapshot_date DESC LIMIT 7;` — should match the last 7 days of dashboard snapshots.

**Risk:** Low (additive write, no read-path change).
**Rollback:** revert the INSERT call. JSON write keeps working.

### Phase 2 — Dual-write corpus tables (`mentions`)

- [ ] Replace each fetcher's `data/*_corpus.json` write with `INSERT INTO mentions ... ON CONFLICT (source, doc_id) DO UPDATE SET last_seen_at=NOW(), observation_count = mentions.observation_count + 1, payload = EXCLUDED.payload`
- [ ] **Keep** the JSON write for now (dual-write). Add a feature flag `AAR_CORPUS_BACKEND=json|both|db` (default `both`).
- [ ] For the Digg fetcher specifically, populate the `trajectory` column with the array of rank/views observations
- [ ] After 7 days of `both` writes, verify row counts match JSON entry counts per source
- [ ] Flip flag to `db` for read-back in `pipeline/run.py` (via `_hydrate_from_corpus`)
- [ ] After 7 days of DB-only reads, remove the JSON fallback for corpus files

**Verify by running:** `SELECT source, COUNT(*), MAX(last_seen_at) FROM mentions GROUP BY source` — should match `data/<source>_corpus.json` row counts.

**Risk:** Medium (touches every fetcher in `pipeline/fetch/`).
**Rollback:** flip feature flag to `json`, restore JSON-only path. DB rows stay; harmless.

### Phase 3 — Dual-write `trends` + `trend_history`

- [ ] In `pipeline/run.py`, after scoring/clustering, UPSERT each Trend row to `trends`
- [ ] Also INSERT a row into `trend_history` with today's metrics
- [ ] Same dual-write pattern: keep `public/data.json` write
- [ ] Add migration verification: for the last 7 daily snapshots, `daily_snapshots.payload->'trends'` should be reconstructible from `trends` + `trend_history` for that date

**Verify by running:** a Python script that compares the last 7 daily snapshots' trend arrays against the DB reconstruction.

**Risk:** Medium (touches core orchestrator).
**Rollback:** stop the DB UPSERTs. Trends keep coming from JSON.

### Phase 4 — Cut the dashboard read path

- [ ] In `worker/src/index.ts`, add Neon HTTP driver (`@neondatabase/serverless`)
- [ ] Add endpoints:
  - `GET /api/trends?stage=whisper&limit=30` — current trends with filters
  - `GET /api/leaderboard?sort=tbts&limit=20` — top N by metric
  - `GET /api/history?keyword=...&days=30` — historical trajectory for one trend
  - `GET /api/snapshot?date=2026-05-10` — full denormalized snapshot for a past date (from `daily_snapshots`)
  - `GET /api/sources_health?days=30` — fetch health timeline
- [ ] Add edge caching (10-min TTL on `/api/trends`, 1-min on `/api/leaderboard`, 1-hour on `/api/history` & `/api/snapshot`)
- [ ] Update `public/index.html` to fetch from Worker endpoints instead of `./data.json`
- [ ] Keep `./data.json` fetch as fallback for first-paint speed (Worker can be slow on cold-start)

**Verify by running:** dashboard works as before. New: Star Log shows real verdict history; Sky Map can show "compare to 30 days ago"; source-health bar shows trend over last week.

**Risk:** Medium (touches frontend). Easy to revert (the JSON fallback stays).
**Rollback:** delete the Worker endpoints + revert the dashboard fetch URLs.

### Phase 5 — Remove JSON fallbacks

- [ ] After 30 days of stable Phase 4, remove dual-write JSON writes from pipeline
- [ ] `public/data.json` becomes a denormalized cache regenerated nightly from `daily_snapshots` (so it still exists for offline / first-paint, but DB is source of truth)
- [ ] Remove `data/*_corpus.json`, `data/youtube_outliers.json`, `data/predictions.jsonl` from the repo (preserve git history). Keep `data/bluesky_mentions.sqlite` as-is (it's its own thing, low-priority migration).
- [ ] Update `data/README.md` to document the new architecture

**Verify by running:** dashboard works for 30 days post-cutover with zero JSON-fallback hits in Worker logs.

**Risk:** Low (just removal). Reversible from git history if needed.
**Rollback:** restore the JSON files from git history. Restore the dual-write code.

---

## 5. Dual-write strategy

Every phase that introduces DB writes follows this pattern:

```python
# Pattern: write to JSON first (existing behavior), then DB (new), don't fail if DB write fails

async def persist_thing(payload):
    # 1. Existing JSON write
    write_json_file(payload)

    # 2. New DB write — best-effort, never fails the pipeline
    try:
        async with get_pool().acquire() as conn:
            await conn.execute("INSERT INTO ...", payload)
    except Exception as e:
        log.warning("db_dual_write_failed", source=source, error=str(e))
        # JSON write succeeded, so pipeline is OK
```

**Invariants:**
- The pipeline NEVER fails because of a DB error during dual-write phases.
- Every dual-write phase has a feature flag (`AAR_<SCOPE>_BACKEND=json|both|db`) so we can flip behavior without a redeploy.
- After 7+ days of `both`, we run a parity verification script before flipping to `db`-only read.

---

## 6. Verification & rollback

### Per-phase parity checks

| Phase | Parity query |
|---|---|
| 1 | `SELECT COUNT(*) FROM daily_snapshots` vs. count of git commits to `public/data.json` |
| 2 | `SELECT source, COUNT(*) FROM mentions GROUP BY source` vs. row counts in `data/*_corpus.json` |
| 3 | reconstruct yesterday's snapshot from `trends` + `trend_history`; compare to `public/data.json` |
| 4 | Worker `/api/trends` response vs. `./data.json` `trends` array (should match byte-for-byte modulo ordering) |

### Rollback playbook

Every phase has a rollback. The general pattern:

1. **Phase 1–3 (dual-write):** revert the DB write call. JSON writes keep working. DB rows are harmless.
2. **Phase 4 (read cutover):** revert the dashboard fetch URLs. JSON file is still on Vercel CDN. Worker endpoints stay (unused) until cleanup.
3. **Phase 5 (removal):** restore the JSON files from git history. Restore the dual-write code from before-the-removal commit.

**Worst case:** Neon outage. Mitigation: the JSON fallback at every phase (until Phase 5) means we degrade gracefully — dashboard reads from `public/data.json` cache, pipeline writes to JSON, we re-sync to DB when Neon is back.

---

## 7. Operational concerns

### Cron + DB connection

The daily cron in GH Actions needs `DATABASE_URL` as a repo secret. Add via:
```bash
gh secret set DATABASE_URL --body "$NEON_CONNECTION_STRING"
```

### Connection pooling

Neon supports connection pooling at the project level. We use:
- Pipeline (GH Actions): direct connection, single asyncpg pool, max 5 connections
- Worker: Neon HTTP driver (`@neondatabase/serverless`) which uses Neon's connection pooler — no pool exhaustion risk

### Schema versioning

`migrations/` directory. Each migration is a numbered SQL file (`001_initial_schema.sql`, `002_add_outliers_index.sql`, ...). On pipeline start, `pipeline/db.py:run_migrations()` checks the `schema_migrations` table and applies any pending files. Standard pattern.

### Secrets

`DATABASE_URL` lives in:
- `.env.local` (gitignored)
- GH Actions repo secret (`DATABASE_URL`)
- Cloudflare Worker environment variable (set via `wrangler secret put DATABASE_URL`)

The placeholder currently in `.env.local` (`postgresql://...your-string-here...`) gets replaced with the real Neon DSN at Phase 0.

---

## 8. Open decisions (need Dean's call before code starts)

1. **Neon region.** Closest options: `aws-eu-west-1` (Ireland) or `aws-us-east-1` (Virginia). GH Actions runners are mostly in `us-east-1`. Recommendation: **`aws-us-east-1`** to minimize cron latency. Confirm?
2. **Vector embeddings.** Current centroid is stored as `corpus_centroid_60d.npy` (NumPy file). Postgres can store `REAL[]` for arrays, but for vector similarity queries we'd want `pgvector` (Neon supports it). Should we migrate the centroid + cluster embeddings to `pgvector` now, or defer to a later phase? Recommendation: **defer** — current use of centroid is one-shot per pipeline run, not query-time. Confirm?
3. **Bluesky SQLite file.** It's its own ecosystem (high-volume firehose) and works fine. Migrate it to Postgres in Phase 5, or leave it as SQLite indefinitely? Recommendation: **leave as SQLite** — different write pattern (real-time vs. batch), and Postgres free tier compute would burn through 5h/mo on the firehose write volume. Confirm?
4. **Predictions append-only file.** Currently `data/predictions.jsonl` is committed. After migration, do we delete it from the repo (DB is source of truth) or keep it as a JSONL backup (for portability)? Recommendation: **delete after 30 days of Phase 5 stability** — DB has it, git history has it. Confirm?
5. **Schedule.** Aggressive (ship Phase 0–2 in one week) or comfortable (one phase per 1–2 weeks, total 6–10 weeks)? Recommendation: **comfortable** — no production pressure, dual-write means every step is reversible, and you've got time to observe each phase before committing to the next.

---

## 9. Out of scope (deferred to later)

- **Authentication / multi-user.** Solo creator product — no users to authenticate.
- **Real-time updates.** Dashboard refreshes once per day (matches the 06:00 UTC cron). If we ever want sub-daily refreshes, that's a different architecture conversation.
- **Analytics dashboard for the data itself.** "How has my pipeline performance trended over the last quarter" — possible from `sources_health` table but not a Phase 1–5 deliverable.
- **Migration of `data/bluesky_mentions.sqlite`** — see decision #3 above.
- **Read replicas / multi-region.** At current scale we don't need them. Neon supports them on paid tiers if we ever do.

---

## 10. References

- Neon docs: https://neon.tech/docs
- asyncpg docs: https://magicstack.github.io/asyncpg/current/
- `@neondatabase/serverless` (Worker driver): https://github.com/neondatabase/serverless
- Project decisions log: `memory/decisions.md` (specifically 2026-05-13 "Stack locked")
- Data layer README: `data/README.md`
- Pydantic models (the schema source-of-truth): `pipeline/models.py`

---

## 11. Sign-off

Once Dean approves this plan:
- [ ] Open answers to §8 (Open decisions) added to this file
- [ ] Phase 0 work begins — Neon project provisioned, `DATABASE_URL` in `.env.local` + GH Actions secret, `migrations/001_initial_schema.sql` written
- [ ] PR for Phase 0 ships as the first deliverable

This doc lives at `docs/DATABASE_MIGRATION.md` and is the single source of truth for the migration. Update it as decisions land or scope shifts.
