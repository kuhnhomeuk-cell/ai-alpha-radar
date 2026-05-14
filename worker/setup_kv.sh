#!/usr/bin/env bash
# Provision the RADAR_KV namespaces (prod + preview) on Cloudflare.
# Requires: cloudflare auth (`npx wrangler login` first) and a Cloudflare account.
#
# Usage: ./setup_kv.sh
# Prints the two ids the operator must paste into wrangler.toml.

set -euo pipefail

cd "$(dirname "$0")"

echo "==> creating production RADAR_KV namespace"
npx wrangler kv:namespace create RADAR_KV

echo
echo "==> creating preview RADAR_KV namespace"
npx wrangler kv:namespace create RADAR_KV --preview

echo
echo "Copy the two ids printed above into wrangler.toml — replace the"
echo "PLACEHOLDER_RADAR_KV_ID and PLACEHOLDER_RADAR_KV_PREVIEW_ID lines."
echo
echo "Then:"
echo "  npx wrangler deploy"
echo "  curl https://<worker>.workers.dev/api/spend?date=\$(date -u +%Y-%m-%d)"
