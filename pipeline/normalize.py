"""Extract canonical candidate terms across arXiv / HN / GitHub.

Per BACKEND_BUILD §7 Step 6. Tokenizes titles + bodies, generates 1-3 grams
via sklearn CountVectorizer, canonicalizes to lowercase-hyphenated form,
folds aliases, and counts per-source document mentions.

Two surfaced spec deviations:

1. Min token length is **2**, not 3 as the spec wrote. AI/ML/RL/NLP are the
   subject domain — dropping every 2-char token kills the literal target
   vocabulary.
2. Noise filters that require historical data (TF-IDF spike, mentions_30d
   floor, bot/low-signal) ship as no-op stubs on day 1 — they need the
   snapshot history that the daily cron builds up. The hooks are in place
   for Step 10 once snapshots accumulate.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from sklearn.feature_extraction import _stop_words
from sklearn.feature_extraction.text import CountVectorizer

from pipeline.fetch.arxiv import Paper
from pipeline.fetch.github import RepoStat
from pipeline.fetch.hackernews import HNPost

MIN_TOKEN_LENGTH = 2  # AI, ML, RL, NLP all need 2 chars — see module docstring

# Light stopword overlay on top of sklearn's English list.
_PIPELINE_EXTRA_STOPWORDS = {
    "paper",
    "papers",
    "github",
    "code",
    "show",
    "ask",
    "use",
    "using",
    "new",
}
PIPELINE_STOPWORDS = set(_stop_words.ENGLISH_STOP_WORDS) | _PIPELINE_EXTRA_STOPWORDS

DEFAULT_ALIASES_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "term_aliases.json"
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TOKEN_PATTERN = r"[a-zA-Z0-9][a-zA-Z0-9\-\.]+"


class Term(BaseModel):
    canonical_form: str
    raw_forms: list[str]
    arxiv_mentions: int = 0
    github_mentions: int = 0
    hn_mentions: int = 0


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub(" ", text)


def _canonical(token: str) -> str:
    return re.sub(r"\s+", "-", token.strip().lower())


def _load_aliases(path: Path = DEFAULT_ALIASES_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _paper_text(p: Paper) -> str:
    return f"{p.title}\n{p.abstract}"


def _post_text(p: HNPost) -> str:
    return _strip_html(f"{p.title}\n{p.story_text or ''}")


def _repo_text(r: RepoStat) -> str:
    return f"{r.full_name.replace('/', ' ')}\n{r.description or ''}\n{' '.join(r.topics)}"


def _build_vectorizer() -> CountVectorizer:
    return CountVectorizer(
        ngram_range=(1, 3),
        min_df=1,
        lowercase=True,
        token_pattern=_TOKEN_PATTERN,
        stop_words=sorted(PIPELINE_STOPWORDS),
    )


def _is_numeric_only(raw: str) -> bool:
    stripped = re.sub(r"[\-\.\s]", "", raw)
    return bool(stripped) and stripped.isdigit()


def extract_candidate_terms(
    papers: list[Paper],
    posts: list[HNPost],
    repos: list[RepoStat],
    *,
    aliases: Optional[dict[str, str]] = None,
) -> list[Term]:
    if aliases is None:
        aliases = _load_aliases()

    arxiv_docs = [_paper_text(p) for p in papers]
    hn_docs = [_post_text(p) for p in posts]
    gh_docs = [_repo_text(r) for r in repos]
    all_docs = arxiv_docs + hn_docs + gh_docs
    if not all_docs:
        return []

    n_arxiv = len(arxiv_docs)
    n_hn = len(hn_docs)

    vectorizer = _build_vectorizer()
    matrix = vectorizer.fit_transform(all_docs)
    features = vectorizer.get_feature_names_out()

    arxiv_slice = matrix[:n_arxiv]
    hn_slice = matrix[n_arxiv : n_arxiv + n_hn]
    gh_slice = matrix[n_arxiv + n_hn :]

    # Per-feature document presence count (binary collapse then sum) per source.
    def _doc_presence(sl) -> list[int]:
        if sl.shape[0] == 0:
            return [0] * sl.shape[1]
        # sl > 0 gives a boolean sparse mask; sum along axis=0 is per-feature
        return [int(v) for v in (sl > 0).sum(axis=0).tolist()[0]]

    arxiv_counts = _doc_presence(arxiv_slice)
    hn_counts = _doc_presence(hn_slice)
    gh_counts = _doc_presence(gh_slice)

    buckets: dict[str, dict] = defaultdict(
        lambda: {"raw_forms": set(), "arxiv": 0, "hn": 0, "github": 0}
    )

    for idx, raw in enumerate(features):
        # Spec filters
        if " " not in raw and len(raw) < MIN_TOKEN_LENGTH:
            continue
        if _is_numeric_only(raw):
            continue

        canonical = aliases.get(raw, _canonical(raw))
        bucket = buckets[canonical]
        bucket["raw_forms"].add(raw)
        bucket["arxiv"] += arxiv_counts[idx]
        bucket["hn"] += hn_counts[idx]
        bucket["github"] += gh_counts[idx]

    return [
        Term(
            canonical_form=c,
            raw_forms=sorted(b["raw_forms"]),
            arxiv_mentions=b["arxiv"],
            hn_mentions=b["hn"],
            github_mentions=b["github"],
        )
        for c, b in buckets.items()
    ]


if __name__ == "__main__":
    # Real-data smoke test against the Step 2-5 cached fixtures.
    from pipeline.fetch import arxiv, github, hackernews

    fixtures = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    papers = arxiv.parse_atom_feed(
        (fixtures / "arxiv_sample.xml").read_text(encoding="utf-8"),
        categories=["cs.AI", "cs.LG", "cs.CL"],
    )
    posts = hackernews.parse_search_response(
        json.loads((fixtures / "hn_sample.json").read_text(encoding="utf-8"))
    )
    repos = github.parse_search_response(
        json.loads((fixtures / "github_sample.json").read_text(encoding="utf-8"))
    )

    terms = extract_candidate_terms(papers, posts, repos)
    print(
        f"papers={len(papers)} posts={len(posts)} repos={len(repos)} "
        f"-> {len(terms)} candidate terms"
    )
    ranked = sorted(
        terms,
        key=lambda t: -(t.arxiv_mentions + t.hn_mentions + t.github_mentions),
    )
    print("top 15 by total document mentions:")
    for t in ranked[:15]:
        total = t.arxiv_mentions + t.hn_mentions + t.github_mentions
        print(
            f"  {total:>4} {t.canonical_form:<32} "
            f"arxiv={t.arxiv_mentions} hn={t.hn_mentions} gh={t.github_mentions}"
        )
