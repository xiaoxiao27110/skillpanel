# SkillPanel VS Code 插件测试计划

本文是插件开发与验收的测试基线。插件只有在自动化测试、容器测试和真实 VS Code UI 验收全部通过后，才算完成。

## 1. 测试目标与边界

测试对象包含：

- 已验证的 SkillPanel 控制器协议：`GET /skills`、`PUT /skills/{name}`。
- 运行在 workspace extension host 中的 VS Code 插件。
- 容器内由 Supervisor 管理的 code-server。
- 终端直接执行 `opencode` 启动的 TUI、其私有 loopback runtime，以及 Hermes 0.18.2 的同 session 下一轮刷新行为。

插件不得直接移动 skill 目录、修改 OpenCode permission、修改 Hermes 配置或调用两套 Agent 的内部刷新接口。全局 skill 状态只能来自控制器 API。

本计划验证新一轮的发现、command 映射、显式加载和 prompt 重建，不把清除 conversation history 中模型已经见过的文字纳入范围。

每次会改变 canary 状态的测试都必须：

1. 先保存 `GET /skills` 的 revision、状态和 PID。
2. 只使用最新 revision 提交切换。
3. 在 `finally`/清理阶段恢复测试前的 enabled 状态。
4. 恢复后重新读取 catalog，并验证 OpenCode、Hermes PID 没有变化。
5. 不在测试输出、截图、镜像层或日志中记录 API key。

## 2. 需求—证据矩阵

| ID | 需求 | 主要证据 |
|---|---|---|
| R01 | 插件在 code-server 和桌面 Dev Containers 中都作为 workspace extension 运行 | manifest 静态测试、镜像扩展列表、桌面真实 UI smoke |
| R02 | 独立 Activity Bar 图标和原生 Tree View | Extension Host 测试、真实 UI |
| R03 | 启用、禁用、项目级、冲突分组 | view-model 单测、TreeItem 集成测试、真实 UI |
| R04 | 使用主题色圆点；pending 使用旋转图标 | TreeItem 集成测试、明暗主题 UI 截图 |
| R05 | 点击全局 skill 整行切换；项目级节点不可切换 | command 测试、真实 UI、API 状态核对 |
| R06 | 悬浮显示名称、描述、状态和来源 | Extension Host TreeItem 内容测试、真实 UI 键盘 hover |
| R07 | 只允许逐项切换；不提供搜索、批量、编辑、删除或详情页 | manifest 静态测试、命令清单检查 |
| R08 | 外部变化只在手动刷新后同步 | provider 单测、真实 UI 外部切换场景 |
| R09 | `expected_revision` 乐观并发；插件自身串行写入 | client/orchestrator 单测、stale revision UI 场景 |
| R10 | pending 期间不乐观改变 enabled 状态 | 延迟 fake server 单测、TreeItem 集成测试 |
| R11 | `409` 重新拉取并提示，不自动覆盖或循环重试 | fake server 契约测试、真实 UI stale 场景 |
| R12 | `503/507` 展示回滚详情并重新获取真实状态 | fake server 契约测试 |
| R13 | 成功或幂等 PUT 后重新 GET，以控制器为事实源 | HTTP 调用序列单测 |
| R14 | 顶部只显示“开关成功后，从下一轮对话开始生效”；reconciliation 和冲突仍在对应分组展示 | view-model/TreeItem 测试、真实 UI |
| R15 | 全局与项目同名时显示冲突，不改文件 | view-model/项目扫描单测、真实 UI fixture |
| R16 | 项目扫描 `.opencode/skills`、`.agents/skills`、`.claude/skills` | 项目扫描集成测试 |
| R17 | 项目 skill 向上扫描到 Git 根；多 workspace 按 name 合并来源 | 项目扫描集成测试 |
| R18 | 无效 SKILL.md 和 skill 目录符号链接完全忽略 | 项目扫描单测与临时文件测试 |
| R19 | 空状态展示全局路径和刷新动作 | view-model/manifest 测试、空 catalog UI 测试 |
| R20 | 从插件切换成功返回开始，终端裸 `opencode` TUI 与 Hermes Dashboard 的同一 session 下一条消息都使用新状态；TUI、Agent PID 和 session ID 不变 | direct-TUI PTY/API 测试、Hermes Dashboard `/api/pty` bundled-TUI smoke、Hermes/OpenCode LLM 黑盒测试、Computer Use 联合验收 |
| R21 | code-server 安装在镜像、只绑定宿主 loopback、自动加载插件 | 镜像检查、Compose 检查、HTTP/扩展列表 smoke |
| R22 | backend 的 rename、revision、完整 managed catalog 收敛、回滚、disabled-wins 行为不回归 | Python unit/integration/stress/comparison |

## 3. 测试层次

### L0：静态检查与构建

目标：尽早发现 manifest、类型和打包错误。

```bash
cd vscode-extension
npm ci
npm run check
npm run package

cd ..
docker compose config
```

检查项：

- `extensionKind` 必须包含 `workspace`。
- 只贡献一个 Activity Bar container、一个 Tree View 和刷新命令。
- 不贡献搜索、批量、编辑、删除、打开详情相关命令。
- VSIX 中不得包含测试缓存、宿主凭据、Docker volume 或运行日志。
- Compose 的 8080、4096、8787、9119 全部只映射到 `127.0.0.1`。

### L1：纯逻辑单元测试

不启动 VS Code，使用临时目录和本地 fake HTTP server。

覆盖：

- `SKILL.md` front matter 解析和名称校验。
- enabled/disabled/project/conflict/reconciliation view model。
- GET/PUT JSON 编解码、URL 编码和超时。
- 400/404/409/503/507 错误结构。
- 单写队列、无乐观更新、成功后 GET、409/503/507 后 GET。
- manifest 只贡献 Activity Bar、Tree View 和刷新命令。
- 源码不注册文件系统 watcher。

```bash
cd vscode-extension
npm run test:unit
```

### L2：VS Code Extension Host 集成测试

通过官方 Extension Development Host 启动隔离的 VS Code 实例。

覆盖：

- 扩展激活并注册 View、Provider 和命令。
- TreeItem 的 command、icon、ThemeColor、tooltip 完整内容和 collapsibleState。
- 项目级节点没有 toggle command。
- refresh 命令会重新请求 catalog 并重扫项目目录。
- 三种项目目录、Git 根边界、多 workspace fixture 去重与来源合并。
- 无效文件、隐藏目录和符号链接忽略。
- pending、empty catalog 和控制器不可达状态。

```bash
cd vscode-extension
npm run test:extension
```

### L3：后端协议与 Agent 回归测试

先运行不调用模型的测试，再运行真实模型测试。

```bash
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

docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/integration.py --cycles 0 --hermes-llm-canary

docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/opencode_tui_session.py --llm

docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/comparisons.py --llm-native-reload
```

验收：

- OpenCode、Hermes PID 在切换前后不变。
- OpenCode `/skill` 必须是合法数组，且全部 managed skill 的启用/禁用 presence 与目录状态一致；结构错误或部分 catalog 必须回滚且不提交 revision。
- 默认 direct-TUI 测试必须在两个真实 PTY 中同时执行字面命令 `opencode`，等待 `/run/skillpanel-opencode-tuis/*.json` 的两份登记；每次 PUT 返回后只读取一次两个 runtime 各自的 `/skill` 和 `/command`，不得用轮询掩盖延迟收敛。
- 两个 direct-TUI 的原生 PID、`/proc` start time 和 loopback URL 在 `enabled → disabled → enabled` 全程不变；主 runtime 的同一个 session 承担 LLM 三轮，peer 保持并发存活；退出后两份登记都必须清理。
- 启动器与 controller 必须共用 state lock；端口选择、native 启动、首次 dispose 和登记发布都在锁内，不能留下“PUT 已成功但 TUI 尚未登记”的窗口。
- `serve`、`run`、`attach`、`generate`、`console` 等根子命令必须原样透传；OpenCode 1.17.12 的 `--mini` 因与外部 runtime 参数不兼容而明确返回 2，不得静默降级为无法热刷新的会话。
- stale revision 返回 409 且不移动目录。
- 100-cycle 后 canary 回到初始状态。
- Hermes 与 OpenCode 在同一 session 的下一轮使用新 revision。
- comparison 继续证明“只移动目录”和“Hermes 原生 reload”不足以满足目标。

### L4：镜像与 code-server 冒烟测试

```bash
docker compose build skillpanel
docker compose up -d --force-recreate skillpanel

curl -fsS http://127.0.0.1:8787/health | jq
curl -fsS http://127.0.0.1:8080/healthz \
  | jq -e '.status == "alive" or .status == "expired"'
curl -fsS -L -o /dev/null -w '%{url_effective}\n' http://127.0.0.1:9119/

docker exec --user coder -e HOME=/home/coder skillpanel-poc code-server --version
docker exec --user coder -e HOME=/home/coder skillpanel-poc code-server \
  --extensions-dir /home/coder/.local/share/code-server/extensions \
  --list-extensions --show-versions \
  | grep '^skillpanel.skill-panel@'
docker exec skillpanel-poc supervisorctl status
docker exec skillpanel-poc /opt/hermes/bin/python \
  /opt/skillpanel/tests/hermes_dashboard_pty_smoke.py
```

额外检查：

- `http://127.0.0.1:8080/?folder=/workspace` 可加载。
- Hermes Dashboard 未登录访问 `/` 必须 3xx 到 `/login`（不得进入旧的 `/auth/login`）；用临时 cookie jar 完成密码登录后，已认证 `/` 必须返回 200。测试不得打印密码。
- Hermes Dashboard smoke 必须完成 Basic Auth、领取一次性 ws-ticket，并在不传 `attach` 的情况下连接真实 `/api/pty` legacy 1:1 路径；必须收到非空 PTY bytes，确认新 child argv 是 `/usr/local/bin/node --expose-gc .../hermes_cli/tui_dist/entry.js` 且源码 `ui-tui` 不存在。关闭 WebSocket 后该 child 的整个 PGID 必须自然退出；fallback 信号只能清理本 smoke 捕获的进程组，且发生 fallback 时测试仍失败。
- 尚无浏览器心跳时 `/healthz` 可返回 `expired`；HTTP 200 即证明服务进程可用，连接 UI 后再验证 `alive`。
- code-server 由 Supervisor 管理，异常退出后可恢复。
- code-server 无公网端口，并使用密码认证；密码只从进程环境读取，不进入命令行、镜像或日志。
- VSIX 由镜像构建阶段生成并安装，运行时不从网络下载插件。
- `/workspace` 指向当前宿主项目，UI 测试 fixture 可见。

### L5：真实 UI 验收（Computer Use）

最终产品路径固定为：桌面 VS Code Dev Containers 中的 SkillPanel、其集成终端里直接执行的裸 `opencode`，以及 Hermes Dashboard。code-server 浏览器版仍做 L4 服务 smoke，但不能替代这条产品级验收。全程用 Computer Use 点击和输入；每个动作后重新读取 UI，不复用过期元素。API、runtime HTTP 与日志只作为只读判定 oracle，不能代替用户操作。

按以下顺序执行：

1. 保存 canary 初始状态、revision、三个受管 Agent PID，并确认 TUI 登记目录初始状态；打开桌面 VS Code Attach 窗口，确认安装的是本次 VSIX。
2. 打开 SkillPanel，确认 Activity Bar 图标、四个分组、计数和 tooltip；顶部只能看到“开关成功后，从下一轮对话开始生效”，不得出现 revision 或进程健康长文案。
3. 在 VS Code 集成终端输入字面命令 `opencode`，创建/选中一个会话；记录原生 TUI PID、start time、runtime URL 和 session ID，后续不得重启或换 session。
4. 打开 `http://127.0.0.1:9119/`，确认未登录访问进入 `/login` 而不是 500；登录 Hermes Dashboard 后创建一个会话并记录 session ID、bundled TUI child PID 和 start time，后续不得换 session。
5. 从 SkillPanel 启用 `canary-beta`；在同一 OpenCode TUI 和同一 Hermes session 的下一轮分别打开 `/skills` 并调用 canary，确认可见且返回 `SKILLPANEL_BETA_91C4`。
6. 从 SkillPanel 点击一次禁用 `canary-beta`；PUT 成功后，两个 session 的紧接下一轮 `/skills` 都不得再显示它，显式加载必须得到 not-found/不可用结果，新 tool 输出和新构造的 prompt 不得重新注入 beta skill 正文。
7. 从 SkillPanel 再次启用；两个 session 的下一轮 `/skills` 与显式调用都恢复。用只读 oracle 核对 `/skill`、`/command`、Hermes slash-command 映射、revision 和目录状态。
8. 核对步骤 3、4 记录的 PID/start time/session ID 全程不变；保存启用、禁用、再启用三阶段截图和 oracle 摘要。
9. 补做插件交互 smoke：项目级节点不可切换、手动刷新同步外部变化、stale revision 显示 409 且不自动重试、明暗主题图标可辨识。
10. 无论成功或失败，都先在仍存活的同一 OpenCode TUI 与 Hermes session 中用最新 revision 恢复全部 canary 初始状态，并重新读取两个 runtime catalog。保存最终 PID/session 证据后，再通过 UI 正常退出裸 TUI 并确认登记目录无条目或临时文件。关闭 Hermes Dashboard 连接后，带 `attach` 的产品 PTY 可按设计保留到 reaper TTL；记录该 child 身份并确认没有额外未跟踪 child，不把它与 L4 legacy 1:1 smoke 的立即回收语义混为一谈。最终还须确认状态无漂移且 `reconciliations` 为空。

## 4. 统一测试入口

实现完成后提供：

```bash
./scripts/test-all.sh
```

默认执行 L0、L1、L2、L3 的 unit/smoke、真实裸 TUI model-free 三阶段测试和 L4。产生真实模型费用的 LLM、comparison 与 L5 Computer Use 测试保持显式阶段，但最终验收必须执行并记录结果。

脚本必须在首个失败处退出；顶层 `EXIT` trap 保存并按最新 revision 恢复所有 `canary-*` 初始状态，保留原始失败码，并在主流程成功但清理失败时使整套测试失败。独立运行的每个状态切换脚本也必须在 `finally` 中恢复自己的原始状态。不得用“后续测试通过”掩盖较早的失败。

## 5. 完成判定

完成必须同时满足：

- R01–R22 均有对应测试通过的当前证据。
- `npm run check`、unit、Extension Host、Python unit、2-cycle、100-cycle 全部为绿色。
- Hermes LLM、OpenCode LLM 和 comparison 在当前镜像通过。
- code-server 健康，镜像内安装的是本次构建的 VSIX；Hermes Dashboard `/api/pty` legacy smoke 已真实拉起并自然回收 bundled TUI。
- Computer Use 已通过桌面 SkillPanel、集成终端裸 `opencode` 和 Hermes Dashboard 完成同 session 联合切换，并覆盖 Dashboard `/ → /login`、插件刷新和 stale revision smoke。
- 测试结束时 canary 状态、PID 和 controller catalog 一致，无待恢复的测试数据。
