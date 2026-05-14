"""Question mining from HN + Reddit + newsletter text.

Audit item 3.14. Surface the real questions creators are asking — that
maps directly to the tutorial-angle the LLM otherwise synthesizes.

Method (v1): regex-filter to question-shaped strings starting with a
classic question word, dedupe by normalized form, rank by frequency.
The audit's embed+HDBSCAN diversity pass is parked as a follow-up —
not worth the extra wiring until we have enough text per term to
benefit from clustering.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

_TAG_RE = re.compile(r"<[^>]+>")
_QUESTION_RE = re.compile(
    r"(?:^|[.!?\s])\s*((?:how|what|why|can|should|when|is|does|do|are|will|where|who|which)\b[^?]+\?)",
    re.IGNORECASE,
)
_NORMALIZE_WS_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub(" ", text)


def _normalize(q: str) -> str:
    collapsed = _NORMALIZE_WS_RE.sub(" ", q).strip().lower()
    # Strip trailing ? and any whitespace that crept in before it.
    return collapsed.rstrip("?").rstrip()


def extract_questions_from_text(text: str) -> list[str]:
    """Return question-shaped substrings (stripped of HTML) from `text`."""
    if not text:
        return []
    clean = _strip_html(text).replace("\n", " ")
    matches = _QUESTION_RE.findall(clean)
    return [m.strip() for m in matches]


def top_questions_for_term(
    texts: Iterable[str], *, term: str, top_n: int = 5
) -> list[str]:
    """Return the most-frequent question-shaped strings that mention `term`."""
    needle = term.lower()
    counts: Counter[str] = Counter()
    canonical_by_form: dict[str, str] = {}
    for text in texts:
        for q in extract_questions_from_text(text):
            if needle not in q.lower():
                continue
            norm = _normalize(q)
            if not norm:
                continue
            counts[norm] += 1
            # Preserve the first non-normalized variant for display.
            canonical_by_form.setdefault(norm, q)
    return [canonical_by_form[norm] for norm, _ in counts.most_common(top_n)]
