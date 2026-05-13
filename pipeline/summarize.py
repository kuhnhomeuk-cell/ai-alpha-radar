"""Claude-driven enrichment for trend cards.

Per BACKEND_BUILD §7 Step 9 + PLAN.md §7. Four sequential Haiku 4.5 prompts
per card (A summary → B angles + D ELI both depend on summary; C risk is
independent). Daily Movers Briefing is one Sonnet 4.6 call across all
cards (lives in `daily_briefing`).

This module ships the SYNCHRONOUS path. The Batch API wrapper lands after
the one-card visual-inspection checkpoint per ~/.claude/CLAUDE.md §5.

Prompts A–D are reproduced verbatim from PLAN.md §7.2. The shared system
prompt is marked with cache_control=ephemeral so prompt caching kicks in
across the daily 75-trend run.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional

import anthropic
from pydantic import BaseModel

from pipeline.models import CreatorAngles, LifecycleStage, RiskFlag

HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-4-6"

MAX_OUTPUT_TOKENS_CARD = 300
MAX_INPUT_TOKENS_BUDGET = 600  # BACKEND_BUILD §9 per-request ceiling

DEFAULT_NICHE = "AI tools for solo creators"

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
