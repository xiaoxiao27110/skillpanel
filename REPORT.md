# SkillPanel 裸 OpenCode / Hermes 热切换验收报告

> 验收日期：2026-07-12  
> 最终产品路径：桌面 VS Code Dev Containers + SkillPanel + 集成终端直接运行 `opencode` + Hermes Dashboard  
> 判定原则：Computer Use 驱动真实 UI，API、runtime HTTP、进程表和日志只作为只读 oracle。

## 结论

本轮计划已完成，最初报告的现象确实是 bug，而不是用户对生效时机的误解。

旧实现只刷新了后台 `opencode serve`。用户在终端直接运行 `opencode` 时，TUI 会拥有另一份独立 runtime 和 skill cache；插件虽然已经把 `canary-beta` 移到禁用目录，旧 TUI 仍可在 `/skills` 中看到并调用它。修复后，直接 TUI 会通过透明启动器登记自己的 loopback runtime；控制器把后台 server 和全部仍存活的直接 TUI 纳入同一次刷新事务。

最终在同一条 OpenCode TUI session 和同一条 Hermes Dashboard session 内完成了：

1. 启用：OpenCode 立即刷新；Hermes 在切换后的首个新对话轮次刷新 revision，随后显式调用返回 `SKILLPANEL_BETA_91C4`。
2. 禁用：下一轮两边 `/skills` 都不再显示它；OpenCode skill tool 确定性返回 not found，Hermes 显示 `Failed to load skill for /canary-beta`。
3. 再启用：OpenCode catalog 立即刷新，Hermes 在下一轮刷新；随后两边 `/skills` 和显式调用都恢复。

整个切换过程中，受管 OpenCode、Hermes、controller、直接 OpenCode TUI、Hermes bundled TUI 的 PID/start-time 以及两边 session ID 均未变化。测试结束后已按最新 revision 恢复运行前状态：`canary-alpha=true`、`canary-beta=false`。

## 最初失败与根因

### 直接 OpenCode TUI

- 失败时 revision：`861`
- controller 状态：`canary-beta=false`
- 旧直接 TUI session：`ses_0aa51018affepmeQilppSZdHF5`
- 现象：插件已显示 beta 禁用，但同一个裸 `opencode` 的 `/skills` 仍显示 beta。
- 证据：[插件禁用、裸 TUI 仍有 beta](evidence/initial-failure-opencode-stale-skill.jpeg)

根因是 OpenCode 1.17.12 的每个外部 TUI runtime 都有自己的进程内 catalog。控制器只对固定后台 server 调用 `/global/dispose`，并不知道终端里新启动的 TUI，因此 PUT 成功不能保证该 TUI 已收敛。

### Hermes Dashboard

真实产品测试还复现了 Dashboard 登录 500：[登录失败证据](evidence/initial-failure-hermes-login-500.jpeg)。Hermes 的自动 SSO 中间件把唯一的 password provider 误送到仅适用于 OAuth 的 `/auth/login`；该 provider 不实现 OAuth `start_login()`，最终返回 500。

## 实现

### 裸 OpenCode 透明启动器

[`docker/opencode_launcher.py`](docker/opencode_launcher.py) 保留原生二进制为 `/usr/local/libexec/opencode`：

- 无透传子命令的直接 TUI 使用随机 loopback 端口启动 OpenCode 外部 runtime。
- 在共用 state lock 内完成端口选择、native 启动、首次 dispose 和登记发布，避免 PUT 与启动登记之间的竞态。
- 登记文件原子写入 `/run/skillpanel-opencode-tuis/<pid>.json`，包含 PID、`/proc` start-time、工作目录和 loopback URL。
- 退出时清理登记；信号转发给原生进程。
- `serve`、`run`、`attach` 等子命令原样透传，不改变原有 CLI 语义。

### 控制器事务

[`src/controller.py`](src/controller.py) 现在把后台 server 与所有已登记、仍存活的直接 TUI 当成一次事务：

1. 原子移动完整 skill 目录。
2. 对后台 server 和每个直接 TUI 执行 `/global/dispose`。
3. 读取并校验 `/skill`；直接 TUI 还校验 `/command`。
4. 按 catalog `location/source` 判定全局 skill，避免项目级同名 skill 造成假阳性。
5. 全部 runtime 收敛后才提交 state 和新 revision。

任何仍存活但不可达、刷新失败或 catalog 不一致的 TUI 都会使 PUT 失败，并回滚目录、catalog 和 revision。已退出或 PID/start-time 不匹配的登记会被忽略。登记读取还限制为 root 创建的普通文件、合法 PID/start-time 与 loopback URL。

外部接口未变化：插件仍只使用 `GET /skills` 和 `PUT /skills/{name}`。

### Hermes 产品路径

- [`patches/hermes-turn-revision.patch`](patches/hermes-turn-revision.patch)：每轮恢复 cached prompt 后读取 revision；新 revision 时重建 slash map 和该轮 system prompt。
- [`patches/hermes-basic-auth-login.patch`](patches/hermes-basic-auth-login.patch)：password provider 保持在 server-rendered `/login` 流程，不再进入 OAuth-only 路径。
- [`patches/hermes-bundled-tui.patch`](patches/hermes-bundled-tui.patch)：优先运行 wheel 内置 `hermes_cli/tui_dist/entry.js`，不要求镜像中存在源码 `ui-tui` workspace。

镜像固定使用 Node `v24.18.0`、OpenCode `1.17.12`、Hermes `0.18.2`。Dashboard 以 `hermes dashboard --host 0.0.0.0 --port 9119 --skip-build --no-open` 启动。

### 插件提示文案

顶部已精简为唯一一句：`开关成功后，从下一轮对话开始生效`。不再展示 revision、进程健康或切换实现细节；这些信息只保留在测试 oracle 和报告中。

## 自动化结果

最终执行：

```bash
RUN_LLM_TESTS=1 ./scripts/test-all.sh
```

结果为 `All automated gates passed.`。关键结果如下：

| 项目 | 结果 |
|---|---|
| 宿主 Python discovery | 48 项：46 通过，2 项 Hermes 容器专用测试跳过 |
| 容器 Python | 48/48 通过 |
| 插件 TypeScript | type check 通过 |
| 插件 unit | 25/25 通过 |
| Extension Host | 9/9 通过 |
| `npm audit` | 0 vulnerabilities |
| 2-cycle | 通过，状态与 PID 恢复 |
| 100-cycle | 通过，最终测试 revision `1107` |
| 镜像/Compose | 完整重建、health、loopback、认证、Supervisor、重启检查全部通过 |
| 宿主最终 VSIX | 7 files；321,696 bytes uncompressed；96,456 bytes archive |

### 启动器与直接 TUI

- 参数透传、登记原子性、登记清理、初始化失败清理均有单元测试。
- 两个并发直接 TUI、失效/恶意登记、活 runtime 不可达、刷新失败回滚、项目同名来源均有覆盖。
- 两套 model-free 产品协议测试都在两个真实 PTY 中输入字面命令 `opencode`，每次 PUT 返回后立即检查两个 runtime 的 `/skill` 和 `/command`，未用轮询掩盖收敛延迟。

| 场景 | session | revision | 结果 |
|---|---|---|---|
| 默认工作目录 | `ses_0a9fda35bffeHYA3WlZi7NYVa4` | `998 → 999 → 1000`，恢复 `1001` | 两个 TUI PID/start-time 不变，登记清理 |
| 产品 fixture | `ses_0a9fd83b5ffeG2St3h1Hukid8a` | `1002 → 1003 → 1004`，恢复 `1005` | 两个 TUI PID/start-time 不变，登记清理 |

### 真实模型

| 产品路径 | session | revision | 结果 |
|---|---|---|---|
| Hermes MiniMax | live `3d978a35`；stored `20260712_110718_9c0883` | `1107 → 1108 → 1109` | 同 session 的 slash map、prompt marker、显式调用按新状态变化；PID 不变 |
| 裸 OpenCode MiniMax | `ses_0a9fd2de2ffetc9UU22MtgqJAe` | `1110 → 1111 → 1112`，恢复 `1113` | skill tool `completed → error/not found → completed`；两个直接 TUI PID 不变，登记清理 |

失败对照也通过：

- 只移动目录时，OpenCode 旧 runtime catalog 仍保留被移动的 canary，证明 dispose 屏障必需。
- Hermes 原生 reload 可移除 slash map，但同 session cached prompt 仍保留旧正文，证明 revision hook 必需。

### Hermes Dashboard `/api/pty`

真实 legacy 1:1 WebSocket smoke 完成 Basic Auth、一次性 ws-ticket 和 `/api/pty` 连接，收到非空二进制帧。启动的 argv 精确为：

```text
/usr/local/bin/node --expose-gc /opt/hermes/lib/python3.12/site-packages/hermes_cli/tui_dist/entry.js
```

捕获的 child PID/PGID 为 `1156`，start-time `575007`；关闭 WebSocket 后整个进程组自然回收，`graceful_reap=true`，未使用 fallback。

## 最终 Computer Use 联合验收

### 身份基线

| 对象 | 验收身份 |
|---|---|
| 受管 OpenCode | PID `33` |
| Hermes Dashboard server | PID `34` |
| controller | PID `35` |
| 裸 OpenCode launcher/native | PID `4248` / `4249`；native start-time `595488` |
| 裸 OpenCode runtime | `http://127.0.0.1:49543`；工作目录 `/workspace/vscode-extension/test-fixtures/workspace` |
| 裸 OpenCode session | `ses_0a9f43282ffee1HmKvw5FqZL89` |
| Hermes UI session | `cb3d2473` |
| Hermes bundled TUI | Node PID `5086`，启动时间 `2026-07-12 11:12:46` |
| Hermes workers | PID `5094` / `5123`；session key `20260712_111246_0a091f` / `20260712_111247_b8973a` |

### 三阶段结果

| 阶段 | revision | SkillPanel | 同一 OpenCode session | 同一 Hermes session |
|---|---:|---|---|---|
| 运行前 | `1113` | beta 禁用 | 启动并登记裸 TUI | Dashboard 登录成功，bundled TUI 已启动 |
| 启用 | `1114` | 已启用 2 | `/skills` 有 beta；tool completed；返回 beta token | 切换后、首轮前 `/skills` 仍是旧缓存 1；`11:20:03` 首个新轮次刷新 revision `1114`，随后 `/canary-beta` 加载正文并返回 beta token |
| 禁用 | `1115` | 已启用 1、已禁用 1 | `/skills` 无 beta；tool error 为 `Skill "canary-beta" not found`；回答 `SKILL_NOT_AVAILABLE` | `/skills` 只剩 alpha；显式命令显示 `Failed to load skill for /canary-beta` |
| 再启用 | `1116` | 已启用 2 | `/skills` 恢复 beta；tool completed；再次返回 beta token | `/skills` 恢复为 2；显式加载并再次返回 beta token |
| 恢复初始值 | `1117` | beta 禁用 | 正常退出并清除登记 | 关闭 Dashboard 标签页并清理本次测试 child |

禁用阶段的 OpenCode runtime JSON 明确记录了新的 tool call：

```text
status=error
Skill "canary-beta" not found. Available skills: canary-alpha, claude-only, customize-opencode, project-only
```

因此该结论不是只看 UI 列表；显式 tool 路径也确实失效。Hermes 同样产生了新的 failed-load 结果。旧轮次已经写入 conversation history 的 beta 正文仍可在回滚屏中看到，但禁用后没有重新注入；这符合“下一轮 catalog 生效、不清除历史记忆”的设计边界。

### 截图证据

| 阶段 | SkillPanel | OpenCode | Hermes |
|---|---|---|---|
| 启用 | [开关状态](evidence/final-skillpanel-enabled.jpeg) | [列表](evidence/final-opencode-enabled.jpeg) / [调用结果](evidence/final-opencode-enabled-result.jpeg) | [首轮前旧缓存](evidence/final-hermes-enabled.jpeg) / [新轮次刷新后的调用结果](evidence/final-hermes-enabled-result.jpeg) |
| 禁用 | [开关状态](evidence/final-skillpanel-disabled.jpeg) | [列表](evidence/final-opencode-disabled.jpeg) / [拒绝结果](evidence/final-opencode-disabled-result.jpeg) | [列表](evidence/final-hermes-disabled.jpeg) / [拒绝结果](evidence/final-hermes-disabled-result.jpeg) |
| 再启用 | [开关状态](evidence/final-skillpanel-reenabled.jpeg) | [列表](evidence/final-opencode-reenabled.jpeg) / [调用结果](evidence/final-opencode-reenabled-result.jpeg) | [列表](evidence/final-hermes-reenabled.jpeg) / [调用结果](evidence/final-hermes-reenabled-result.jpeg) |
| 恢复 | [最终 beta 禁用](evidence/final-restored.jpeg) | — | — |

`evidence/` 共保存 22 张 Computer Use 截图，包括最初失败、第一轮修复复测和最终联合验收。

## 最终制品与运行状态

| 项目 | 最终值 |
|---|---|
| 镜像 | `skillpanel-poc:1.17.12`，Linux arm64 |
| image ID / repo digest | `sha256:15a6532a5ea11b0f285ee821c5820269f695858945edf1058b849ee857ee4c3a` |
| image created | `2026-07-12T19:05:32.70979154+08:00` |
| image size | `547,421,138` bytes |
| Colima | macOS Virtualization.Framework；aarch64；Docker；virtiofs |
| OpenCode | `1.17.12` |
| Hermes | `0.18.2` |
| Node | `v24.18.0` |
| Python | `3.12.13` |
| code-server | `4.121.0` / Code `1.121.0` |
| 插件 | `skillpanel.skill-panel@0.1.0` |
| 宿主最终 VSIX | `96,456` bytes；SHA-256 `d1de3d9b2eabe20b87efb3dbcf95297281f4ae259442e1b8f32ad2d26456d792` |
| 镜像内 VSIX | `95,846` bytes；SHA-256 `2acedb7400e1833235e515b8cf6d99c67d4b318b6be70d4ece0ddd3b2e57c497` |
| 两份 VSIX 的运行 bundle | `dist/extension.js` 共同 SHA-256 `44bfe8d9a43b454afcd02716771b655fb9d00395ee90f0b17b9e48255afa843d` |

最终 `GET /skills`：

| 字段 | 值 |
|---|---|
| revision | `1117` |
| `canary-alpha` | `enabled=true`；`/root/.config/opencode/skills/canary-alpha` |
| `canary-beta` | `enabled=false`；`/root/.config/opencode/skills-disabled/canary-beta` |
| reconciliations | `[]` |
| managed PIDs | OpenCode `33`；Hermes `34`；controller `35` |
| code-server PID | `444` |
| container health | `healthy`，failing streak `0` |
| TUI registry | 空 |
| Hermes 测试 active-session marker | 已清理 |
| 本次测试临时进程 | PID `4248/4249/5086/5094/5123` 均已回收 |

产品 Dashboard 使用 `attach` token，正常断开可按 Hermes 设计保留到 30 分钟 reaper TTL；这与自动化 legacy 1:1 smoke 的“关闭 WebSocket 后立即回收”不是同一语义。最终收尾只终止了本次记录到的测试 PGID，后台 reaper 随后完成回收，未重启或替换受管 Hermes PID `34`。

## 已知边界

- 开关成功只保证同一 session 的下一轮使用新 catalog，不取消已经开始的模型/tool 调用。
- 禁用不会删除 conversation history 中旧轮次的 skill 正文，也不承诺模型“遗忘”；强隔离场景应新建 session。
- 当前是容器内全局共享开关，不按用户、workspace、session 或 Agent 独立配置。
- 产品验收路径是直接执行 `opencode`；`opencode attach` 按计划不在本轮产品验收范围。
- 控制器与 OpenCode 内部 HTTP 没有独立公网鉴权，安全边界依赖 loopback、容器网络和宿主权限，不能直接暴露到公网。
- Hermes 补丁固定到 0.18.2 的内部接口；升级时必须重新应用并执行完整测试。

## 最终判定

用户要求的直接 OpenCode TUI、Hermes Dashboard、事务回滚、竞态/来源校验、自动化覆盖、真实模型、镜像健康、VSIX 安装和 Computer Use 同会话联合验收均已通过。最终状态已恢复，无 runtime 登记、临时进程或 reconciliation 残留。
