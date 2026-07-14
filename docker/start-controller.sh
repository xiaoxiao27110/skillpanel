#!/usr/bin/env bash
set -euo pipefail
echo $$ > /run/controller.pid
exec /opt/hermes/bin/python -m uvicorn controller:app --app-dir /opt/skillpanel/src --host 0.0.0.0 --port 8787

