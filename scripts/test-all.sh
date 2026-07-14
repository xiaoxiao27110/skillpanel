#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

clean_appledouble() {
  find . \( -name '._*' -o -name '.___*' \) -type f -delete
}

clean_appledouble

initial_states=""
initial_pids=""
hermes_cookie_jar=""
hermes_pty_pid=""
hermes_pty_start_time=""
hermes_pty_processes=""
restore_needed=false

cleanup_curl() {
  curl --connect-timeout 2 --max-time 5 "$@"
}

test_curl() {
  curl --connect-timeout 2 --max-time 10 "$@"
}

canary_states() {
  jq -Sc \
    '.skills | map(select(.name | startswith("canary-")) | {key: .name, value: .enabled}) | from_entries' \
    <<<"$1"
}

assert_process_identity_gone() {
  docker exec skillpanel-poc /usr/bin/python3 -c '
import pathlib
import sys

path = pathlib.Path(f"/proc/{sys.argv[1]}/stat")
try:
    raw = path.read_text(encoding="utf-8")
except OSError:
    raise SystemExit(0)
closing = raw.rfind(")")
fields = raw[closing + 1:].split() if closing >= 0 else []
raise SystemExit(1 if len(fields) > 19 and fields[19] == sys.argv[2] else 0)
' "$1" "$2"
}

restore_initial_state() {
  [[ "${restore_needed}" == true && -n "${initial_states}" ]] || return 0

  local name desired catalog current encoded revision payload restored
  restored=true
  while IFS=$'\t' read -r name desired; do
    [[ -n "${name}" ]] || continue
    encoded="$(jq -rn --arg value "${name}" '$value | @uri')"
    for _ in 1 2 3; do
      if ! catalog="$(cleanup_curl -fsS http://127.0.0.1:8787/skills)"; then
        restored=false
        break
      fi
      current="$(jq -r --arg name "${name}" '.skills[] | select(.name == $name) | .enabled' <<<"${catalog}")"
      if [[ "${current}" == "${desired}" ]]; then
        break
      fi
      if [[ -z "${current}" ]]; then
        echo "Cannot restore missing skill ${name}" >&2
        restored=false
        break
      fi
      revision="$(jq -r '.revision' <<<"${catalog}")"
      payload="$(
        jq -cn \
          --argjson enabled "${desired}" \
          --argjson expected_revision "${revision}" \
          '{enabled: $enabled, expected_revision: $expected_revision}'
      )"
      cleanup_curl -fsS -X PUT "http://127.0.0.1:8787/skills/${encoded}" \
        -H 'Content-Type: application/json' \
        --data "${payload}" >/dev/null || true
    done

    catalog="$(cleanup_curl -fsS http://127.0.0.1:8787/skills 2>/dev/null || true)"
    current="$(
      jq -r --arg name "${name}" '.skills[] | select(.name == $name) | .enabled' \
        <<<"${catalog}" 2>/dev/null || true
    )"
    if [[ "${current}" != "${desired}" ]]; then
      echo "Failed to restore ${name} to enabled=${desired}" >&2
      restored=false
    fi
  done < <(jq -r 'to_entries[] | [.key, (.value | tostring)] | @tsv' <<<"${initial_states}")

  [[ "${restored}" == true ]]
}

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if [[ -n "${hermes_cookie_jar}" ]]; then
    rm -f "${hermes_cookie_jar}"
  fi
  if ! restore_initial_state; then
    echo "Warning: test cleanup could not fully restore the initial skill state." >&2
    if [[ "${status}" == 0 ]]; then
      status=1
    fi
  fi
  exit "${status}"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

pre_run_catalog="$(cleanup_curl -fsS http://127.0.0.1:8787/skills 2>/dev/null || true)"
if jq -e '.skills | any(.name | startswith("canary-"))' \
  <<<"${pre_run_catalog}" >/dev/null 2>&1; then
  initial_states="$(canary_states "${pre_run_catalog}")"
  restore_needed=true
fi

section() {
  printf '\n==> %s\n' "$1"
}

section "Host unit tests"
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v

section "VS Code extension checks"
(
  cd vscode-extension
  npm ci
  audit_ok=false
  for _ in 1 2 3; do
    if npm audit; then
      audit_ok=true
      break
    fi
    sleep 2
  done
  test "${audit_ok}" = true
  npm run check
  npm run test:unit
  npm run test:extension
  npm run package
)

section "Compose and image"
clean_appledouble
docker compose config --quiet
docker compose build skillpanel
docker compose up -d --force-recreate skillpanel

section "Wait for controller and code-server"
healthy=false
for _ in $(seq 1 90); do
  if test_curl -fsS http://127.0.0.1:8787/health | jq -e '.ok == true' >/dev/null \
    && test_curl -fsS http://127.0.0.1:8080/healthz | jq -e '.status == "alive" or .status == "expired"' >/dev/null \
    && test_curl -fsS http://127.0.0.1:9119/login >/dev/null \
    && docker exec skillpanel-poc supervisorctl status \
      | awk 'BEGIN { ok=1 } NF && $2 != "RUNNING" { ok=0 } END { exit !ok }'; then
    healthy=true
    break
  fi
  sleep 1
done
if [[ "${healthy}" != true ]]; then
  docker compose logs --tail=200 skillpanel
  exit 1
fi

initial_catalog="$(test_curl -fsS http://127.0.0.1:8787/skills)"
current_states="$(canary_states "${initial_catalog}")"
if [[ -z "${initial_states}" ]]; then
  initial_states="${current_states}"
  restore_needed=true
else
  test "$(jq -Sc 'keys' <<<"${current_states}")" = "$(jq -Sc 'keys' <<<"${initial_states}")"
fi
initial_pids="$(jq -Sc '.pids' <<<"${initial_catalog}")"

section "Container smoke checks"
docker exec --user coder -e HOME=/home/coder skillpanel-poc code-server --version | grep -F '4.121.0'
docker exec skillpanel-poc node --version | grep -E '^v24\.'
docker exec --user coder -e HOME=/home/coder skillpanel-poc \
  code-server \
  --extensions-dir /home/coder/.local/share/code-server/extensions \
  --list-extensions --show-versions \
  | grep -Fx 'skillpanel.skill-panel@0.1.0'
docker exec skillpanel-poc supervisorctl status | awk 'BEGIN { ok=1 } NF && $2 != "RUNNING" { ok=0 } END { exit !ok }'
docker exec skillpanel-poc opencode generate | jq -e '.openapi == "3.1.0"' >/dev/null
if console_output="$(docker exec skillpanel-poc opencode console 2>&1)"; then
  console_status=0
else
  console_status=$?
fi
test "${console_status}" = '1'
grep -F 'opencode console' <<<"${console_output}" >/dev/null
if grep -F 'opencode launcher:' <<<"${console_output}" >/dev/null; then
  echo "opencode console was intercepted by the TUI launcher" >&2
  exit 1
fi
if mini_output="$(docker exec skillpanel-poc opencode --mini 2>&1)"; then
  echo "opencode --mini unexpectedly started" >&2
  exit 1
else
  mini_status=$?
fi
test "${mini_status}" = '2'
grep -F -- '--mini is unsupported' <<<"${mini_output}" >/dev/null
docker exec skillpanel-poc sh -lc '
  test "$(stat -c "%U:%G:%a" /run/skillpanel)" = root:root:700
  test "$(stat -c "%U:%G:%a" /run/skillpanel/opencode-auth.json)" = root:root:600
  test "$(stat -c "%U:%G:%a" /run/skillpanel-opencode-tuis)" = root:skillpanel:750
  test "$(stat -c "%U:%G:%a" /data/skill-state.lock)" = skillpanel:skillpanel:660
'
for port in 4096 8080 8787 9119; do
  test "$(docker port skillpanel-poc "${port}/tcp")" = "127.0.0.1:${port}"
done
docker exec skillpanel-poc test -f /workspace/HANDOFF.md

code_server_uid="$(
  docker exec skillpanel-poc sh -lc \
    "awk '/^Uid:/ { print \$2 }' /proc/\$(cat /run/code-server.pid)/status"
)"
test "${code_server_uid}" = '1000'

code_server_command="$(
  docker exec skillpanel-poc sh -lc \
    "tr '\\0' ' ' < /proc/\$(cat /run/code-server.pid)/cmdline"
)"
if [[ "${code_server_command}" == *"${CODE_SERVER_PASSWORD:-skillpanel-dev}"* ]]; then
  echo "code-server password leaked into the process command line" >&2
  exit 1
fi

unauthenticated_status="$(test_curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/)"
test "${unauthenticated_status}" = '302'

hermes_root_headers="$(test_curl -sS -D - -o /dev/null http://127.0.0.1:9119/)"
hermes_root_status="$(awk 'NR == 1 { print $2 }' <<<"${hermes_root_headers}")"
hermes_root_location="$(
  awk 'tolower($1) == "location:" { print $2; exit }' <<<"${hermes_root_headers}" | tr -d '\r'
)"
case "${hermes_root_status}:${hermes_root_location}" in
  30[12378]:/login | 30[12378]:/login\?*) ;;
  *)
    echo "Hermes Dashboard root did not redirect to /login (status=${hermes_root_status}, location=${hermes_root_location})" >&2
    exit 1
    ;;
esac

hermes_password_provider="$(
  test_curl -fsS http://127.0.0.1:9119/api/auth/providers \
    | jq -er '.providers[] | select(.supports_password == true) | .name' \
    | head -n 1
)"
hermes_cookie_jar="$(mktemp "${TMPDIR:-/tmp}/skillpanel-hermes-cookie.XXXXXX")"
hermes_login_response="$(
  HERMES_TEST_PROVIDER="${hermes_password_provider}" \
  HERMES_TEST_USER="${HERMES_DASHBOARD_USER:-skillpanel}" \
  HERMES_TEST_PASSWORD="${HERMES_DASHBOARD_PASSWORD:-skillpanel-dev}" \
    jq -cn \
      '{provider: env.HERMES_TEST_PROVIDER, username: env.HERMES_TEST_USER, password: env.HERMES_TEST_PASSWORD, next: "/"}' \
    | test_curl -fsS \
        -c "${hermes_cookie_jar}" \
        -H 'Content-Type: application/json' \
        --data-binary @- \
        http://127.0.0.1:9119/auth/password-login
)"
jq -e '.ok == true and .next == "/"' <<<"${hermes_login_response}" >/dev/null
hermes_authenticated_status="$(
  test_curl -sS -b "${hermes_cookie_jar}" -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:9119/
)"
test "${hermes_authenticated_status}" = '200'
rm -f "${hermes_cookie_jar}"
hermes_cookie_jar=""

hermes_pty_summary="$(
  docker exec skillpanel-poc /opt/hermes/bin/python \
    /opt/skillpanel/tests/hermes_dashboard_pty_smoke.py
)"
jq -e \
  '.ok == true and .graceful_reap == true and .pid_removed == true and .process_group_empty == true and .source_workspace_absent == true and (.processes | length > 0)' \
  <<<"${hermes_pty_summary}" >/dev/null
hermes_pty_pid="$(jq -r '.pid' <<<"${hermes_pty_summary}")"
hermes_pty_start_time="$(jq -r '.start_time' <<<"${hermes_pty_summary}")"
hermes_pty_processes="$(jq -c '.processes' <<<"${hermes_pty_summary}")"
jq '{dashboard_pid,pid,start_time,pgid,argv,processes,source_workspace_absent,graceful_reap,pid_removed,process_group_empty}' \
  <<<"${hermes_pty_summary}"

section "Supervisor restarts code-server after an abnormal exit"
code_server_pid_before="$(docker exec skillpanel-poc cat /run/code-server.pid)"
code_server_descendants_before="$(
  docker exec skillpanel-poc sh -lc '
    descendants() {
      for child in $(pgrep -P "$1" 2>/dev/null || true); do
        echo "$child"
        descendants "$child"
      done
    }
    descendants "$(cat /run/code-server.pid)"
  '
)"
docker exec skillpanel-poc supervisorctl signal KILL code-server >/dev/null
code_server_restarted=false
for _ in $(seq 1 30); do
  code_server_pid_after="$(docker exec skillpanel-poc cat /run/code-server.pid 2>/dev/null || true)"
  if [[ -n "${code_server_pid_after}" && "${code_server_pid_after}" != "${code_server_pid_before}" ]] \
    && docker exec skillpanel-poc supervisorctl status code-server | awk '$2 == "RUNNING" { found=1 } END { exit !found }' \
    && test_curl -fsS http://127.0.0.1:8080/healthz \
      | jq -e '.status == "alive" or .status == "expired"' >/dev/null; then
    code_server_restarted=true
    break
  fi
  sleep 1
done
test "${code_server_restarted}" = true
while IFS= read -r old_pid; do
  [[ -n "${old_pid}" ]] || continue
  docker exec skillpanel-poc test ! -e "/proc/${old_pid}"
done <<<"${code_server_descendants_before}"
test "$(test_curl -fsS http://127.0.0.1:8787/skills | jq -Sc '.pids')" = "${initial_pids}"
test "$(
  docker exec skillpanel-poc sh -lc \
    "awk '/^Uid:/ { print \$2 }' /proc/\$(cat /run/code-server.pid)/status"
)" = '1000'
docker exec --user coder -e HOME=/home/coder skillpanel-poc \
  code-server \
  --extensions-dir /home/coder/.local/share/code-server/extensions \
  --list-extensions --show-versions \
  | grep -Fx 'skillpanel.skill-panel@0.1.0'
restarted_code_server_command="$(
  docker exec skillpanel-poc sh -lc \
    "tr '\\0' ' ' < /proc/\$(cat /run/code-server.pid)/cmdline"
)"
if [[ "${restarted_code_server_command}" == *"${CODE_SERVER_PASSWORD:-skillpanel-dev}"* ]]; then
  echo "code-server password leaked into the restarted process command line" >&2
  exit 1
fi
test "$(test_curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/)" = '302'

section "Container unit and model-free integration"
docker exec skillpanel-poc /opt/hermes/bin/python -m unittest discover \
  -s /opt/skillpanel/tests -p 'test_*.py' -v
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/integration.py --cycles 2
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/opencode_tui_session.py
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/opencode_tui_session.py \
  --directory /workspace/vscode-extension/test-fixtures/workspace
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/integration.py --cycles 100

if [[ "${RUN_LLM_TESTS:-0}" == '1' ]]; then
  section "Real-model same-session tests"
  docker exec skillpanel-poc /opt/hermes/bin/python \
    /opt/skillpanel/tests/integration.py --cycles 0 --hermes-llm-canary
  docker exec skillpanel-poc /opt/hermes/bin/python \
    /opt/skillpanel/tests/opencode_tui_session.py --llm
  docker exec skillpanel-poc /opt/hermes/bin/python \
    /opt/skillpanel/tests/comparisons.py --llm-native-reload
fi

section "Final state"
restore_initial_state
final_catalog="$(test_curl -fsS http://127.0.0.1:8787/skills)"
final_states="$(
  canary_states "${final_catalog}"
)"
test "${final_states}" = "${initial_states}"
test "$(jq -Sc '.pids' <<<"${final_catalog}")" = "${initial_pids}"
jq -e '.reconciliations == []' <<<"${final_catalog}" >/dev/null
docker exec skillpanel-poc sh -lc \
  'test -z "$(find /run/skillpanel-opencode-tuis -mindepth 1 -maxdepth 1 -print -quit)"'
if [[ -n "${hermes_pty_pid}" ]]; then
  assert_process_identity_gone "${hermes_pty_pid}" "${hermes_pty_start_time}"
  while IFS=$'\t' read -r process_pid process_start_time; do
    [[ -n "${process_pid}" && "${process_pid}" != "${hermes_pty_pid}" ]] || continue
    assert_process_identity_gone "${process_pid}" "${process_start_time}"
  done < <(jq -r '.[] | [.pid, .start_time] | @tsv' <<<"${hermes_pty_processes}")
fi
jq '{revision,skills,pids,reconciliations}' <<<"${final_catalog}"
restore_needed=false
printf '\nAll automated gates passed.\n'
