#!/usr/bin/env bash
set -euo pipefail

auth_source=/run/secrets/opencode-auth.json
auth_runtime_dir=/run/skillpanel
auth_runtime="${auth_runtime_dir}/opencode-auth.json"
auth_target=/root/.local/share/opencode/auth.json
opencode_tui_dir="${SKILLPANEL_OPENCODE_TUI_DIR:-/run/skillpanel-opencode-tuis}"
state_file="${SKILLPANEL_STATE_FILE:-/data/skill-state.json}"
state_lock="$(/usr/bin/python3 -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).with_suffix(".lock"))' "${state_file}")"
provider="${SKILLPANEL_OPENCODE_PROVIDER:-minimax-cn-coding-plan}"

if [[ ! -r "${auth_source}" ]]; then
  echo "Missing read-only OpenCode auth file at ${auth_source}" >&2
  exit 1
fi

jq -er --arg provider "${provider}" '.[$provider].key | select(type == "string" and length > 0)' "${auth_source}" >/dev/null || {
  echo "OpenCode auth file has no API key for ${provider}" >&2
  exit 1
}

mkdir -p \
  "${auth_runtime_dir}" \
  /root/.local/share/opencode \
  /root/.config/opencode \
  /root/.hermes/skills \
  /home/coder/.config/code-server \
  /home/coder/.local/share/code-server/extensions \
  /data \
  /workspace
chown root:root "${auth_runtime_dir}"
chmod 0700 "${auth_runtime_dir}"
install -d -o root -g skillpanel -m 0750 "${opencode_tui_dir}"
mkdir -p "$(dirname "${state_lock}")"
touch "${state_lock}"
chown skillpanel:skillpanel "${state_lock}"
chmod 0660 "${state_lock}"
jq -ce --arg provider "${provider}" '{($provider): .[$provider]}' "${auth_source}" > "${auth_runtime}"
chmod 0600 "${auth_runtime}"
ln -sfn "${auth_runtime}" "${auth_target}"

enabled="${SKILLPANEL_ENABLED_DIR}"
disabled="${SKILLPANEL_DISABLED_DIR}"
mkdir -p "${enabled}" "${disabled}"

for fixture in /opt/skillpanel/fixtures/skills/*; do
  name="$(basename "${fixture}")"
  if [[ ! -e "${enabled}/${name}" && ! -e "${disabled}/${name}" ]]; then
    cp -a "${fixture}" "${enabled}/${name}"
  fi
done

/opt/hermes/bin/python -m bootstrap_config

chown -R skillpanel:skillpanel "${enabled}" "${disabled}" /data
chmod 0711 /root
chmod 0755 /root/.config /root/.config/opencode
touch /run/controller.pid
chown skillpanel:skillpanel /run/controller.pid
touch /run/code-server.pid
chown coder:coder /run/code-server.pid
chown -R coder:coder /home/coder/.config /home/coder/.local

exec /usr/bin/supervisord -c /opt/skillpanel/docker/supervisord.conf
