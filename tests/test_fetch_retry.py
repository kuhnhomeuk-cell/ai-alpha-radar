"""TDD for pipeline.fetch._retry — exponential backoff wrapper.

Tests cover the decorator's contract via mocked target functions that raise
specific httpx errors. The fetchers themselves get integration coverage in
audit item 4.5.
"""

from __future__ import annotations

import httpx
import pytest

from pipeline.fetch import _retry


def _make_status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.invalid/")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("simulated", request=request, response=response)


def test_with_retry_succeeds_after_transient_503() -> None:
    calls = {"n": 0}

    @_retry.with_retry(attempts=3, base_delay=0.0, retry_on={503})
    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise _make_status_error(503)
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2


def test_with_retry_exhausts_and_raises_on_persistent_500() -> None:
    calls = {"n": 0}

    @_retry.with_retry(attempts=3, base_delay=0.0)
    def always_500() -> None:
        calls["n"] += 1
        raise _make_status_error(500)

    with pytest.raises(httpx.HTTPStatusError) as exc:
        always_500()
    assert exc.value.response.status_code == 500
    assert calls["n"] == 3


def test_with_retry_does_not_retry_non_retryable_status() -> None:
    calls = {"n": 0}

    @_retry.with_retry(attempts=3, base_delay=0.0, retry_on={500, 503})
    def auth_failure() -> None:
        calls["n"] += 1
        raise _make_status_error(401)

    with pytest.raises(httpx.HTTPStatusError):
        auth_failure()
    assert calls["n"] == 1  # no retry on 401


def test_with_retry_retries_network_errors() -> None:
    calls = {"n": 0}

    @_retry.with_retry(attempts=3, base_delay=0.0)
    def network_glitch() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("simulated", request=httpx.Request("GET", "https://x.invalid"))
        return "ok"

    assert network_glitch() == "ok"
    assert calls["n"] == 3


def test_with_retry_honors_retry_after_numeric(monkeypatch) -> None:
    """When 429 is returned with Retry-After: <seconds>, sleep that long."""
    slept: list[float] = []
    monkeypatch.setattr(_retry.time, "sleep", lambda s: slept.append(s))
    calls = {"n": 0}

    @_retry.with_retry(attempts=3, base_delay=10.0, retry_on={429}, jitter=0.0)
    def rate_limited() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise _make_status_error(429, headers={"retry-after": "2"})
        return "ok"

    assert rate_limited() == "ok"
    # First (and only) sleep should be ~2s from Retry-After, not the 10s base_delay.
    assert len(slept) == 1
    assert 1.9 <= slept[0] <= 2.1


def test_with_retry_propagates_non_httpx_errors() -> None:
    @_retry.with_retry(attempts=3, base_delay=0.0)
    def boom() -> None:
        raise ValueError("not an http error")

    with pytest.raises(ValueError):
        boom()


def test_parse_retry_after_numeric() -> None:
    assert _retry._parse_retry_after("5") == 5.0
    assert _retry._parse_retry_after("2.5") == 2.5


def test_parse_retry_after_invalid_returns_none() -> None:
    assert _retry._parse_retry_after("") is None
    assert _retry._parse_retry_after("not-a-date") is None
