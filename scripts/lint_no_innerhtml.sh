#!/usr/bin/env bash
# Audit 4.6 — guard against new `.innerHTML =` assignments in public/index.html.
#
# The current LLM-string render path is escapeHtml-on-every-interpolation
# (verified once during 4.6). That's a correct XSS defense, but it relies on
# every contributor remembering to escape — a property that erodes silently.
#
# This lint counts the .innerHTML assignment sites and fails CI if the
# baseline grows. New sites must either: (a) use textContent/createElement
# instead, or (b) bump the baseline below intentionally, with a PR note
# explaining why the new site is safe.

set -euo pipefail

# Bumped 23 → 24 on 2026-05-16: redesign promotion added one new site
# (copy-to-clipboard toast micro-template, static HTML, no user data).
# Bumped 24 → 27 on 2026-05-17: three new sites accumulated since the
# last bump — the bubble-chart stage legend (static color/label pairs
# from a hardcoded zones array, no user data), the leaderboard-empty
# banner (static fallback strings only), and a bubble tooltip body
# (every interpolation routed through escapeHtml). All three were
# verified by hand against this script's intent.
# Bumped 27 → 28 on 2026-05-17: design-engineering polish pass added the
# aarToast() helper (Phase 5). The `el.innerHTML = msg` site is the toast
# message-formatting affordance — callers pass HTML fragments like
# '<em>Copied</em>' so the toast can style accent spans. All three current
# call sites (watchlist save, Almanac copy, Comets copy) route user data
# through escapeHtml before composing the message. Same XSS-defense pattern
# this script's intent already permits (see prior bumps).
BASELINE=28
TARGET="public/index.html"

if [[ ! -f "$TARGET" ]]; then
  echo "lint_no_innerhtml: $TARGET not found" >&2
  exit 1
fi

COUNT=$(grep -c '\.innerHTML\s*=' "$TARGET" || true)

if [[ "$COUNT" -gt "$BASELINE" ]]; then
  echo "lint_no_innerhtml: FAIL — $COUNT .innerHTML assignments in $TARGET (baseline $BASELINE)" >&2
  echo "Replace new sites with .textContent or document.createElement, or bump BASELINE in this script with rationale." >&2
  grep -n '\.innerHTML\s*=' "$TARGET" >&2
  exit 1
fi

echo "lint_no_innerhtml: ok — $COUNT .innerHTML sites (baseline $BASELINE)"
