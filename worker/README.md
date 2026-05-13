# ai-alpha-radar worker

Cloudflare Worker — CORS proxy + Anthropic deep-dive endpoint + daily spend tracker.

## Local dev

```bash
cd worker
npm install
npm run dev
```

This boots `wrangler dev` on `localhost:8787` with an in-memory KV. Hit:

```bash
curl 'http://localhost:8787/api/spend'
```

## Deploy

You need a Cloudflare account (free is fine).

1. **Auth** — `npx wrangler login`
2. **Create the KV namespace**:
   ```bash
   npx wrangler kv:namespace create RADAR_KV
   npx wrangler kv:namespace create RADAR_KV --preview
   ```
   Paste the returned ids into the `[[kv_namespaces]]` block in `wrangler.toml`.
3. **Set secrets**:
   ```bash
   npx wrangler secret put ANTHROPIC_API_KEY
   npx wrangler secret put SEMANTIC_SCHOLAR_KEY   # optional
   ```
4. **Deploy**:
   ```bash
   npm run deploy
   ```
5. **Confirm**: the deploy output prints `https://ai-alpha-radar.<your-subdomain>.workers.dev`. Hit:
   ```bash
   curl -H 'Origin: https://kuhnhomeuk-cell.github.io' \
        'https://ai-alpha-radar.<sub>.workers.dev/api/spend'
   ```

## Routes

| Method | Path | Notes |
|---|---|---|
| `GET`  | `/proxy/arxiv?query=...` | CORS-stripped arXiv passthrough |
| `GET`  | `/proxy/s2?ids=ARXIV:1706.03762,...` | Semantic Scholar batch |
| `POST` | `/api/deep-dive` | Anthropic Sonnet on-demand, body `{keyword, context?, niche?}` |
| `GET`  | `/api/spend?date=YYYY-MM-DD` | Returns `{spent_cents, cap_cents, remaining_cents}` |

Allowlisted origins: `*.github.io`, `localhost`, `127.0.0.1`. Other origins get 403.

## Cost guardrail

Each successful `/api/deep-dive` adds 1 cent to today's `spend:YYYY-MM-DD` KV row. When the daily total hits `DAILY_SPEND_CAP_CENTS` (default 30), subsequent calls return 429 without hitting Anthropic.
