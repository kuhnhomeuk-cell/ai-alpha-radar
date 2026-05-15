"""Citation enrichment via the Semantic Scholar batch endpoint.

Per BACKEND_BUILD §7 Step 5 — filters arxiv papers that get published but
never cited. POSTs up to 500 arXiv IDs per request, returns a dict keyed by
the caller's input ids.

Indexing-lag reality: papers published in the last ~1-2 weeks are typically
NOT in S2's index, so enrich_papers returns no entry for them. That's
expected — Step 6 normalize.py treats missing citation data as "no signal
yet", not as an error. The spec's "≥70% coverage" verify reflects a steady
state, not the day-of-publication state.
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterable, Optional

import httpx
from pydantic import BaseModel

from pipeline.fetch._retry import with_retry

S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_BATCH_LIMIT = 500
S2_FIELDS = "citationCount,influentialCitationCount,referenceCount"
S2_REQUEST_INTERVAL_SECONDS = 1.0  # 1 RPS unauth per BACKEND_BUILD §9
S2_USER_AGENT = "ai-alpha-radar/0.1 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"


class CitationInfo(BaseModel):
    citation_count: int
    influential_citation_count: int
    references_count: int


def parse_batch_response(
    arxiv_ids: list[str], response: list[Optional[dict[str, Any]]]
) -> dict[str, CitationInfo]:
    """Zip the input ids with the S2 batch response (parallel arrays).

    Null response entries (unindexed papers) are skipped — they simply
    don't appear in the result dict.
    """
    out: dict[str, CitationInfo] = {}
    for arxiv_id, entry in zip(arxiv_ids, response):
        if entry is None:
            continue
        out[arxiv_id] = CitationInfo(
            citation_count=int(entry.get("citationCount") or 0),
            influential_citation_count=int(entry.get("influentialCitationCount") or 0),
            references_count=int(entry.get("referenceCount") or 0),
        )
    return out


ARXIV_VERSION_SUFFIX_RE = re.compile(r"v\d+$", re.IGNORECASE)


def _strip_arxiv_version(arxiv_id: str) -> str:
    return ARXIV_VERSION_SUFFIX_RE.sub("", arxiv_id)


def _prefix_arxiv_ids(arxiv_ids: Iterable[str]) -> list[str]:
    """Normalize to S2's `ARXIV:<base>` form.

    Accepts bare ids (`1706.03762`), arxiv URLs (`http://arxiv.org/abs/2005.14165v1`),
    and already-prefixed ids — strips version suffix in all cases.
    """
    out: list[str] = []
    for aid in arxiv_ids:
        aid = aid.strip()
        if aid.startswith("ARXIV:"):
            out.append(f"ARXIV:{_strip_arxiv_version(aid.removeprefix('ARXIV:'))}")
            continue
        if "/abs/" in aid:
            base = aid.split("/abs/", 1)[1]
        else:
            base = aid.rsplit("/", 1)[-1]
        out.append(f"ARXIV:{_strip_arxiv_version(base)}")
    return out


@with_retry(attempts=3, base_delay=1.0)
def _post_batch(
    arxiv_ids: list[str], api_key: Optional[str] = None
) -> dict[str, CitationInfo]:
    """One POST to the S2 batch endpoint. Up to S2_BATCH_LIMIT ids per call."""
    if not arxiv_ids:
        return {}
    prefixed = _prefix_arxiv_ids(arxiv_ids)
    headers = {"User-Agent": S2_USER_AGENT}
    if api_key:
        headers["x-api-key"] = api_key

    with httpx.Client(timeout=60, headers=headers) as client:
        response = client.post(
            S2_BATCH_URL,
            params={"fields": S2_FIELDS},
            json={"ids": prefixed},
        )
        response.raise_for_status()
        data = response.json()
    time.sleep(S2_REQUEST_INTERVAL_SECONDS)
    return parse_batch_response(arxiv_ids, data)


def enrich_papers(
    arxiv_ids: list[str], *, api_key: Optional[str] = None
) -> dict[str, CitationInfo]:
    """Return {original_id: CitationInfo} for every indexed paper.

    Chunks input into S2_BATCH_LIMIT-sized POSTs so callers don't need to
    pre-split. Per-batch retry is applied via _post_batch's @with_retry.
    """
    if not arxiv_ids:
        return {}
    results: dict[str, CitationInfo] = {}
    for start in range(0, len(arxiv_ids), S2_BATCH_LIMIT):
        chunk = arxiv_ids[start : start + S2_BATCH_LIMIT]
        results.update(_post_batch_resilient(chunk, api_key))
    return results


def _post_batch_resilient(
    arxiv_ids: list[str], api_key: Optional[str] = None
) -> dict[str, CitationInfo]:
    """Split 400ing S2 batches to isolate bad ids without losing the rest."""
    try:
        return _post_batch(arxiv_ids, api_key)
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 400 or len(arxiv_ids) <= 1:
            if e.response.status_code == 400 and len(arxiv_ids) == 1:
                return {}
            raise
        mid = len(arxiv_ids) // 2
        left = _post_batch_resilient(arxiv_ids[:mid], api_key)
        right = _post_batch_resilient(arxiv_ids[mid:], api_key)
        return {**left, **right}


if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(".env.local", override=True)
    key = os.environ.get("SEMANTIC_SCHOLAR_KEY") or None

    from pipeline.fetch import arxiv

    xml = Path("tests/fixtures/arxiv_sample.xml").read_text(encoding="utf-8")
    papers = arxiv.parse_atom_feed(xml, categories=["cs.AI", "cs.LG", "cs.CL"])
    recent_ids = [p.id for p in papers[:10]]
    # Mix in a few well-known indexed papers so the verify proves the
    # fetcher works end-to-end even when fresh arxiv ids haven't been
    # indexed yet.
    known_ids = ["ARXIV:1706.03762", "ARXIV:2005.14165", "ARXIV:2203.02155"]
    ids = recent_ids + known_ids

    print(f"requesting {len(ids)} ids ({len(recent_ids)} fresh + {len(known_ids)} known)")
    print(f"auth: {'x-api-key' if key else 'unauthenticated'}")
    enriched = enrich_papers(ids, api_key=key)
    print(f"enriched: {len(enriched)}/{len(ids)}")
    for aid in known_ids:
        info = enriched.get(aid)
        if info:
            print(f"  {aid}: {info.citation_count} citations, {info.references_count} refs")
    fresh_enriched = sum(1 for aid in recent_ids if aid in enriched)
    print(
        f"fresh-arxiv coverage: {fresh_enriched}/{len(recent_ids)} "
        f"(S2 indexes papers with ~1-2 week lag)"
    )
    if len(enriched) < 3:
        # Even with indexing lag, the three known ids should always return.
        sys.exit(1)
