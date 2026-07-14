#!/usr/bin/env bash
set -euo pipefail
provider="${SKILLPANEL_OPENCODE_PROVIDER:-minimax-cn-coding-plan}"
export MINIMAX_CN_API_KEY="$(jq -er --arg provider "${provider}" '.[$provider].key' /run/secrets/opencode-auth.json)"
echo $$ > /run/hermes.pid
exec hermes dashboard \
  --host 0.0.0.0 \
  --port 9119 \
  --skip-build \
  --no-open
