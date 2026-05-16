"""Shared niche-relevance filter for the solo-creator AI niche.

The audit flagged that several fetchers (arxiv, github, hackernews) cast
too wide a net for the project's actual niche — "AI tools for solo
creators". This module centralises the curated keyword list so each
fetcher can apply the same post-fetch filter and pass/fail the same
unit-tested rules.

Design:

- `CREATOR_NICHE_TERMS` is a single curated frozenset of lowercase
  substrings. Substring (not word-boundary) matching is intentional so
  "fine-tun" catches both "fine-tune" and "fine-tuning", and "code-gen"
  catches "code-generation".
- `is_niche_relevant(text, *, terms=...)` is the per-text gate. Passing
  `terms=None` returns True for everything — a deliberate escape hatch
  for code paths that should not yet filter (e.g. arxiv during day-1
  bootstrap, where the upstream Claude topic extractor is the real
  filter).
- `filter_niche_relevant(items, *, key)` is the convenience wrapper
  fetchers call to drop non-matching items in one expression.

Why substring not word-boundary: real-world title strings include
hyphens, slashes, and code identifiers ("rag-eval", "openai/gpt-4o",
"voice-cloning"). Word-boundary regex misses these. Conversely the
keyword list is curated tight enough that "rag" doesn't false-positive
on "rage" because adjacent letters in real titles almost never appear.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional, TypeVar

T = TypeVar("T")

# Curated lowercase substrings. Order doesn't matter; matching is
# any-of. Keep this list tight — every term added widens the funnel.
# Source: BACKEND_BUILD.md §1 + the audit's §3 niche-pass prescription.
CREATOR_NICHE_TERMS: frozenset[str] = frozenset({
    # Foundation tech
    "llm",
    "gpt",
    "claude",
    "gemini",
    "mistral",
    "llama",
    "anthropic",
    "openai",
    "huggingface",
    "hugging face",
    "transformer",
    "diffusion",
    "stable diffusion",
    "flux",
    # Builder primitives
    "agent",
    "agents",
    "agentic",
    "rag",
    "retrieval-augmented",
    "embedding",
    "fine-tun",  # matches fine-tune AND fine-tuning
    "mcp",
    "model context protocol",
    "tool calling",
    "tool-use",
    "vector db",
    "vector store",
    # Creator tools
    "code-gen",
    "code generation",
    "vibe coding",
    "copilot",
    "cursor",
    "voice cloning",
    "voice clone",
    "text-to-speech",
    "tts",
    "speech-to-text",
    "image gen",
    "image generation",
    "video generation",
    "comfyui",
    "midjourney",
    "stable video",
    # Solo-creator concerns
    "local llm",
    "self-host",
    "open source",
    "open-source",
    "indie",
    "solo creator",
    "creator tool",
    "shortform",
    "shorts pipeline",
    "youtube automation",
})


def is_niche_relevant(
    text: str, *, terms: Optional[Iterable[str]] = CREATOR_NICHE_TERMS
) -> bool:
    """Return True when `text` contains any term in the niche keyword set.

    `terms=None` is a permissive bypass — every input returns True. This
    is intentional so callers can flip the filter on/off without forking
    the function. `terms=CREATOR_NICHE_TERMS` (the default) applies the
    project's curated list.
    """
    if terms is None:
        return True
    if not text:
        return False
    lower = text.lower()
    return any(term in lower for term in terms)


def filter_niche_relevant(
    items: Iterable[T],
    *,
    key: Callable[[T], str],
    terms: Optional[Iterable[str]] = CREATOR_NICHE_TERMS,
) -> list[T]:
    """Return items whose `key(item)` text passes is_niche_relevant.

    Iterates once; preserves order. Used by fetchers as a post-fetch
    pass: `filter_niche_relevant(papers, key=lambda p: p.title + p.abstract)`.
    """
    return [item for item in items if is_niche_relevant(key(item), terms=terms)]
