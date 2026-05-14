"""Exponential-backoff retry wrapper for httpx-based fetchers.

Audit item 2.7. Wraps a fetcher to retry on transient HTTP failures
(429 / 5xx) and network errors (timeouts, connection resets) with
jittered exponential backoff. Honors `Retry-After` on 429. Non-retryable
status codes (e.g. 401, 404) and non-httpx exceptions propagate
immediately.

Does NOT cover PyGithub-based fetches — PyGithub has its own retry
configuration via `urllib3.util.Retry` injected at client construction,
applied separately in pipeline/fetch/github.py.
"""

from __future__ import annotations

import functools
import random
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Optional, TypeVar

import httpx

DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

F = TypeVar("F", bound=Callable[..., Any])


def _parse_retry_after(value: str) -> Optional[float]:
    """Return seconds-to-wait from a `Retry-After` header, or None on parse failure."""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(tz=timezone.utc)).total_seconds())
    except Exception:
        return None


def with_retry(
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retry_on: Iterable[int] = DEFAULT_RETRY_STATUSES,
    jitter: float = 0.3,
) -> Callable[[F], F]:
    """Decorate an httpx-using callable with bounded retries.

    - `attempts`: total tries including the first call (so attempts=3 = 1 initial + 2 retries).
    - `base_delay`: initial backoff in seconds. Doubles each retry, capped at `max_delay`.
    - `retry_on`: set of HTTP status codes that trigger retry on HTTPStatusError.
    - `jitter`: fractional jitter added to each delay (e.g. 0.3 = 0-30% extra).
    """
    retry_set = frozenset(retry_on)

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    if status not in retry_set or attempt == attempts:
                        raise
                    retry_after = _parse_retry_after(
                        e.response.headers.get("retry-after", "")
                    )
                    if retry_after is not None:
                        # Honor server's Retry-After but never wait longer
                        # than max_delay — observed in production: tldr.tech
                        # returned Retry-After: 15013 (~4h), which would have
                        # parked the daily pipeline indefinitely.
                        delay = min(max_delay, retry_after)
                    else:
                        delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay = delay * (1 + random.uniform(0, jitter))
                    print(
                        f"retry: {fn.__name__} got HTTP {status}; "
                        f"sleeping {delay:.2f}s before attempt {attempt + 1}/{attempts}",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                except httpx.HTTPError as e:
                    if attempt == attempts:
                        raise
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1))) * (
                        1 + random.uniform(0, jitter)
                    )
                    print(
                        f"retry: {fn.__name__} got {type(e).__name__}; "
                        f"sleeping {delay:.2f}s before attempt {attempt + 1}/{attempts}",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
            # Unreachable — every branch above either returns or raises.
            raise RuntimeError("with_retry: control flow invariant violated")

        return wrapper  # type: ignore[return-value]

    return decorator
