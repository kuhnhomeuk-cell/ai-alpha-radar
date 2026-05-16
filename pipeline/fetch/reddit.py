"""Reddit subreddit-shortlist fetcher (public RSS variant).

Audit item 3.3 — Theme 7: r/LocalLLaMA, r/StableDiffusion, r/ClaudeAI
typically surface creator-tool launches 5-10 days ahead of HN.

History: this module originally used PRAW + a Reddit script-app's
OAuth credentials. Reddit deprecated new legacy-API script apps in
late 2024 (the create-app flow silently fails for new applicants
unless approved through a manual moderation request), so we switched
to the public per-subreddit RSS endpoint at /r/<sub>/top/.rss?t=week.
No auth required.

Tradeoffs vs. the PRAW path:

- score, upvote_ratio, num_comments are NOT exposed in RSS — they
  default to 0 / 0.5 / 0 on every parsed post. Downstream consumers
  that scored by engagement (e.g., the mention-aggregator in run.py)
  will see all posts as equally weighted; the count itself (whether a
  topic surfaced at all in a subreddit) is the load-bearing signal,
  which we still get.
- Comments are not fetched (RSS doesn't expose them). The question-
  mining path (audit 3.14) loses Reddit as a source and falls back to
  HN comments only.
- RSS returns ~25 entries per request — same order of magnitude as
  the prior PRAW limit_per_sub=25 default.

Reddit aggressively rate-limits anonymous RSS requests when the
User-Agent header is generic, so we keep the custom UA convention
the audit prescribed and a 2-second polite delay between subreddits.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import feedparser
import httpx
from pydantic import BaseModel

from pipeline.fetch._retry import with_retry

DEFAULT_SUBREDDITS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "reddit_subreddits.json"
)
REDDIT_USER_AGENT_FALLBACK = (
    "ai-alpha-radar/0.1 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"
)
REDDIT_RSS_URL_TEMPLATE = "https://www.reddit.com/r/{sub}/top/.rss"
REDDIT_OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_OAUTH_BASE = "https://oauth.reddit.com"
REDDIT_REQUEST_INTERVAL_SECONDS = 2.0  # Reddit rate-limits aggressively
TIME_FILTER_DEFAULT = "week"
LIMIT_PER_SUB_DEFAULT = 25


class RedditPost(BaseModel):
    id: str
    title: str
    subreddit: str
    # The RSS endpoint doesn't expose engagement counters — defaults
    # preserved so RedditPost remains the same shape downstream.
    score: int = 0
    upvote_ratio: float = 0.5
    num_comments: int = 0
    created_at: datetime
    url: str
    selftext: str = ""


_HTML_RE = re.compile(r"<[^>]+>")
_REDDIT_ID_RE = re.compile(r"t3_([a-z0-9]+)", re.IGNORECASE)


def _strip_html(text: str) -> str:
    return _HTML_RE.sub(" ", text or "").strip()


def _extract_post_id(raw_id: str) -> str:
    """Reddit RSS entry IDs look like 't3_abc123' or a full tag URI ending
    in '/comments/abc123/...'. Either way, the post id is the short alnum
    after 't3_' or the path segment after '/comments/'.
    """
    if not raw_id:
        return ""
    m = _REDDIT_ID_RE.search(raw_id)
    if m:
        return m.group(1)
    # Fallback: take the last non-empty path segment.
    parts = [p for p in raw_id.replace("\\", "/").split("/") if p]
    return parts[-1] if parts else raw_id


def parse_rss_entry(entry: Any, *, subreddit: str) -> RedditPost:
    """Convert a feedparser entry (or SimpleNamespace with the same attrs)
    into a RedditPost.

    The RSS feed exposes id/title/link/published/summary. Engagement
    counters are missing and stay at their model defaults.
    """
    raw_id = getattr(entry, "id", "") or ""
    post_id = _extract_post_id(raw_id)
    title = (getattr(entry, "title", "") or "").strip()
    published = (
        getattr(entry, "published_parsed", None)
        or getattr(entry, "updated_parsed", None)
    )
    if published:
        ts = datetime.fromtimestamp(time.mktime(published), tz=timezone.utc)
    else:
        ts = datetime.now(tz=timezone.utc)
    link = getattr(entry, "link", "") or ""
    selftext = _strip_html(getattr(entry, "summary", "") or "")
    return RedditPost(
        id=post_id,
        title=title,
        subreddit=subreddit,
        created_at=ts,
        url=link,
        selftext=selftext,
    )


def engagement_score(post: RedditPost, *, now: Optional[datetime] = None) -> float:
    """audit's recipe: (upvote_ratio * score * num_comments) / age_hours, with floor.

    Note: RSS-sourced posts have score=0 / num_comments=0, so this
    returns 0.0 for them. The signal we keep is "did this topic
    surface in a creator-relevant subreddit at all" — captured by the
    mention counts, not by the engagement score.
    """
    now = now or datetime.now(tz=timezone.utc)
    age_hours = max((now - post.created_at).total_seconds() / 3600.0, 1.0)
    return (post.upvote_ratio * post.score * max(post.num_comments, 1)) / age_hours


def dedupe_posts(posts: Iterable[RedditPost]) -> list[RedditPost]:
    seen: set[str] = set()
    out: list[RedditPost] = []
    for p in posts:
        if p.id in seen:
            continue
        seen.add(p.id)
        out.append(p)
    return out


def _post_text_lower(p: RedditPost) -> str:
    return (p.title + " " + p.selftext).lower()


def mentions_per_term(
    posts: Sequence[RedditPost], *, terms: Sequence[str]
) -> dict[str, int]:
    """Per-term: count of posts whose title+selftext contains the term (case-insensitive)."""
    out: dict[str, int] = {t: 0 for t in terms}
    texts = [(_post_text_lower(p)) for p in posts]
    for term in terms:
        needle = term.lower()
        out[term] = sum(1 for text in texts if needle in text)
    return out


def top_subreddit_per_term(
    posts: Sequence[RedditPost], *, terms: Sequence[str]
) -> dict[str, str]:
    """Per-term: subreddit with the most matching posts. Ties broken by name."""
    out: dict[str, str] = {}
    for term in terms:
        needle = term.lower()
        counts: dict[str, int] = {}
        for p in posts:
            if needle in _post_text_lower(p):
                counts[p.subreddit] = counts.get(p.subreddit, 0) + 1
        if counts:
            out[term] = max(sorted(counts), key=lambda k: counts[k])
    return out


def load_subreddit_list(path: Path = DEFAULT_SUBREDDITS_PATH) -> list[str]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@with_retry(attempts=2, base_delay=1.0, max_delay=10.0)
def _fetch_rss(url: str, user_agent: str) -> str:
    headers = {"User-Agent": user_agent, "Accept": "application/rss+xml, text/xml"}
    with httpx.Client(timeout=30, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


# ---------- OAuth path (preferred when creds are present) ----------

# Reddit's public RSS endpoint returns 403 to datacenter IPs since
# 2026-05 — CI runs cannot reach it. OAuth bypasses that wall (traffic
# routes through oauth.reddit.com, not www.reddit.com) and also gives
# us richer fields (score, upvote_ratio, num_comments) that the RSS
# path leaves at defaults.
#
# Two grant types are tried in order:
#   1. client_credentials — app-only, read-only. Needs only CLIENT_ID +
#      CLIENT_SECRET. No user account involved, no plaintext password
#      stored. Tokens valid 3600s. Sufficient for the only thing this
#      fetcher reads: public subreddit /top listings.
#   2. password — fallback. Needs USERNAME + PASSWORD in addition to
#      the script-app creds. Only used if app-only is refused (e.g. a
#      particular client_id has the grant disabled).
#
# Creds come from a pre-Nov-2025 Reddit "script" app at
# https://www.reddit.com/prefs/apps. New apps are gated behind the
# Responsible Builder Policy (Nov 2025) and silently fail to create —
# apply via Reddit's Developer Support form if you need fresh creds.


def _request_token(
    grant_data: dict[str, str],
    *,
    client_id: str,
    client_secret: str,
    user_agent: str,
) -> Optional[str]:
    """POST to Reddit's token endpoint; return access_token or None."""
    import sys

    headers = {"User-Agent": user_agent}
    try:
        with httpx.Client(
            timeout=30,
            headers=headers,
            auth=httpx.BasicAuth(client_id, client_secret),
        ) as client:
            response = client.post(REDDIT_OAUTH_TOKEN_URL, data=grant_data)
            response.raise_for_status()
            token = response.json().get("access_token")
            return token if isinstance(token, str) and token else None
    except Exception as e:
        grant = grant_data.get("grant_type", "?")
        print(
            f"reddit: OAuth token request failed ({grant}): {e}",
            file=sys.stderr,
        )
        return None


def _oauth_token() -> Optional[str]:
    """Obtain a Reddit OAuth bearer token.

    Tries client_credentials (app-only, read-only) first — needs only
    CLIENT_ID + CLIENT_SECRET. Falls back to the password grant when
    app-only is refused and USERNAME + PASSWORD are configured.
    Returns None when no path produces a token; callers fall back to
    the RSS path.
    """
    client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    if not (client_id and client_secret):
        return None
    user_agent = os.environ.get("REDDIT_USER_AGENT", REDDIT_USER_AGENT_FALLBACK)

    token = _request_token(
        {"grant_type": "client_credentials"},
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )
    if token:
        return token

    username = os.environ.get("REDDIT_USERNAME", "").strip()
    password = os.environ.get("REDDIT_PASSWORD", "").strip()
    if not (username and password):
        return None
    return _request_token(
        {"grant_type": "password", "username": username, "password": password},
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )


def _parse_oauth_listing(payload: dict[str, Any], subreddit: str) -> list[RedditPost]:
    """Convert one /r/{sub}/top JSON response into RedditPosts.

    The JSON has richer fields than RSS — score, upvote_ratio,
    num_comments are populated. Items missing the required `id` are
    silently skipped (some moderator-removed posts have a stripped
    payload).
    """
    out: list[RedditPost] = []
    for child in (payload.get("data") or {}).get("children") or []:
        post = child.get("data") or {}
        post_id = post.get("id")
        if not post_id:
            continue
        created_utc = float(post.get("created_utc") or 0)
        if created_utc <= 0:
            ts = datetime.now(tz=timezone.utc)
        else:
            ts = datetime.fromtimestamp(created_utc, tz=timezone.utc)
        permalink = post.get("permalink") or ""
        full_url = (
            f"https://www.reddit.com{permalink}"
            if permalink.startswith("/")
            else (post.get("url") or "")
        )
        out.append(
            RedditPost(
                id=post_id,
                title=(post.get("title") or "").strip(),
                subreddit=subreddit,
                score=int(post.get("score") or 0),
                upvote_ratio=float(post.get("upvote_ratio") or 0.5),
                num_comments=int(post.get("num_comments") or 0),
                created_at=ts,
                url=full_url,
                selftext=post.get("selftext") or "",
            )
        )
    return out


@with_retry(attempts=2, base_delay=1.0, max_delay=10.0)
def _fetch_top_oauth(
    token: str, subreddit: str, *, time_filter: str, limit: int
) -> list[RedditPost]:
    user_agent = os.environ.get("REDDIT_USER_AGENT", REDDIT_USER_AGENT_FALLBACK)
    headers = {
        "Authorization": f"bearer {token}",
        "User-Agent": user_agent,
    }
    url = f"{REDDIT_OAUTH_BASE}/r/{subreddit}/top"
    params = {"t": time_filter, "limit": limit, "raw_json": 1}
    with httpx.Client(timeout=30, headers=headers) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return _parse_oauth_listing(response.json(), subreddit)


def fetch_top_posts(
    *,
    subreddits: Optional[Sequence[str]] = None,
    time_filter: str = TIME_FILTER_DEFAULT,
    limit_per_sub: int = LIMIT_PER_SUB_DEFAULT,
) -> list[RedditPost]:
    """Fan out across subreddits via public RSS, parse + dedupe.

    Reads optional REDDIT_USER_AGENT from the environment (Reddit
    aggressively rate-limits anonymous traffic with default UAs, so
    supply a real one if you can).

    Production state (2026-05): Reddit returns 403 to anonymous RSS
    requests from datacenter IPs regardless of UA. Per-subreddit
    failures are swallowed (one rate-limited sub shouldn't kill the
    rest), but the failure mode is now logged to stderr so operators
    can tell "0 posts" apart from "Reddit blocked us entirely". When
    every subreddit fails, that's the loud signal.
    """
    import sys

    user_agent = os.environ.get("REDDIT_USER_AGENT", REDDIT_USER_AGENT_FALLBACK)
    if subreddits is None:
        subreddits = load_subreddit_list()
    if not subreddits:
        return []

    # Prefer OAuth — bypasses Reddit's datacenter-IP 403 wall and also
    # surfaces score / upvote_ratio / num_comments. Falls through to RSS
    # only when creds are missing/invalid; both paths degrade silently
    # so a missing config never parks the daily pipeline.
    token = _oauth_token()
    if token:
        oauth_out: list[RedditPost] = []
        for sub_name in subreddits:
            try:
                oauth_out.extend(
                    _fetch_top_oauth(
                        token,
                        sub_name,
                        time_filter=time_filter,
                        limit=limit_per_sub,
                    )
                )
            except Exception as e:
                print(
                    f"reddit (oauth): r/{sub_name} failed: "
                    f"{type(e).__name__}: {e}",
                    file=sys.stderr,
                )
                continue
            time.sleep(REDDIT_REQUEST_INTERVAL_SECONDS)
        if oauth_out:
            return dedupe_posts(oauth_out)
        # OAuth produced nothing across every sub — fall through to RSS.

    out: list[RedditPost] = []
    failures: list[tuple[str, str]] = []
    for sub_name in subreddits:
        url = f"{REDDIT_RSS_URL_TEMPLATE.format(sub=sub_name)}?t={time_filter}&limit={limit_per_sub}"
        try:
            xml = _fetch_rss(url, user_agent)
        except Exception as e:
            failures.append((sub_name, f"{type(e).__name__}: {e}"))
            continue
        feed = feedparser.parse(xml)
        for entry in feed.entries[:limit_per_sub]:
            out.append(parse_rss_entry(entry, subreddit=sub_name))
        time.sleep(REDDIT_REQUEST_INTERVAL_SECONDS)
    if failures and not out:
        # All subreddits failed — Reddit is blocking the whole batch.
        # Log a single summary line so the operator sees the real issue
        # in the daily run output. Per-sub detail still goes to stderr.
        print(
            f"reddit: all {len(failures)} subreddits failed "
            f"(likely 403 from anonymous RSS; need OAuth path)",
            file=sys.stderr,
        )
        for sub_name, reason in failures[:3]:
            print(f"  r/{sub_name}: {reason}", file=sys.stderr)
    return dedupe_posts(out)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(".env.local", override=True)
    subs = load_subreddit_list()
    print(f"trying {len(subs)} subreddits: {subs}")
    posts = fetch_top_posts(subreddits=subs[:5], limit_per_sub=10)
    print(f"fetched {len(posts)} posts")
    for p in posts[:5]:
        print(f"  - r/{p.subreddit}: {p.title[:70]}")
    # We don't sys.exit(1) on empty — Reddit's 403 wall is the known
    # production state until OAuth is wired (week-4 spec). The verifier's
    # job is to show the operator what happened, not gate the build.
