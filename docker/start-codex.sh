#!/usr/bin/env bash
set -euo pipefail
echo $$ > /run/codex.pid
# Codex CLI is an interactive TUI; it reads the shared skill pool through the
# /root/.codex/skills symlink created by entrypoint.sh. Toggles take effect on
# the next Codex session ("codex_refresh": "next-session"). Run in the
# foreground so supervisord owns the process lifecycle.
exec codex
