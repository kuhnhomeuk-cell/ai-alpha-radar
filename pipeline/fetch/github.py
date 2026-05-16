"""Fetch trending AI-relevant GitHub repos by topic, then compute star velocity.

Per BACKEND_BUILD §7 Step 4 — practitioner adoption signal driving the
"Builder" lifecycle stage. PyGithub is the live client; the parser is a pure
function that also drives the offline tests.

Spec deviation (surfaced not silent): GitHub Search rejects
`q=topic:ai OR topic:llm OR topic:agents` — its OR only applies to text
terms, not qualifiers. Fan-out: one search per topic, then dedupe by
full_name. Same merge pattern as the HN fetcher.

Day-1 reality: no prior snapshot exists, so every RepoStat ships with
`stars_7d_delta=None` and `warming_up=True` (BACKEND_BUILD §12 risk row).
The flag flips off automatically once snapshots accumulate.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from github import Auth, Github
from pydantic import BaseModel
from urllib3.util.retry import Retry

# v0.2.0 — expanded from 3 to 8 topics. The added tool-category labels
# (rag/mcp/embedding/fine-tuning/transformer) catch builder/tooling
# repos that the narrower trio would miss. Total request budget is
# 8 topics × 2s spacing = ~16s, still inside the 30 req/min ceiling.
GH_TOPICS = ["ai", "llm", "agents", "rag", "transformer", "mcp", "embedding", "fine-tuning"]
GH_PER_TOPIC_LIMIT = 30
GH_REQUEST_INTERVAL_SECONDS = 2.0  # 30 req/min cap per BACKEND_BUILD §9
# Retry config injected into PyGithub — mirrors _retry.with_retry semantics
# for the httpx-based fetchers (item 2.7).
GH_RETRY = Retry(
    total=2,  # 2 retries on top of the initial call → 3 attempts total
    backoff_factor=1.0,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=frozenset(["GET"]),
    respect_retry_after_header=True,
)


class RepoStat(BaseModel):
    full_name: str
    description: Optional[str] = None
    stars: int
    stars_7d_delta: Optional[int] = None
    warming_up: bool = True
    language: Optional[str] = None
    topics: list[str]
    created_at: datetime
    pushed_at: datetime
    html_url: str


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_search_response(payload: dict[str, Any]) -> list[RepoStat]:
    """Parse a GitHub Search API response dict into RepoStat objects.

    Same shape whether the response came from a raw HTTP call or a
    PyGithub repo's `raw_data` dict wrapped in `{"items": [...]}`.
    """
    repos: list[RepoStat] = []
    for item in payload.get("items", []):
        if not item.get("full_name"):
            continue
        repos.append(
            RepoStat(
                full_name=item["full_name"],
                description=item.get("description"),
                stars=int(item.get("stargazers_count") or 0),
                language=item.get("language"),
                topics=list(item.get("topics") or []),
                created_at=_parse_iso(item["created_at"]),
                pushed_at=_parse_iso(item["pushed_at"]),
                html_url=item["html_url"],
            )
        )
    return repos


def compute_star_velocity(
    today: list[RepoStat], *, prior_stars: dict[str, int]
) -> list[RepoStat]:
    """Annotate each repo with stars_7d_delta and warming_up using a prior map."""
    annotated: list[RepoStat] = []
    for r in today:
        if r.full_name in prior_stars:
            delta = r.stars - prior_stars[r.full_name]
            annotated.append(r.model_copy(update={"stars_7d_delta": delta, "warming_up": False}))
        else:
            annotated.append(r)
    return annotated


def load_prior_star_map(snapshot_path: Path) -> dict[str, int]:
    """Read a previous snapshot file and pull the github_stars map from meta.

    Returns {} if the path is missing or the meta key isn't there. Step 12
    will populate meta.github_stars when it writes snapshots.
    """
    if not snapshot_path.exists():
        return {}
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    return data.get("meta", {}).get("github_stars", {})


def fetch_trending_repos(
    gh_pat: str,
    *,
    topics: Iterable[str] = GH_TOPICS,
    lookback_days: int = 7,
    snapshots_dir: Optional[Path] = None,
    apply_niche_filter: bool = False,
) -> list[RepoStat]:
    """Live PyGithub search across topics, dedupe, then attach star velocity
    against the snapshot from `lookback_days` ago (or warming_up if absent).

    `apply_niche_filter=True` runs each repo's description+topics through
    `pipeline.niche_filter.is_niche_relevant`. The 8 search topics already
    bound results to AI-adjacent repos, so this is a second-stage filter
    that drops e.g. an "ai" topic on a fashion-design library. Default
    off to keep the orchestrator's call-shape backwards compatible.
    """
    since = (datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    auth = Auth.Token(gh_pat)
    items_by_name: dict[str, dict[str, Any]] = {}
    g = Github(auth=auth, retry=GH_RETRY)
    try:
        for topic in topics:
            query = f"topic:{topic} created:>{since}"
            for repo in g.search_repositories(query=query, sort="stars", order="desc")[
                :GH_PER_TOPIC_LIMIT
            ]:
                items_by_name.setdefault(repo.full_name, repo.raw_data)
            time.sleep(GH_REQUEST_INTERVAL_SECONDS)
    finally:
        g.close()

    today = parse_search_response({"items": list(items_by_name.values())})

    if apply_niche_filter:
        from pipeline.niche_filter import filter_niche_relevant

        today = filter_niche_relevant(
            today,
            key=lambda r: (r.description or "") + " " + " ".join(r.topics),
        )

    prior: dict[str, int] = {}
    if snapshots_dir is not None:
        prior_date = (datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)).date()
        prior = load_prior_star_map(snapshots_dir / f"{prior_date.isoformat()}.json")

    return compute_star_velocity(today, prior_stars=prior)


if __name__ == "__main__":
    import os
    import sys

    from dotenv import load_dotenv

    load_dotenv(".env.local", override=True)  # repo-local secrets win over shell exports
    pat = os.environ.get("GH_PAT", "").strip()
    if not pat:
        print(
            "GH_PAT not set in environment. Create a token at "
            "https://github.com/settings/tokens (repo + read:org scope) and "
            "export GH_PAT=... before re-running.",
            file=sys.stderr,
        )
        sys.exit(2)

    repos = fetch_trending_repos(pat, snapshots_dir=Path("public/snapshots"))
    warming = sum(1 for r in repos if r.warming_up)
    print(
        f"fetched {len(repos)} unique trending AI repos (last 7d), "
        f"{warming} warming_up (no prior snapshot yet)"
    )
    for r in sorted(repos, key=lambda r: -r.stars)[:5]:
        delta = "warming" if r.warming_up else f"+{r.stars_7d_delta}"
        print(f"  - [{r.stars}* {delta}] {r.full_name} - topics: {r.topics[:3]}")
    if len(repos) < 20:
        sys.exit(1)
