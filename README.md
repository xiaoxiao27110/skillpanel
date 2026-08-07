# SkillPanel 双 Agent Skill 热切换 PoC

这是一个 Ubuntu 22.04 单容器验证环境，用来验证 OpenCode 与 Hermes 共用同一套 skill 时，能否在不重启两个主进程的前提下，于同一 session 的下一轮对话看到开关结果。

本仓库包含验证环境、最小控制面和 VS Code workspace 插件。实际运行结果、版本对比和延迟数据应记录在 `REPORT.md`；本文只描述实现、接口和复现方法，不预先声明集成验证已经通过。

## 架构

容器内由 Supervisor 长期运行四个进程：

- OpenCode server：`0.0.0.0:4096`
- Hermes Dashboard（带完整 Web UI）：`0.0.0.0:9119`
- SkillPanel 控制器：`0.0.0.0:8787`
- code-server：`0.0.0.0:8080`

宿主机端口全部只绑定到 `127.0.0.1`。

除此之外，用户在容器终端直接输入 `opencode` 时会启动一个临时 TUI runtime。它只监听随机的 `127.0.0.1` 端口，不映射到宿主机；TUI 退出时 runtime 和登记一起清理。

```text
VS Code/测试客户端
        |
        | GET /skills, PUT /skills/{name}
        v
SkillPanel controller :8787
        |
        +-- 原子 rename skill 整个目录并提交 revision
        +-- 同步刷新并校验每个 OpenCode runtime
        |       |
        |       +-- Supervisor server :4096
        |       +-- 裸 TUI：随机 127.0.0.1 端口
        |
        +-- Hermes :9119 下一轮读取 revision、清缓存并重扫
        |
        v
~/.agents/skills
```

开关使用两个同文件系统目录：

- 启用：`~/.agents/skills`
- 禁用：`~/.agents/skills-disabled`

控制器用 `os.rename()` 移动整个 skill 包，因此 `SKILL.md`、`scripts/`、`references/`、`assets/` 会一起切换。两个目录都位于 `agents-home` named volume 内，这是原子移动成立的必要条件。

状态保存在 `/data/skill-state.json`。状态文件通过临时文件、文件 `fsync`、`os.replace()` 和目录 `fsync` 提交；控制器与 TUI 启动器共用 `/data/skill-state.lock`，把扫描、写入和新 runtime 的启动登记串行化。

OpenCode 的刷新通过同步 `POST /global/dispose` 完成。它释放进程内 instance 缓存而不重启进程，随后控制器调用 `GET /skill` 与 `GET /command` 校验目标 skill 是否收敛。控制器既刷新 Supervisor server，也读取 `/run/skillpanel-opencode-tuis/*.json`，刷新所有仍存活的直接 TUI runtime；任一存活 runtime 不可达或 catalog 未收敛，整个 PUT 都失败并回滚。

`opencode` 是标准 TUI 的透明启动器：没有子命令时保留原生 TUI 体验，但强制原生 runtime 使用随机 loopback 端口，并原子登记原生 PID、`/proc` start time、工作目录和 URL。`serve`、`run`、`attach`、`generate`、`console` 等子命令原样交给内部原生二进制。日常使用直接运行 `opencode` 即可获得热切换，不要求改用 `opencode attach`。OpenCode 1.17.12 的 `--mini` 与外部 runtime 网络参数不兼容，因此启动器会明确报错并返回 2，不会悄悄启动一个无法热刷新的会话。

Hermes 0.18.2 在构建镜像时应用 `patches/hermes-turn-revision.patch`。每轮恢复普通 session prompt 后，补丁会：

1. 比较 prompt 中的 revision marker 与 `/data/skill-state.json`。
2. revision 变化时清理 skill system-prompt 缓存并调用 Hermes 原生 `reload_skills()`。
3. 重建 `_cached_system_prompt`，加入新 marker，并更新 session DB 中的 system prompt。
4. revision 未变化时不重复重建；未设置 `HERMES_SKILL_STATE_FILE` 时完全不生效。

镜像同时应用 `patches/hermes-basic-auth-login.patch`，避免仅配置密码 provider 时把未登录用户错误送入 OAuth 路由；Dashboard 根路径会进入 `/login`，而不是返回 500。`patches/hermes-bundled-tui.patch` 让 wheel 直接使用随包发布的预编译 TUI，Dashboard Chat 不再错误依赖缺失的 `ui-tui` 源码目录；最终镜像只从 Node builder 复制 Node 24 runtime，不复制 npm 依赖树。

## 仓库与容器目录

```text
.
├── Dockerfile                         # Ubuntu、OpenCode、Hermes、code-server 与 VSIX 构建
├── compose.yaml                       # 单服务、端口、凭据和持久卷
├── docker/                            # entrypoint、Supervisor 与进程启动脚本
├── fixtures/skills/                   # canary-alpha / canary-beta
├── patches/hermes-turn-revision.patch # Hermes 0.18.2 隔离补丁
├── patches/hermes-basic-auth-login.patch # Hermes 密码登录路由修复
├── patches/hermes-bundled-tui.patch    # wheel 内置 TUI 启动修复
├── src/controller.py                  # GET/PUT 控制 API
├── src/skillpanel_hot_reload.py       # Hermes revision hook
├── vscode-extension/                  # SkillPanel workspace extension 与测试 fixture
├── scripts/test-all.sh                # 可重复的自动化测试入口
├── TEST_PLAN.md                       # 需求—证据矩阵和真实 UI 验收流程
├── REPORT.md                          # 当前镜像、插件和真实 UI 验收证据
└── tests/                             # backend unit 与黑盒 integration/stress
```

主要持久路径：

| Named volume         | 容器路径                                 | 内容                                                    |
| -------------------- | ---------------------------------------- | ------------------------------------------------------- |
| `opencode-config`  | `/root/.config/opencode`               | OpenCode 配置                                             |
| `opencode-state`   | `/root/.local/share/opencode`          | OpenCode 持久状态；auth 路径指向 tmpfs 中的最小凭据文件 |
| `agents-home`      | `/root/.agents`(即 `$HOME/.agents`) | 共享 skill 池：启用和禁用目录                             |
| `hermes-home`      | `/root/.hermes`                        | Hermes 配置和 session 数据                              |
| `codex-home`       | `/root/.codex`                         | Codex 配置和 session 数据                               |
| `code-server-data` | `/home/coder/.local/share/code-server` | code-server 用户状态和已安装 VSIX                       |
| `skillpanel-state` | `/data`                                | revision 状态和控制锁                                   |

TUI registry 位于容器 tmpfs 的 `/run/skillpanel-opencode-tuis`，与存放最小鉴权文件的 `0700` 目录分离，也不是持久状态。登记文件由 root 创建、controller 组只读，并以 PID start time 防止 PID 复用；控制器只接受绝对工作目录和 `http://127.0.0.1:<port>`。

首次启动会把两个 canary fixture 复制到启用目录；如果同名目录已经存在于启用或禁用根目录，启动脚本不会覆盖它。

## 前置条件与凭据

本项目统一使用 Colima 的默认 profile。它对应 Docker context 和 buildx builder `colima`：

```bash
colima start
docker context use colima
docker buildx use colima
docker context show
docker buildx inspect --bootstrap
```

默认读取宿主机：

```text
~/.local/share/opencode/auth.json
```

其中必须存在非空的 `minimax-cn-coding-plan.key`。可在不输出 key 的情况下检查：

```bash
jq -e '.["minimax-cn-coding-plan"].key | type == "string" and length > 0' \
  "$HOME/.local/share/opencode/auth.json"
```

也可以在 Compose 命令前设置绝对路径：

```bash
export OPENCODE_AUTH_FILE=/absolute/path/to/auth.json
```

Compose 将文件只读挂载为 `/run/secrets/opencode-auth.json:ro`，不会把它 COPY 进镜像。entrypoint 只提取指定 provider 对象到 `/run/skillpanel/opencode-auth.json`；该目录是 `mode=0700` 的 tmpfs，最小文件是 `0600`，OpenCode 的 auth 路径只指向这个内存文件。Hermes 启动脚本只提取该 provider 的 key 到 Hermes 进程环境变量 `MINIMAX_CN_API_KEY`。原始 auth、最小 auth 和 key 都不会进入镜像、持久卷或日志。

默认模型配置为：

- OpenCode provider：`minimax-cn-coding-plan`
- Hermes provider：`minimax-cn`
- model：`MiniMax-M2.7`

可以通过 `SKILLPANEL_OPENCODE_PROVIDER`、`SKILLPANEL_HERMES_PROVIDER` 和 `SKILLPANEL_MODEL` 覆盖，但当前 Hermes 启动脚本固定导出 `MINIMAX_CN_API_KEY`，因此更换为非 MiniMax provider 需要同步调整启动脚本。

## 构建与启动

默认组合是 OpenCode 1.17.12、Hermes 0.18.2、code-server 4.121.0（Code 1.121.0）与 SkillPanel 0.1.0：

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs -f skillpanel
```

健康检查：

```bash
curl -fsS http://127.0.0.1:8787/health | jq
curl -fsS http://127.0.0.1:4096/global/health | jq
curl -fsS http://127.0.0.1:8080/healthz | jq
```

控制器健康响应包含当前 revision、skill 数量和三个进程 PID。Supervisor 仍在启动时，`ok` 可能暂时为 `false`。

Hermes dashboard 默认 Basic Auth 是 `skillpanel / skillpanel-dev`。未登录访问 `http://127.0.0.1:9119/` 应进入 `/login`；出现 500 视为 UI smoke 失败。启动共享或长期环境前必须覆盖：

```bash
export HERMES_DASHBOARD_USER=your-user
export HERMES_DASHBOARD_PASSWORD='a-long-random-password'
docker compose up -d --force-recreate
```

code-server 默认密码同样只适合本机 PoC：

```bash
export CODE_SERVER_PASSWORD='a-long-random-password'
docker compose up -d --force-recreate
```

code-server 以非 root 的 `coder` 用户运行，密码只通过环境变量传入，端口只绑定宿主 `127.0.0.1`。

## VS Code 插件

镜像构建阶段会编译并打包 `vscode-extension/skill-panel.vsix`，code-server 每次启动时用 `--force` 安装镜像内的准确版本。浏览器访问：

```text
http://127.0.0.1:8080/?folder=/workspace
```

code-server 是浏览器版 VS Code，不是桌面 VS Code 的远程连接协议。桌面端应安装官方 Dev Containers 扩展并执行 **Dev Containers: Attach to Running Container...**，然后选择 `skillpanel-poc`。Attach 建立的是另一套远程 Extension Host，不会复用 code-server 的扩展目录；进入容器窗口后还要执行 **Extensions: Install from VSIX...**，选择 `/workspace/vscode-extension/skill-panel.vsix`。两种 UI 中插件都以 `extensionKind: ["workspace"]` 运行，因此 `127.0.0.1:8787` 都指向容器内同一个控制器。

若 VS Code 1.128 与 Dev Containers 0.463 的本地扩展宿主日志出现 `PendingMigrationError: navigator is now a global in nodejs`，可临时在宿主 VS Code 用户设置加入 `"extensions.supportNodeGlobalNavigator": true` 并重新加载窗口；升级到已修复的扩展版本后应移除这个兼容开关。

插件只调用控制协议，不直接移动目录或修改 Agent 配置。原生 Tree View 展示“已启用、已禁用、项目级、冲突”四组：全局 skill 整行点击切换，pending 期间禁止再次点击；项目级 skill 只读。项目扫描 `.opencode/skills`、`.agents/skills`、`.claude/skills`，向上止于 Git 根，多 workspace 同名来源合并。外部变化只在手动刷新后显示；409 会提示并刷新但不自动重试，503/507 会显示回滚错误并重新读取真实状态。控制器暂时不可达时保留上次成功 catalog 供查看，明确标注缓存和离线状态，并禁用切换；恢复后手动刷新即可重新启用操作。顶部只提示“开关成功后，从下一轮对话开始生效”。已经开始的当前轮不会撤销；PUT 成功返回后，受管 server、直接 `opencode` TUI 和 Hermes 的同 session 下一轮都使用新状态。

停止容器但保留状态：

```bash
docker compose down
```

不要使用 `docker compose down -v`，除非明确要删除 OpenCode、Hermes、skill 开关状态和所有 named volumes。

## Skill 控制 API

控制器基址：`http://127.0.0.1:8787`。

### `GET /skills`

返回当前扫描结果、revision 和 PID：

```bash
curl -fsS http://127.0.0.1:8787/skills | jq
```

响应结构：

```json
{
  "revision": 0,
  "skills": [
    {
      "name": "canary-alpha",
      "description": "...",
      "enabled": true,
      "location": "/root/.agents/skills/canary-alpha"
    }
  ],
  "pids": {
    "opencode": 0,
    "hermes": 0,
    "codex": 0,
    "controller": 0
  }
}
```

上面的数值和列表仅说明 wire shape，不是实测输出。扫描要求 skill 名符合 `^[a-z0-9]+(?:-[a-z0-9]+)*$`，目录名必须与 `SKILL.md` frontmatter 的 `name` 一致；无效 skill 和目录符号链接会被忽略。如果安装器在某 skill 已禁用时又向 enabled 根目录安装同名副本，控制器采用 `disabled-wins`：保留 canonical disabled 副本，把新 enabled 副本原子隔离到 `.skillpanel-conflicts/`，并在响应的 `reconciliations` 中报告。

如果有人绕过 API 直接增删或移动目录，下一次 `GET /skills`、`GET /health` 或 `PUT` 会 reconcile 扫描结果，并可能推进 revision。因此客户端不能长期缓存 revision。

### `PUT /skills/{name}`

请求体必须同时带目标状态和刚从 `GET /skills` 取得的 `expected_revision`：

```bash
catalog="$(curl -fsS http://127.0.0.1:8787/skills)"
revision="$(printf '%s' "$catalog" | jq -r .revision)"

curl -fsS -X PUT \
  http://127.0.0.1:8787/skills/canary-alpha \
  -H 'Content-Type: application/json' \
  -d "{\"enabled\":false,\"expected_revision\":$revision}" | jq
```

成功响应包含：

- `changed`：目录是否真的发生移动。
- `revision`：成功提交后的 revision。
- `opencode_skills`：OpenCode dispose 后重新扫描到的名称。
- `pids`：三个当前进程 PID。
- `latency_ms`：控制器处理耗时。
- `hermes_refresh: "next-turn"`：仅状态实际变化时出现，表示 Hermes 会在下一轮检查 revision。

请求目标状态与当前状态相同时是幂等操作：控制器仍会刷新并校验 OpenCode，但 `changed` 为 `false`，revision 不增加。

错误状态：

| HTTP    | 含义                                                        |
| ------- | ----------------------------------------------------------- |
| `400` | skill 名格式非法或请求体无效                                |
| `404` | 未扫描到该 skill                                            |
| `409` | `expected_revision` 已过期；响应给出 `current_revision` |
| `503` | OpenCode dispose/校验失败；发生移动时控制器会尝试回滚       |
| `507` | revision 状态提交失败；控制器会尝试回滚目录和 OpenCode 视图 |

VS Code 客户端遇到 `409` 时应重新 `GET /skills`，向用户展示最新状态，再基于用户原始意图决定是否重试；不能用旧 revision 盲目循环。

### 场景 API

场景是一份「关名单」（`disabled` 为 skill 精确名字数组），把一组开关状态命名保存下来。任何时刻恰有一个激活场景；激活某个场景会把名单内的 skill 关掉、其余（包括名单未记录的新装 skill）一律打开，名单里已不存在的 skill 静默跳过。场景状态保存在 `/data/scenes.json`（`SKILLPANEL_SCENES_FILE`），带独立 revision 乐观锁，与 skill 操作共用同一把文件锁；文件缺失或损坏时惰性创建「默认」场景接管当前状态。

- `GET /scenes`：返回 `revision`、`active`、按名字排序的 `scenes`（每项含 `name`、`disabled`、`active`）和 `pids`。
- `POST /scenes`（`{"name": "编码"}`）：创建并切入新场景，全部 skill 打开起步。场景名 strip 后非空、不超过 64 字符，允许中文等任意 unicode；`400` 非法名，`409` 重名。
- `PUT /scenes/{name}/activate`（`{"expected_revision": n}`）：原子切换。任一目录搬动失败会把已搬的全部搬回，scenes.json 与 skill revision 均不变，返回 `503`；成功时 skill-state 与 scenes.json 各递增一次 revision。`404` 未知场景，`409` revision 冲突（响应带 `current_revision`）。
- `PUT /scenes/{name}`（`{"new_name": "绘图", "expected_revision": n}`）：重命名，激活场景同步更新 `active`，不做 apply。
- `DELETE /scenes/{name}`（`{"expected_revision": n}`）：删除激活场景会自动切到剩余场景中名字排序第一个并 apply；删除最后一个场景会重新生成全开的「默认」场景并 apply。

`PUT /skills/{name}` 开关成功后会把结果实时写回激活场景的关名单（关→加入、开→移出），与状态提交在同一事务内，写回失败则整体回滚；响应新增 `active_scene` 和 `scenes_revision` 两个字段。

容器首次启动时可用 seed 预置场景：entrypoint 在 scenes 文件不存在且 seed 文件（`SKILLPANEL_SCENES_SEED`，默认 `/opt/skillpanel/docker/scenes.seed.json`）可读时把 seed 复制过去；重启或升级绝不覆盖已有文件。

## 导入已有 skill

当前 Compose 使用 named volume，不会自动挂载宿主机的 `~/.config/opencode/skills`。维护窗口内可先把已有目录复制进容器：

```bash
docker cp "$HOME/.config/opencode/skills/." \
  skillpanel-poc:/root/.agents/skills/

# reconcile 目录、刷新 OpenCode 并提交新 revision，供 Hermes 下一轮识别
curl -fsS http://127.0.0.1:8787/skills | jq
```

导入必须在没有进行中对话轮次时完成。导入前应检查名称、frontmatter 和启用/禁用目录中是否有同名项。正常开关必须使用控制 API，不要让 VS Code 插件直接移动目录或修改 `skill-state.json`。

## 测试

完整测试矩阵见 `TEST_PLAN.md`，当前镜像和真实 UI 的验收结果见 `REPORT.md`。自动化入口会执行 Python unit、插件类型检查与 unit、VS Code Extension Host、镜像/code-server smoke、2-cycle、100-cycle，以及真实 PTY 中裸 `opencode` 的 model-free 三阶段切换：

```bash
./scripts/test-all.sh
```

统一入口还会真实 `SIGKILL` 一次 code-server，验证 Supervisor 用新 PID 自动拉起、旧进程后代退出、UID/VSIX/认证仍正确且三个后端 PID 不变；检查 Hermes Dashboard 未登录 `/` 只重定向到 `/login`，再用临时 cookie jar 完成密码登录并确认 dashboard 返回 200（密码不进入输出）；并检查 4096、8080、8787、9119 四个宿主端口都只绑定 loopback。顶层清理 trap 覆盖正常退出、HUP、INT 和 TERM，记录并恢复全部 `canary-*` 初始状态；每次 PUT 前重新读取最新 revision，清理请求也有连接和总超时。主测试成功但清理失败时，整套测试仍失败。

真实模型测试会产生 API 调用，显式开启：

```bash
RUN_LLM_TESTS=1 ./scripts/test-all.sh
```

只运行插件测试：

```bash
cd vscode-extension
npm ci
npm test
npm run package
```

### Unit

本机 Python 环境需要 FastAPI、Pydantic 和 PyYAML：

```bash
PYTHONPATH=src python3 -m unittest discover \
  -s tests -p 'test_*.py' -v
```

Unit 覆盖控制器的原子移动、revision 冲突、全部 OpenCode runtime 刷新失败回滚、`/skill` 响应与全量 managed skill/全局来源校验、TUI registry 安全过滤、启动器透传和登记清理、状态提交失败回滚、100 次临时目录切换、外部漂移、disabled-wins、无效/符号链接 skill，以及 Hermes hook 每个 revision 只重建一次和未配置时 no-op。它不等同于真实 OpenCode/Hermes/LLM 集成结果。

### Integration smoke

先等待 `/health` 返回 `ok: true` 再运行；脚本会在成功或失败时按最新 revision 恢复 canary 的运行前状态：

```bash
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/integration.py --cycles 2
```

这个黑盒脚本建立真实 Hermes dashboard WebSocket 和同一个 live/stored session，检查启用、禁用、再启用时 slash-command 映射、控制 API、OpenCode catalog、stale revision `409` 以及 Agent PID/session ID 是否保持一致。默认模式不调用模型。

终端直接运行 OpenCode 的产品路径默认也不调用模型：脚本在两个真实 PTY 中同时执行字面命令 `opencode`，等待两份原生 runtime 登记，然后在两个固定 TUI PID/start time 上完成 `enabled → disabled → enabled`，主 runtime 还保持同一个 session。每次 PUT 返回后立即读取一次两个 runtime 各自的 `/skill` 与 `/command`；这里刻意不轮询，避免把延迟收敛误判为同步生效。脚本结束前按最新 revision 恢复运行前状态，并确认两个 TUI 退出后两份 registry 都已删除：

```bash
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/opencode_tui_session.py

# 产品 UI 使用的 fixture 含 project/global 同名 skill，必须同样通过
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/opencode_tui_session.py \
  --directory /workspace/vscode-extension/test-fixtures/workspace
```

Hermes 真实 MiniMax 与同-session system-prompt revision 验证：

```bash
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/integration.py --cycles 0 --hermes-llm-canary
```

裸 OpenCode TUI 真实 MiniMax 同-session 三轮工具验证：

```bash
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/opencode_tui_session.py --llm
```

失败对照（脚本需要在容器内以 root 暂停控制器并确保 `finally` 恢复）：

```bash
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/comparisons.py --llm-native-reload
```

### Stress

```bash
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/integration.py --cycles 100
```

脚本输出 cycle 数、最终 revision、PID 检查结果和控制器延迟统计。运行前后都应保存 `/skills`、容器日志和版本信息到报告：

```bash
docker exec skillpanel-poc opencode --version
docker exec skillpanel-poc hermes --version
docker compose logs --no-color skillpanel
```

## OpenCode 1.17.12 / 1.17.18 矩阵

Compose 通过环境变量把版本传入 Docker build args，并把 OpenCode 版本写入 image tag。Hermes 补丁目前固定针对 0.18.2。

构建并启动 1.17.12：

```bash
OPENCODE_VERSION=1.17.12 HERMES_VERSION=0.18.2 \
  docker compose build --no-cache skillpanel
OPENCODE_VERSION=1.17.12 HERMES_VERSION=0.18.2 \
  docker compose up -d --force-recreate skillpanel
```

顺序停止/替换同一个容器后，再构建并启动 1.17.18：

```bash
OPENCODE_VERSION=1.17.18 HERMES_VERSION=0.18.2 \
  docker compose build --no-cache skillpanel
OPENCODE_VERSION=1.17.18 HERMES_VERSION=0.18.2 \
  docker compose up -d --force-recreate skillpanel
```

两个版本都应分别执行 health、integration、stress 和同 session 下一轮对话验证，并把命令、日志、PID、session ID、tool call 和延迟写入 `REPORT.md`，而不是根据构建成功推断热更新成功。

Dockerfile 根据 BuildKit 的 `TARGETARCH` 选择：

- `linux/arm64` → `opencode-linux-arm64`
- `linux/amd64` → `opencode-linux-x64-baseline`

Colima 默认 profile 当前可通过 builder `colima` 构建本机架构；如需交叉检查，可显式使用 `docker buildx build --builder colima --platform linux/amd64` 或 `linux/arm64`，并保持相同 build args。

## 回滚

单次 skill 切换的自动回滚边界：

- OpenCode dispose 或 catalog 校验失败：把目录移回原位置，尝试恢复 OpenCode 视图，返回 `503`。
- revision 文件提交失败：把目录移回原位置，尝试恢复 OpenCode 视图，返回 `507`。
- stale revision：不移动任何目录，返回 `409`。

发生错误后先重新读取：

```bash
curl -fsS http://127.0.0.1:8787/skills | jq
curl -fsS http://127.0.0.1:4096/skill?directory=%2Fworkspace | jq
docker compose logs --tail=200 skillpanel
```

从 OpenCode 1.17.18 回退到已构建的 1.17.12 镜像，同时保留 named volumes：

```bash
OPENCODE_VERSION=1.17.12 HERMES_VERSION=0.18.2 \
  docker compose up -d --force-recreate --no-build skillpanel
```

如果本地没有 `skillpanel-poc:1.17.12`，先用上一节命令重新构建。不要通过还原旧 `skill-state.json` 来降低 revision；需要恢复某个 skill 时，用最新 revision 调用 `PUT`。

## 安全边界与限制

- OpenCode Supervisor server 和控制器当前没有 HTTP 鉴权。虽然宿主端口只绑定 `127.0.0.1`，同一 Docker network 内的容器仍能访问它们；不要把端口改为公网绑定。直接 TUI runtime 只监听容器内部随机 loopback 端口，也不得改为外部地址。
- Hermes 有 Basic Auth，但 Compose 默认密码只适合本机 PoC，必须在共享环境覆盖。
- auth 文件是只读 bind mount，也不会进入镜像；但 Hermes key 存在于 Hermes 进程环境中，容器 root、Docker daemon 管理者以及有 `docker exec` 权限的人仍可读取。只读挂载不等于对容器管理员保密。
- 控制器进程以 `skillpanel` 用户运行；OpenCode 和 Hermes 当前以 root 运行。该隔离只限制普通控制器文件权限，不是容器内强安全边界。
- 这是全局共享开关，不支持按用户、workspace、session 或 Agent 独立启用。
- 开关语义以“下一轮”为边界。已经开始的 LLM/tool 调用不会撤销，也没有为目录移动和 Hermes 新一轮之间实现全局 quiescence 锁；必须在两轮之间切换。
- OpenCode 使用 `/global/dispose`，会分别释放 Supervisor server 和每个已登记直接 TUI runtime 内的所有 workspace instance，而不仅是当前 `/workspace`。它适合单容器 PoC，但高并发产品应评估更细粒度且有完成屏障的 reload 协议。
- `GET /skills` 是控制器的物理启用/禁用视图；OpenCode 的 permission 配置是另一层过滤。不要同时把 permission deny 当作同一开关协议的一部分。
- Hermes 补丁依赖 0.18.2 的内部模块和私有属性。升级 Hermes 时，build-time `patch` 失败应视为兼容性阻断，不能跳过补丁继续发布。
- 自动化脚本可覆盖真实 MiniMax canary，但会产生实际 API 调用；默认 integration 模式保持 model-free。
- 禁用不会删除 conversation history 中已经加载的 skill 内容。本方案保证下一轮的新发现/加载被阻止并刷新当前 prompt，不保证模型真正 unlearn 旧上下文。
- 直接向 named volume 安装或删除 skill 会在下一次 reconcile 时推进 revision，但绕过了正常事务边界。产品集成只应调用控制 API。
