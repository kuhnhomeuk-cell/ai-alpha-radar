"""Claude-driven enrichment for trend cards.

Per BACKEND_BUILD §7 Step 9 + PLAN.md §7. Four sequential Haiku 4.5 prompts
per card (A summary → B angles + D ELI both depend on summary; C risk is
independent). Daily Movers Briefing is one Sonnet 4.6 call across all
cards (lives in `daily_briefing`).

This module ships both paths:

- enrich_card(): SYNCHRONOUS, full price. Used for the §5 one-card
  inspection and for any ad-hoc enrichment.
- enrich_cards_batch(): BATCH API, ~50% cost. Two sequential batches —
  stage 1 fires prompts A + C, stage 2 fires prompts B + D using
  stage 1's summary. Daily cron uses this path.

Prompts A–D are reproduced verbatim from PLAN.md §7.2. The shared system
prompt is marked with cache_control=ephemeral so prompt caching kicks in
across the daily 75-trend run.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Optional

import anthropic
from pydantic import BaseModel

from pipeline.models import CreatorAngles, DailyBriefing, LifecycleStage, RiskFlag

HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-4-6"

MAX_OUTPUT_TOKENS_CARD = 300
MAX_OUTPUT_TOKENS_BRIEFING = 600
MAX_INPUT_TOKENS_BUDGET = 600  # BACKEND_BUILD §9 per-request ceiling

DEFAULT_NICHE = "AI tools for solo creators"

BATCH_POLL_INTERVAL_SECONDS = 30.0
BATCH_TIMEOUT_SECONDS = 60 * 60  # 1h hard timeout per BACKEND_BUILD §14 workflow timeout

SYSTEM_PROMPT_TEMPLATE = (
    "You are a trend-analysis engine for a YouTube Shorts creator dashboard.\n"
    "Return ONLY valid JSON matching the schema provided. No prose. No markdown fences.\n"
    "If you have low confidence about a trend, set \"confidence\": \"low\" and keep fields brief.\n"
    "Never fabricate specific people, numbers, products, or dates.\n"
    "The user's content niche is: {user_niche}. Tailor angles to this niche when possible."
)

_MARKDOWN_JSON_FENCE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)


class ClaudeParseError(ValueError):
    """Raised when a Claude response can't be parsed as JSON after one retry."""


class CardInput(BaseModel):
    keyword: str
    cluster_label: str
    related_terms: list[str]
    arxiv_papers_7d: int
    github_repos_7d: int
    hn_posts_7d: int
    velocity_score: float
    saturation: float
    convergence_detected: bool
    lifecycle_stage: LifecycleStage
    user_niche: str = DEFAULT_NICHE


class CardOutput(BaseModel):
    summary: str
    summary_confidence: Literal["high", "medium", "low"]
    angles: CreatorAngles
    risk: RiskFlag


# ---------- Prompts (verbatim PLAN.md §7.2) ----------


def _build_prompt_a(card: CardInput) -> str:
    related = ", ".join(card.related_terms) if card.related_terms else "(none)"
    return (
        f"Trend keyword: {card.keyword}\n"
        f"Cluster context: {card.cluster_label}; related terms: {related}\n"
        f"Signal data: arxiv_papers_7d={card.arxiv_papers_7d}, "
        f"github_repos_7d={card.github_repos_7d}, hn_posts_7d={card.hn_posts_7d}\n\n"
        "Task: Write a single-sentence summary of this trend in plain English, max 18 words.\n"
        "No jargon. A smart non-engineer should understand it.\n\n"
        'Return JSON: {"summary": string, "confidence": "high"|"medium"|"low"}'
    )


def _build_prompt_b(card: CardInput, *, summary: str) -> str:
    return (
        f"Trend keyword: {card.keyword}\n"
        f"Summary: {summary}\n"
        f"Creator niche: {card.user_niche}\n\n"
        "Generate three YouTube Shorts angles. Each must be a standalone-titleable hook (<=12 words).\n"
        "- \"hook\": the most clickable framing\n"
        "- \"contrarian\": the unpopular-take framing\n"
        "- \"tutorial\": the how-to framing\n\n"
        'Return JSON: {"hook": string, "contrarian": string, "tutorial": string}'
    )


def _build_prompt_c(card: CardInput) -> str:
    return (
        f"Trend keyword: {card.keyword}\n"
        f"Lifecycle stage: {card.lifecycle_stage}\n"
        f"Velocity: {card.velocity_score}; Saturation: {card.saturation}; "
        f"Convergence event: {card.convergence_detected}\n\n"
        "Estimate:\n"
        '- breakout_likelihood: "low" | "medium" | "high" | "breakout"\n'
        "- peak_estimate_days: integer (days until mainstream peak; 0 if already peaked)\n"
        "- risk_flag: short string (\"none\" | \"may be hype cycle\" | \"regulatory risk\" | "
        "\"single-source signal\" | other)\n"
        "- rationale: <=25 words\n\n"
        "Return JSON with all four fields."
    )


def _build_prompt_d(card: CardInput, *, summary: str) -> str:
    return (
        f"Trend keyword: {card.keyword}\n"
        f"Technical summary: {summary}\n\n"
        "Explain this trend using one analogy a YouTube viewer would get instantly.\n"
        "Max 40 words. No jargon at all.\n\n"
        'Return JSON: {"eli_creator": string}'
    )


# ---------- Client wrapper ----------


def _system_block(niche: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT_TEMPLATE.format(user_niche=niche),
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _extract_json(text: str) -> dict[str, Any]:
    """Parse JSON from a Claude response. Strips ```json fences if present."""
    text = text.strip()
    match = _MARKDOWN_JSON_FENCE.match(text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ClaudeParseError(f"Claude response was not valid JSON: {text[:120]!r}") from e


def _call_haiku(
    client: anthropic.Anthropic,
    *,
    niche: str,
    user_prompt: str,
) -> dict[str, Any]:
    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS_CARD,
        system=_system_block(niche),
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = response.content[0].text
    return _extract_json(text)


def enrich_card(
    card: CardInput, *, client: Optional[anthropic.Anthropic] = None
) -> CardOutput:
    """Run prompts A → (B, C, D) sequentially and assemble a CardOutput.

    Order is A → B → C → D (B and D depend on A's summary; C is
    independent but kept sequential for simplicity in the sync path).
    """
    if client is None:
        client = anthropic.Anthropic()

    a = _call_haiku(client, niche=card.user_niche, user_prompt=_build_prompt_a(card))
    summary = a["summary"]

    b = _call_haiku(
        client, niche=card.user_niche, user_prompt=_build_prompt_b(card, summary=summary)
    )
    c = _call_haiku(client, niche=card.user_niche, user_prompt=_build_prompt_c(card))
    d = _call_haiku(
        client, niche=card.user_niche, user_prompt=_build_prompt_d(card, summary=summary)
    )

    return CardOutput(
        summary=summary,
        summary_confidence=a["confidence"],
        angles=CreatorAngles(
            hook=b["hook"],
            contrarian=b["contrarian"],
            tutorial=b["tutorial"],
            eli_creator=d["eli_creator"],
        ),
        risk=RiskFlag(
            breakout_likelihood=c["breakout_likelihood"],
            peak_estimate_days=c.get("peak_estimate_days"),
            risk_flag=c["risk_flag"],
            rationale=c["rationale"],
        ),
    )


# ---------- Batch API ----------


def _build_request_params(
    *, niche: str, prompt: str, model: str = HAIKU_MODEL, max_tokens: int = MAX_OUTPUT_TOKENS_CARD
) -> dict[str, Any]:
    """Shape one MessageBatch request body."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": _system_block(niche),
        "messages": [{"role": "user", "content": prompt}],
    }


def _submit_and_collect_batch(
    client: anthropic.Anthropic, requests: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Submit one batch, poll until ended, return {custom_id: parsed_json}.

    Each `requests` entry has shape: {"custom_id": str, "params": <_build_request_params>}.
    Unparseable JSON responses become ClaudeParseError exceptions; failed
    batch entries become entries missing from the result dict.
    """
    batch = client.messages.batches.create(requests=requests)
    deadline = time.time() + BATCH_TIMEOUT_SECONDS
    while batch.processing_status != "ended":
        if time.time() > deadline:
            raise TimeoutError(f"Anthropic batch {batch.id} did not finish in 1 hour")
        time.sleep(BATCH_POLL_INTERVAL_SECONDS)
        batch = client.messages.batches.retrieve(batch.id)

    out: dict[str, dict[str, Any]] = {}
    for entry in client.messages.batches.results(batch.id):
        result = getattr(entry, "result", None)
        if result is None or getattr(result, "type", "") != "succeeded":
            continue
        message = result.message
        text = message.content[0].text
        out[entry.custom_id] = _extract_json(text)
    return out


def enrich_cards_batch(
    cards: list[CardInput], *, client: Optional[anthropic.Anthropic] = None
) -> dict[int, CardOutput]:
    """Enrich a list of cards via two sequential Batch API submissions.

    Stage 1 fires prompts A + C (independent of any other call).
    Stage 2 fires B + D using Stage 1's summary output.

    Cards are keyed by their position in the input list. Missing
    summary outputs from Stage 1 drop the card from the result entirely
    rather than fabricating a partial CardOutput.
    """
    if client is None:
        client = anthropic.Anthropic()
    if not cards:
        return {}

    # Stage 1: A + C
    stage1: list[dict[str, Any]] = []
    for i, card in enumerate(cards):
        stage1.append(
            {
                "custom_id": f"a_{i}",
                "params": _build_request_params(
                    niche=card.user_niche, prompt=_build_prompt_a(card)
                ),
            }
        )
        stage1.append(
            {
                "custom_id": f"c_{i}",
                "params": _build_request_params(
                    niche=card.user_niche, prompt=_build_prompt_c(card)
                ),
            }
        )
    stage1_results = _submit_and_collect_batch(client, stage1)

    # Stage 2: B + D — depend on A's summary
    stage2: list[dict[str, Any]] = []
    for i, card in enumerate(cards):
        a = stage1_results.get(f"a_{i}")
        if a is None:
            continue
        summary = a["summary"]
        stage2.append(
            {
                "custom_id": f"b_{i}",
                "params": _build_request_params(
                    niche=card.user_niche, prompt=_build_prompt_b(card, summary=summary)
                ),
            }
        )
        stage2.append(
            {
                "custom_id": f"d_{i}",
                "params": _build_request_params(
                    niche=card.user_niche, prompt=_build_prompt_d(card, summary=summary)
                ),
            }
        )
    stage2_results = _submit_and_collect_batch(client, stage2) if stage2 else {}

    outputs: dict[int, CardOutput] = {}
    for i, card in enumerate(cards):
        a = stage1_results.get(f"a_{i}")
        b = stage2_results.get(f"b_{i}")
        c = stage1_results.get(f"c_{i}")
        d = stage2_results.get(f"d_{i}")
        if not all([a, b, c, d]):
            continue
        outputs[i] = CardOutput(
            summary=a["summary"],
            summary_confidence=a["confidence"],
            angles=CreatorAngles(
                hook=b["hook"],
                contrarian=b["contrarian"],
                tutorial=b["tutorial"],
                eli_creator=d["eli_creator"],
            ),
            risk=RiskFlag(
                breakout_likelihood=c["breakout_likelihood"],
                peak_estimate_days=c.get("peak_estimate_days"),
                risk_flag=c["risk_flag"],
                rationale=c["rationale"],
            ),
        )
    return outputs


# ---------- Daily Movers Briefing (Sonnet, one call/day) ----------


class TrendMover(BaseModel):
    """Compact trend descriptor for the briefing prompt — caller assembles."""

    keyword: str
    lifecycle_stage: LifecycleStage
    velocity_score: float
    velocity_acceleration: float
    saturation: float


def _build_briefing_prompt(movers: Iterable[TrendMover], *, niche: str) -> str:
    lines = [
        f"- {m.keyword}: stage={m.lifecycle_stage}, velocity={m.velocity_score:.2f} "
        f"(accel={m.velocity_acceleration:+.2f}), saturation={m.saturation:.0f}"
        for m in movers
    ]
    movers_block = "\n".join(lines)
    return (
        f"Today's tracked trends (niche: {niche}):\n{movers_block}\n\n"
        "Write a ~150-word Daily Movers Briefing for a YouTube Shorts creator dashboard.\n"
        "Format: three short sections - 'What moved', 'What died', 'What's emerging'.\n"
        "Be concrete. Reference 2-4 actual keywords from the list above. No fluff.\n\n"
        "Return JSON with keys:\n"
        '- "text" (string, ~150 words, markdown)\n'
        '- "moved_up" (list of keyword strings)\n'
        '- "moved_down" (list of keyword strings)\n'
        '- "emerging" (list of keyword strings)'
    )


def daily_briefing(
    movers: list[TrendMover],
    *,
    niche: str = DEFAULT_NICHE,
    client: Optional[anthropic.Anthropic] = None,
) -> DailyBriefing:
    """Single Sonnet 4.6 call producing a DailyBriefing object."""
    if client is None:
        client = anthropic.Anthropic()

    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS_BRIEFING,
        system=_system_block(niche),
        messages=[
            {"role": "user", "content": _build_briefing_prompt(movers, niche=niche)}
        ],
    )
    parsed = _extract_json(response.content[0].text)
    return DailyBriefing(
        text=parsed["text"],
        moved_up=parsed.get("moved_up", []),
        moved_down=parsed.get("moved_down", []),
        emerging=parsed.get("emerging", []),
        generated_at=datetime.now(tz=timezone.utc),
    )
