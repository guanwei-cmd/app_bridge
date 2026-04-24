#!/usr/bin/env bash
# scripts/deploy_surge.sh — rebuild dashboard + ship to Surge
#
# Usage:
#   bash scripts/deploy_surge.sh                              # first time: will prompt for domain
#   bash scripts/deploy_surge.sh bridge-analyst-dorian        # reuse existing domain
set -euo pipefail

cd "$(dirname "$0")/.."   # cd to repo root

DOMAIN="${1:-}"

# 1. Rebuild dashboard from latest DB
python -m scripts.build_dashboard

# 2. Stage for Surge (wants a folder with index.html)
mkdir -p deploy
cp reports/dashboard.html deploy/index.html

# 3. Deploy
if [ -n "$DOMAIN" ]; then
    surge deploy/ "$DOMAIN.surge.sh"
else
    surge deploy/
fi
