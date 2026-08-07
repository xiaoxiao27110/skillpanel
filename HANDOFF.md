# SkillPanel AI 交接稿

## 任务定位

这个项目是 OpenCode 与 Hermes 共享 skill 的热切换 PoC，目录为：

```text
/Users/xiaoxiao/MobileDisk/skillpanel
```

目标不是开发完整 VS Code 插件，而是先提供一个可靠的后端协议，使未来插件可以查看、启用和禁用 skill。

必须满足的语义：

- OpenCode 与 Hermes 使用同一个 skill 根目录。
- 用户切换 skill 后，不重启 OpenCode 或 Hermes。
- 已经开始的一轮允许继续使用旧状态。
- 切换确认后，同一 session 的下一条消息必须使用新状态。
- 开关操作需要支持并发控制、幂等、失败回滚和状态恢复。

## 当前状态

实现和验证已经完成，当前常驻环境为：

```text
Colima profile: default
Container:       skillpanel-poc
Image:           skillpanel-poc:1.17.12
OpenCode:        1.17.12
Hermes:          0.18.2
Model:           MiniMax-M2.7
```

容器当前健康运行。OpenCode 1.17.18 和 amd64 镜像也已构建验证，但常驻版本应保持 1.17.12，除非用户明确要求切换。

详细测试数据在 `REPORT.md`，复现命令在 `README.md`。不要把本交接稿扩写成第二份测试报告。

## 已确定的方案

### 共享目录

```text
Enabled:  ~/.agents/skills
Disabled: ~/.agents/skills-disabled
```

Hermes 的 `skills.external_dirs` 指向 enabled 目录。

enabled 和 disabled 必须处于同一个文件系统。开关通过 `os.rename()` 移动整个 skill 目录，不能只修改 `SKILL.md`，也不能逐文件复制。

### Revision 状态

状态文件：

```text
/data/skill-state.json
```

每次实际状态变化递增 revision。提交必须保持：

```text
临时文件写入
→ 文件 fsync
→ os.replace
→ 父目录 fsync
```

读写和目录扫描由文件锁串行化。API 客户端使用 `expected_revision` 做乐观并发控制。

### OpenCode 刷新

仅移动目录不会刷新 OpenCode 的 instance skill cache。

目录移动后必须执行：

```http
POST /global/dispose
GET /skill?directory=/workspace
```

第二个请求是收敛校验。不能在校验成功前提交新 revision。

不要轻易改成 `/instance/dispose`：当前验证版本中该接口的 teardown 与 HTTP 响应存在时序问题；`/global/dispose` 是已经验证过的同步屏障。

### Hermes 刷新

Hermes 0.18.2 会缓存同一 session 的 system prompt。原生 `reload-skills` 只刷新 slash-command 映射，不能刷新 cached system prompt。

镜像对 Hermes 应用：

```text
patches/hermes-turn-revision.patch
```

补丁在每轮 prompt 恢复之后调用：

```python
refresh_after_prompt_restore(agent, system_message)
```

实现位于：

```text
src/skillpanel_hot_reload.py
```

逻辑是比较 `/data/skill-state.json` 与 system prompt 中的 marker：

```text
[SkillPanel-Revision:<revision>]
```

revision 变化时才执行：

- 清理 Hermes skill prompt cache。
- 重新扫描 slash-command 映射。
- 重建 `_cached_system_prompt`。
- 写入新 marker。
- 更新 session DB 中保存的 system prompt。

revision 未变化时必须 no-op，避免每轮重复生成完整 prompt。未设置 `HERMES_SKILL_STATE_FILE` 时也必须 no-op，以便补丁可被安全移除。

## 控制协议

### 查询

```http
GET /skills
```

返回：

- 当前 revision。
- skill 名称、描述、启用状态和实际目录。
- reconciliation 信息。
- OpenCode、Hermes 和控制器 PID。

### 切换

```http
PUT /skills/{name}
Content-Type: application/json
```

```json
{
  "enabled": false,
  "expected_revision": 148
}
```

关键响应语义：

- `changed=true`：发生了目录移动和 revision 提交。
- `changed=false`：已经是目标状态；revision 不增加，但仍刷新并校验 OpenCode。
- `409`：客户端 revision 过期，必须重新 `GET /skills`。
- `503`：OpenCode 刷新或收敛失败，控制器已尝试回滚。
- `507`：状态提交失败，控制器已尝试恢复目录、OpenCode catalog 和旧 state。
- `hermes_refresh=next-turn`：Hermes 将在下一轮比较 revision。

未来 VS Code 插件只能调用这个协议，不应直接移动目录，也不应同时编辑 OpenCode permission 与 Hermes disabled 配置。

## 外部安装规则

如果某个 skill 已经禁用，而安装器又向 enabled 目录写入同名 skill，策略固定为：

```text
disabled-wins
```

canonical disabled 副本继续保留，新 enabled 副本被移动到：

```text
skills-disabled/.skillpanel-conflicts/<name>/<timestamp-pid>
```

结果通过 API 的 `reconciliations` 返回。不要恢复成“发现重名就让全部接口 500”的行为。

## 回滚约束

正常切换事务顺序：

```text
加锁
→ 校验 expected_revision
→ rename 目录
→ OpenCode dispose
→ OpenCode catalog 校验
→ 原子提交 revision
→ 返回成功
```

任何提交前错误都必须回滚到旧目录和旧 catalog。状态提交错误还必须恢复旧 state。

外部目录漂移的 reconcile 也必须先刷新 OpenCode，再推进 revision，不能只更新 revision 让 Hermes 单方面刷新。

## 安全边界

宿主 OpenCode auth 以只读文件挂载。entrypoint 只把目标 provider 对象提取到：

```text
/run/skillpanel/opencode-auth.json
```

该目录是 tmpfs，目录权限 0700，文件权限 0600。不要把凭据写入镜像、项目文件、named volume 或日志。

Hermes 仍需要在自身进程环境中持有 MiniMax key。容器 root、Docker daemon 管理者和拥有 `docker exec` 权限的人仍属于可信边界。

## 不能宣称的能力

禁用 skill 可以保证：

- 下一轮 discovery/list 不再展示它。
- 新的显式 skill 加载被拒绝。
- Hermes 当前 cached system prompt 按新 revision 重建。
- OpenCode catalog 与 enabled 目录一致。

不能保证：

- 删除 conversation history 中已经加载的 skill 内容。
- 让模型真正 unlearn 已经看到的指令。
- 取消已经开始的 LLM 或 tool 调用。

如果未来需求是安全意义上的强撤销，应创建新 session，或者给历史 skill 注入增加可识别 metadata，并在组装历史时过滤已禁用 skill。不能只修改当前 system prompt 后声称旧内容已经消失。

## 不要重新尝试的方案

以下方案已经证明不满足目标：

1. 只移动目录：OpenCode 继续使用旧 instance cache。
2. OpenCode permission 与 Hermes disabled 双配置：两套状态无法原子提交，物理 catalog 语义也不一致。
3. OpenCode dispose 加 Hermes 原生 reload：Hermes slash 映射会刷新，但同-session cached system prompt 不会失效。

## 代码入口

```text
Dockerfile
compose.yaml

docker/entrypoint.sh
docker/supervisord.conf
docker/start-opencode.sh
docker/start-hermes.sh
docker/start-controller.sh

src/controller.py
src/skillpanel_hot_reload.py
src/bootstrap_config.py

patches/hermes-turn-revision.patch

tests/integration.py
tests/opencode_session.py
tests/comparisons.py
tests/test_controller.py
tests/test_hot_reload_module.py
```

职责划分：

- `controller.py`：扫描、锁、revision、rename、OpenCode refresh、回滚和 HTTP API。
- `skillpanel_hot_reload.py`：Hermes revision-aware prompt 刷新。
- `bootstrap_config.py`：合并 OpenCode/Hermes 配置，不覆盖卷中的其他用户设置。
- `integration.py`：Hermes WebSocket/session、结构测试和压力循环。
- `opencode_session.py`：OpenCode 真实 MiniMax 同-session 三轮测试。
- `comparisons.py`：失败对照实验。

## 后续 AI 的工作方式

如果任务是开发 VS Code 插件：

1. 不修改当前热更新协议。
2. 先基于 `GET /skills` 和 `PUT /skills/{name}` 定义 TypeScript client。
3. UI 展示 enabled、revision、pending、conflict 和 reconciliation 状态。
4. 409 时重新拉取并提示用户，不做静默覆盖。
5. 503/507 时展示回滚结果，不能只在 UI 中乐观切换。
6. 明确提示“正在执行的当前轮不会被撤销，下一轮生效”。

如果任务是升级 Hermes：

1. 先检查上游是否已原生支持 session-level skill revision。
2. 如果没有，重新验证 `turn_context.py`、prompt cache API、slash reload API 和 session DB API。
3. build-time patch 失败必须视为阻断，不能跳过补丁继续构建。
4. 重新运行全部真实同-session 测试和失败对照。

如果任务是修改控制器：

1. 保持 rename、OpenCode 收敛校验和 revision 提交的事务顺序。
2. 保持 state/OSError 故障回滚。
3. 保持 disabled-wins 行为。
4. 运行 unit、100-cycle、并发读取、Hermes LLM、OpenCode LLM 和 comparison 测试。

## 最小接手检查

```bash
cd /Users/xiaoxiao/MobileDisk/skillpanel

docker context use colima
docker buildx use colima
docker compose ps

curl -fsS http://127.0.0.1:8787/health | jq
curl -fsS http://127.0.0.1:8787/skills | jq
```

如果这两项健康检查正常，应从现有实现继续，不要重新设计或重建已经验证过的热更新机制。
