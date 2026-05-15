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

BASELINE=23
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
