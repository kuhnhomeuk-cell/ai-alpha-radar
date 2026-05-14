/**
 * AI Alpha Radar — Cloudflare Worker (CORS + AI proxy + spend tracker)
 *
 * Per BACKEND_BUILD §7 Step 13. Routes:
 *   GET  /proxy/arxiv?query=...          → arxiv.org/api/query (CORS-strip)
 *   GET  /proxy/s2?ids=ARXIV:...         → Semantic Scholar batch
 *   POST /api/deep-dive                  → Anthropic Sonnet (on-demand)
 *   GET  /api/spend?date=YYYY-MM-DD      → today's cumulative spend in cents
 *
 * Allowlist: *.github.io + localhost. Browser previews from other origins are
 * rejected with a stripped CORS header (browser will block the response).
 *
 * The deep-dive endpoint enforces a hard daily spend cap (default 30 cents)
 * tracked in KV. Cap exceeded → 429, no Anthropic call.
 */

export interface Env {
  ANTHROPIC_API_KEY: string;
  XAI_API_KEY?: string;
  SEMANTIC_SCHOLAR_KEY?: string;
  ANTHROPIC_MODEL_HAIKU: string;
  ANTHROPIC_MODEL_SONNET: string;
  DAILY_SPEND_CAP_CENTS: string;
  RADAR_KV: KVNamespace;
}

const ALLOWED_ORIGIN_PATTERNS: RegExp[] = [
  /^https:\/\/[a-z0-9-]+\.github\.io$/i,
  /^http:\/\/localhost:\d+$/i,
  /^http:\/\/127\.0\.0\.1:\d+$/i,
];

function isAllowedOrigin(origin: string | null): origin is string {
  if (!origin) return false;
  return ALLOWED_ORIGIN_PATTERNS.some((re) => re.test(origin));
}

function corsHeaders(origin: string | null): HeadersInit {
  const allowOrigin = isAllowedOrigin(origin) ? origin : "";
  return {
    "Access-Control-Allow-Origin": allowOrigin,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}

function jsonResponse(body: unknown, init: ResponseInit, origin: string | null): Response {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: {
      ...corsHeaders(origin),
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
}

function todayUtcIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

async function getSpendCents(env: Env, day: string): Promise<number> {
  const v = await env.RADAR_KV.get(`spend:${day}`);
  return v ? parseInt(v, 10) : 0;
}

async function addSpendCents(env: Env, day: string, delta: number): Promise<number> {
  const current = await getSpendCents(env, day);
  const next = current + delta;
  // 7-day TTL — spend rows are only useful for current-day decisions and
  // recent dashboard analytics.
  await env.RADAR_KV.put(`spend:${day}`, String(next), { expirationTtl: 60 * 60 * 24 * 7 });
  return next;
}

// ---------- /proxy/arxiv ----------

async function proxyArxiv(url: URL, origin: string | null): Promise<Response> {
  const query = url.searchParams.get("query") || "";
  if (!query) {
    return jsonResponse({ error: "missing query param" }, { status: 400 }, origin);
  }
  const target = new URL("https://export.arxiv.org/api/query");
  for (const [k, v] of url.searchParams.entries()) {
    target.searchParams.set(k, v);
  }
  const upstream = await fetch(target.toString(), {
    headers: { "User-Agent": "ai-alpha-radar-worker/0.1" },
  });
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: {
      ...corsHeaders(origin),
      "Content-Type": upstream.headers.get("Content-Type") || "application/xml",
    },
  });
}

// ---------- /proxy/s2 ----------

async function proxyS2(url: URL, env: Env, origin: string | null): Promise<Response> {
  const ids = url.searchParams.get("ids");
  if (!ids) {
    return jsonResponse({ error: "missing ids param" }, { status: 400 }, origin);
  }
  const fields = url.searchParams.get("fields") ||
    "citationCount,influentialCitationCount,referenceCount";
  const target = new URL("https://api.semanticscholar.org/graph/v1/paper/batch");
  target.searchParams.set("fields", fields);
  const headers: HeadersInit = { "Content-Type": "application/json" };
  if (env.SEMANTIC_SCHOLAR_KEY) headers["x-api-key"] = env.SEMANTIC_SCHOLAR_KEY;
  const upstream = await fetch(target.toString(), {
    method: "POST",
    headers,
    body: JSON.stringify({ ids: ids.split(",") }),
  });
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: {
      ...corsHeaders(origin),
      "Content-Type": "application/json",
    },
  });
}

// ---------- /api/deep-dive ----------

interface DeepDiveRequest {
  keyword: string;
  context?: string;
  niche?: string;
}

const DEEP_DIVE_SYSTEM =
  "You are AI Alpha Radar's deep-dive analyst. Given a trend keyword, " +
  "produce a tight 2-3 paragraph briefing for a YouTube Shorts creator: " +
  "what the trend is, why it matters now, two concrete content angles. " +
  "Be punchy, no jargon, no fluff. Niche: {niche}.";

// Audit 4.1 — refuse anything that would cost more than ~50 cents to forward.
// 5000 bytes ≈ 1250 tokens, well under Sonnet's 200k window but already large
// for a deep-dive prompt where 200 tokens is plenty.
const MAX_BODY_BYTES = 5000;
const MAX_ESTIMATED_INPUT_TOKENS = 200;

async function deepDive(req: Request, env: Env, origin: string | null): Promise<Response> {
  const day = todayUtcIsoDate();
  const cap = parseInt(env.DAILY_SPEND_CAP_CENTS || "30", 10);
  const current = await getSpendCents(env, day);
  if (current >= cap) {
    return jsonResponse(
      { error: "daily spend cap reached", cap_cents: cap, spent_cents: current },
      { status: 429 },
      origin,
    );
  }
  const contentLength = parseInt(req.headers.get("content-length") ?? "0", 10);
  if (contentLength > MAX_BODY_BYTES) {
    return jsonResponse(
      { error: "payload too large", limit_bytes: MAX_BODY_BYTES },
      { status: 413 },
      origin,
    );
  }
  let body: DeepDiveRequest;
  let rawText: string;
  try {
    rawText = await req.text();
    body = JSON.parse(rawText) as DeepDiveRequest;
  } catch {
    return jsonResponse({ error: "invalid JSON body" }, { status: 400 }, origin);
  }
  // Belt-and-suspenders against missing/spoofed content-length: re-check the
  // actual byte length and estimate the token cost the body would impose.
  if (rawText.length > MAX_BODY_BYTES) {
    return jsonResponse(
      { error: "payload too large", limit_bytes: MAX_BODY_BYTES },
      { status: 413 },
      origin,
    );
  }
  const estimatedInputTokens = Math.ceil(rawText.length / 4);
  if (estimatedInputTokens > MAX_ESTIMATED_INPUT_TOKENS) {
    return jsonResponse(
      { error: "request too large", estimated_tokens: estimatedInputTokens },
      { status: 413 },
      origin,
    );
  }
  if (!body.keyword) {
    return jsonResponse({ error: "missing keyword" }, { status: 400 }, origin);
  }
  const niche = body.niche || "AI tools for solo creators";
  const userPrompt =
    `Trend keyword: ${body.keyword}\n` +
    (body.context ? `Context: ${body.context}\n` : "") +
    "Write the deep-dive briefing now.";

  const upstream = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": env.ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: env.ANTHROPIC_MODEL_SONNET,
      max_tokens: 600,
      system: DEEP_DIVE_SYSTEM.replace("{niche}", niche),
      messages: [{ role: "user", content: userPrompt }],
    }),
  });

  const upstreamBody = await upstream.text();
  if (upstream.ok) {
    // Sonnet 4.6 estimate: ~$3/M input + $15/M output. 600/600 tokens → ~1.1 cents.
    await addSpendCents(env, day, 1);
  }
  return new Response(upstreamBody, {
    status: upstream.status,
    headers: { ...corsHeaders(origin), "Content-Type": "application/json" },
  });
}

// ---------- /api/spend ----------

async function spendRoute(url: URL, env: Env, origin: string | null): Promise<Response> {
  const day = url.searchParams.get("date") || todayUtcIsoDate();
  const cents = await getSpendCents(env, day);
  const cap = parseInt(env.DAILY_SPEND_CAP_CENTS || "30", 10);
  return jsonResponse(
    { date: day, spent_cents: cents, cap_cents: cap, remaining_cents: Math.max(cap - cents, 0) },
    { status: 200 },
    origin,
  );
}

// ---------- dispatch ----------

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const origin = req.headers.get("origin");

    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }
    if (!isAllowedOrigin(origin) && origin !== null) {
      return jsonResponse({ error: "origin not allowed" }, { status: 403 }, origin);
    }
    try {
      if (url.pathname === "/proxy/arxiv" && req.method === "GET") {
        return await proxyArxiv(url, origin);
      }
      if (url.pathname === "/proxy/s2" && req.method === "GET") {
        return await proxyS2(url, env, origin);
      }
      if (url.pathname === "/api/deep-dive" && req.method === "POST") {
        return await deepDive(req, env, origin);
      }
      if (url.pathname === "/api/spend" && req.method === "GET") {
        return await spendRoute(url, env, origin);
      }
      return jsonResponse({ error: "not found" }, { status: 404 }, origin);
    } catch (err) {
      return jsonResponse(
        { error: String((err as Error).message || err) },
        { status: 500 },
        origin,
      );
    }
  },
};
