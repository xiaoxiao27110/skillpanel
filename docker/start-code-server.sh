#!/usr/bin/env bash
set -euo pipefail

: "${CODE_SERVER_PASSWORD:?CODE_SERVER_PASSWORD is required}"
export PASSWORD="${CODE_SERVER_PASSWORD}"

code-server \
  --user-data-dir /home/coder/.local/share/code-server \
  --extensions-dir /home/coder/.local/share/code-server/extensions \
  --install-extension /opt/skillpanel/vscode-extension/skill-panel.vsix \
  --force >/dev/null

echo $$ > /run/code-server.pid
exec code-server \
  --bind-addr 0.0.0.0:8080 \
  --auth password \
  --disable-telemetry \
  --disable-update-check \
  --disable-workspace-trust \
  --user-data-dir /home/coder/.local/share/code-server \
  --extensions-dir /home/coder/.local/share/code-server/extensions \
  /workspace
