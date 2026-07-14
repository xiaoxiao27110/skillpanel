# syntax=docker/dockerfile:1.7

FROM node:24-bookworm-slim AS extension-builder

WORKDIR /build
COPY vscode-extension/package.json vscode-extension/package-lock.json ./
RUN npm ci
COPY vscode-extension/ ./
RUN npm run package

FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG OPENCODE_VERSION=1.17.12
ARG HERMES_VERSION=0.18.2
ARG CODE_SERVER_VERSION=4.121.0
ARG TARGETARCH

ENV PATH="/usr/local/bin:/root/.opencode/bin:/opt/hermes/bin:${PATH}" \
    PYTHONPATH="/opt/skillpanel/src" \
    HERMES_HOME="/root/.hermes" \
    HERMES_SKIP_NODE_BOOTSTRAP="1" \
    HERMES_SKILL_STATE_FILE="/data/skill-state.json" \
    SKILLPANEL_ENABLED_DIR="/root/.config/opencode/skills" \
    SKILLPANEL_DISABLED_DIR="/root/.config/opencode/skills-disabled" \
    SKILLPANEL_STATE_FILE="/data/skill-state.json" \
    SKILLPANEL_OPENCODE_URL="http://127.0.0.1:4096"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git gnupg jq patch software-properties-common supervisor xz-utils \
    && rm -rf /var/lib/apt/lists/*

ADD "https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server-${CODE_SERVER_VERSION}-linux-${TARGETARCH}.tar.gz" /tmp/code-server.tar.gz
RUN case "${TARGETARCH}" in \
      arm64) code_server_sha="de5101a3c1f86b3853431d6920dcf4daaca879f613dec9139496099e31baa569" ;; \
      amd64) code_server_sha="3860893f15376e5f984492c5c92e87c51975d67e3902410f422823d9f60e06af" ;; \
      *) echo "Unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && echo "${code_server_sha}  /tmp/code-server.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/code-server \
    && tar -xzf /tmp/code-server.tar.gz -C /opt/code-server --strip-components=1 \
    && ln -s /opt/code-server/bin/code-server /usr/local/bin/code-server \
    && rm /tmp/code-server.tar.gz \
    && XDG_CONFIG_HOME=/tmp/code-server-version code-server --version \
    && rm -rf /tmp/code-server-version

RUN add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3.12 python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin skillpanel \
    && useradd --create-home --uid 1000 --shell /bin/bash coder

RUN case "${TARGETARCH}" in \
      arm64) package="opencode-linux-arm64" ;; \
      amd64) package="opencode-linux-x64-baseline" ;; \
      *) echo "Unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && mkdir -p /usr/local/libexec \
    && curl -fsSL "https://registry.npmjs.org/${package}/-/${package}-${OPENCODE_VERSION}.tgz" \
      | tar -xz -C /usr/local/libexec --strip-components=2 package/bin/opencode \
    && chmod 0755 /usr/local/libexec/opencode \
    && /usr/local/libexec/opencode --version | grep -Fx "${OPENCODE_VERSION}"

COPY docker/opencode_launcher.py /usr/local/bin/opencode
RUN chmod 0755 /usr/local/bin/opencode \
    && opencode --version | grep -Fx "${OPENCODE_VERSION}"

RUN python3.12 -m venv /opt/hermes \
    && /opt/hermes/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/hermes/bin/pip install --no-cache-dir "hermes-agent[web,pty]==${HERMES_VERSION}" \
    && hermes --version | grep -F "${HERMES_VERSION}"

# Hermes Dashboard Chat launches the prebuilt Ink TUI with Node. The runtime
# image does not otherwise need npm or the builder's dependency tree.
COPY --from=extension-builder /usr/local/bin/node /usr/local/bin/node
RUN node --version | grep -E '^v24\.'

RUN apt-get update \
    && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/skillpanel
COPY src/ /opt/skillpanel/src/
COPY patches/hermes-turn-revision.patch /tmp/hermes-turn-revision.patch
COPY patches/hermes-basic-auth-login.patch /tmp/hermes-basic-auth-login.patch
COPY patches/hermes-bundled-tui.patch /tmp/hermes-bundled-tui.patch
COPY docker/ /opt/skillpanel/docker/
COPY fixtures/ /opt/skillpanel/fixtures/
COPY tests/ /opt/skillpanel/tests/
COPY --from=extension-builder /build/skill-panel.vsix /opt/skillpanel/vscode-extension/skill-panel.vsix

RUN HERMES_SITE="$(/opt/hermes/bin/python -c 'import pathlib, agent; print(pathlib.Path(agent.__file__).parent.parent)')" \
    && cd "${HERMES_SITE}" \
    && patch -p1 < /tmp/hermes-turn-revision.patch \
    && patch -p1 < /tmp/hermes-basic-auth-login.patch \
    && patch -p1 < /tmp/hermes-bundled-tui.patch \
    && rm /tmp/hermes-turn-revision.patch \
      /tmp/hermes-basic-auth-login.patch \
      /tmp/hermes-bundled-tui.patch \
    && chmod 0755 /opt/skillpanel/docker/*.sh \
    && find /opt/skillpanel/src /opt/skillpanel/tests -type f -exec chmod 0644 {} +

EXPOSE 4096 8080 9119 8787

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
  CMD curl -fsS http://127.0.0.1:8787/health >/dev/null \
    && curl -fsS http://127.0.0.1:8080/healthz | jq -e '.status == "alive" or .status == "expired"' >/dev/null \
    || exit 1

ENTRYPOINT ["/opt/skillpanel/docker/entrypoint.sh"]
