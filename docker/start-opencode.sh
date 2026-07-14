#!/usr/bin/env bash
set -euo pipefail
echo $$ > /run/opencode.pid
exec /usr/local/libexec/opencode serve --hostname 0.0.0.0 --port 4096
