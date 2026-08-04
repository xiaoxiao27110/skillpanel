# `hermes-agent==0.18.2` 子 agent 支持调研

## 调研范围与证据基准

本报告严格以 PyPI wheel `hermes_agent-0.18.2-py3-none-any.whl` 内的源码为结论基准，没有安装该包。

- 下载命令：`python -m pip download --no-deps 'hermes-agent==0.18.2' -d hermes-pkg`
- wheel 路径：`.scratch/subagent-research/hermes-pkg/hermes_agent-0.18.2-py3-none-any.whl`
- 解包源码根目录：`.scratch/subagent-research/hermes-pkg/unpacked/`
- wheel SHA-256：`8f02155cfc84b28bd98551cd18dffec0efa9ec070dd08f90f1a850f1c779492f`，与 PyPI 0.18.2 JSON 元数据公布的 wheel digest 一致。
- `hermes_agent-0.18.2.dist-info/METADATA:2-3` 确认包名和版本为 `hermes-agent 0.18.2`。

包内 METADATA 指向公开仓库 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)。公开 release/tag [`v2026.7.7.2`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.7.7.2) 解引用到 commit `9de9c25f620ff7f1ce0fd5457d596052d5159596`；对 `run_agent.py`、`toolsets.py`、`tools/delegate_tool.py`、`tools/async_delegation.py`、`tools/skills_tool.py`、`agent/system_prompt.py`、`agent/skill_utils.py`、`agent/tool_dispatch_helpers.py` 做 SHA-256 对比，均与 wheel 内容一致。下文仍只把 wheel 内容当作裁决依据。

## 结论速览

| 问题 | 结论 |
|---|---|
| 1. 主 agent 能否派生子 agent | **能。**模型可调用 `delegate_task`，即时构造一个或多个隔离的 `AIAgent` 子实例；默认 CLI/coding/API 等工具包均包含该工具，但平台若关闭 delegation/相关工具集，模型就看不到它。 |
| 2. 如何定义和调用 | **不是静态命名 agent 配置，而是按任务动态创建。**调用参数是单个 `goal/context/role`，或 `tasks[]` 批量任务；每个任务动态生成专用 system prompt、独立 session/task id、终端状态和受限工具集。模型不能逐次选择 model 或 toolsets。 |
| 3. 能否并行、数量上限 | **能并行。**单次 batch 的任务数和其线程池并发默认均为 3，由 `delegation.max_concurrent_children` 控制，只有下限 1、没有硬上限。后台池限制的是“delegation unit/batch”而非实际 child 数；源码没有真正的全局实际子 agent 数上限。默认配置下，3 个满批次可静态推导为最多 3×3=9 个一层 child 同时运行；开启嵌套后还可能继续乘法增长。 |
| 4. 同步还是后台 | **顶层对话调用通常强制后台；内部仍用线程同步 join。**顶层模型调用忽略 `background` 参数并强制后台，先返回 handle；单个 batch 内的 children 并行运行但要全部 join，随后以一次 consolidated completion 回到新对话 turn。子 agent 的嵌套委派、直接 Python 调用默认、无异步回传通道的 stateless API、后台池满时则同步阻塞。 |
| 5. 能否嵌套 | **默认不能，配置后能。**默认 `max_spawn_depth=1`；设为 2+ 且 `orchestrator_enabled=true`，调用时传 `role="orchestrator"` 即可让子 agent 再调用 `delegate_task`，深度没有硬上限。**但存在实现缺口：复合工具集可能让 `leaf` 也保留 `delegate_task`；深度提高后，leaf 也可能实际嵌套。** |
| 6. skills 是否关联/继承 | **只有工具集和共享配置层面的间接关联，没有“把已加载 skill 绑定给 subagent”的专门机制。**子 agent 通常继承父 agent 的 skills 工具可用性，并自己读取全局 disabled 配置；父会话已经 preload/load 的 skill 正文不会自动复制。`disabled_toolsets` 和 gateway 的 `platform_disabled` 均存在不能保证完整继承的静态代码风险，详见第 6 节。 |

---

## 1. 主 agent 有无 subagent/task/delegate/spawn 机制

### 确定事实：有，工具名为 `delegate_task`

`toolsets.py:245-248` 定义单独的 delegation 工具集：

```python
"delegation": {
    "description": "Spawn subagents with isolated context for complex subtasks",
    "tools": ["delegate_task"],
    "includes": []
},
```

`tools/delegate_tool.py:3321-3335` 把它定义成模型可见的 function tool；`tools/delegate_tool.py:3443-3458` 注册处理器：

```python
DELEGATE_TASK_SCHEMA = {
    "name": "delegate_task",
    "description": (
        "Spawn one or more subagents in isolated contexts. "
        ...
    ),
    ...
}

registry.register(
    name="delegate_task",
    toolset="delegation",
    schema=DELEGATE_TASK_SCHEMA,
    handler=lambda args, **kw: delegate_task(...),
)
```

`tools/delegate_tool.py:2342-2353` 是实际入口：

```python
def delegate_task(..., parent_agent=None) -> str:
    """
    Spawn one or more child agents to handle delegated tasks.
    """
```

`toolsets.py:64-65` 又把 `delegate_task` 放进默认 core tools；`toolsets.py:346-361` 的 coding posture 也包含它。因此常见主 agent 默认可在对话中调用它。是否最终出现于某一会话的模型 schema，仍取决于该会话的 effective toolsets。

### 子 agent 的隔离程度

`tools/delegate_tool.py:3-16` 的模块说明与后续实现一致：新鲜对话、独立 task id/终端状态、受限工具集，父上下文只接收最终摘要，不接收子 agent 的中间推理和工具调用。

```python
Spawns child AIAgent instances with isolated context, restricted toolsets,
and their own terminal sessions.
...
The parent's context only sees the delegation call and the summary result,
never the child's intermediate tool calls or reasoning.
```

## 2. 子 agent 如何定义和调用

### 确定事实：按任务动态构造，不是预先声明命名角色

wheel 中没有找到“先注册一个命名 subagent 定义、再按名字调用”的静态 registry。实际是每次 `delegate_task` 都调用 `_build_child_agent`，构造通用 `AIAgent`。

`tools/delegate_tool.py:661-684` 只用委派的 `goal/context` 构造聚焦 system prompt：

```python
def _build_child_system_prompt(goal, context=None, ...):
    parts = [
        "You are a focused subagent working on a specific delegated task.",
        "",
        f"YOUR TASK:\n{goal}",
    ]
    if context and context.strip():
        parts.append(f"\nCONTEXT:\n{context}")
```

`tools/delegate_tool.py:1301-1324` 生成真正的实例：

```python
child = AIAgent(
    ...
    enabled_toolsets=child_toolsets,
    quiet_mode=True,
    ephemeral_system_prompt=child_prompt,
    platform="subagent",
    skip_context_files=True,
    skip_memory=True,
    session_db=getattr(parent_agent, "_session_db", None),
    parent_session_id=getattr(parent_agent, "session_id", None),
)
```

这里的 `skip_context_files=True`、`skip_memory=True` 和新 `ephemeral_system_prompt` 说明它不是父 agent 的对话 fork；只是共享工作区、session DB 父子关系、凭据/模型等运行配置。

### 模型调用参数

`tools/delegate_tool.py:3336-3397` 的 schema 支持：

- 单任务：`goal`、可选 `context`、可选 `role`；
- 批量：`tasks: [{goal, context, role}, ...]`；
- `role` 只有 `leaf` / `orchestrator`；
- `background` 仍保留在 schema，但文案明确为 deprecated/ignored。

模型侧示意：

```json
{"goal":"检查认证模块的竞态","context":"仓库在 /abs/path；只读源码","role":"leaf"}
```

或：

```json
{
  "tasks": [
    {"goal":"审计 API 层","context":"...","role":"leaf"},
    {"goal":"审计数据库层","context":"...","role":"leaf"}
  ]
}
```

### 模型不能逐调用选择 model/toolsets

`tools/delegate_tool.py:2488-2505` 明确将 `toolsets=None` 传给每个 child：

```python
child = _build_child_agent(
    ...
    # Subagents always inherit the parent's toolsets; the model
    # cannot choose or narrow them (no model-facing toolsets arg).
    toolsets=None,
    model=creds["model"],
    ...
)
```

model 默认继承父 agent；只能由用户通过全局 `delegation.provider` / `delegation.model` 固定子 agent 路由，不能由模型在某次工具调用里选。

## 3. 能否并行派生多个，数量/并发上限

### 确定事实：单 batch 并行，默认上限 3，可配置且无硬 ceiling

`hermes_cli/config.py:2170-2174` 的默认配置：

```python
"max_concurrent_children": 3,  # max parallel children per batch
                              # AND max concurrent background delegation units.
                              # ... Floor of 1, no ceiling.
```

`tools/delegate_tool.py:354-392` 的读取逻辑将值做 `max(1, int(val))`，大于 10 只告警，不拒绝；未配置时回退 `_DEFAULT_MAX_CONCURRENT_CHILDREN = 3`。

`tools/delegate_tool.py:2431-2446` 在运行时拒绝超过 N 项的 batch：

```python
max_children = _get_max_concurrent_children()
...
if len(tasks) > max_children:
    return tool_error(
        f"Too many tasks: {len(tasks)} provided, but "
        f"max_concurrent_children is {max_children}. "
        ...
    )
```

`tools/delegate_tool.py:2529-2548` 用线程池并行运行同一 batch 的 children：

```python
from tools.daemon_pool import DaemonThreadPoolExecutor
with DaemonThreadPoolExecutor(max_workers=max_children) as executor:
    for i, t, child in children:
        future = executor.submit(_run_single_child, ...)
```

### 确定事实：同一模型 turn 的多个 `delegate_task` call 也只保留 N 个

`run_agent.py:3756-3784` 统计同一 assistant turn 的 `delegate_task` tool calls，超过 `max_concurrent_children` 的会被截掉。

### 重要口径：后台上限按 batch/unit 计，不按 child 计

`tools/async_delegation.py:311-337` 明确写出：一个完整 fan-out batch 占 **一个** async slot，batch 内并发另由 `max_concurrent_children` 控制：

```python
def dispatch_async_delegation_batch(...):
    """Dispatch a WHOLE fan-out batch as ONE background unit.
    ...
    We occupy ONE async slot for the whole batch (the in-batch
    parallelism is bounded separately by ``max_concurrent_children``)
    ...
    """
```

`tools/async_delegation.py:365-381` 只统计 `_records` 里 status 为 `running` 的 delegation record，然后与 N 比较。

因此源码层面没有“全进程实际 child agent 数 <= N”的约束。**静态推导**：默认 N=3 时，同一 turn 最多保留 3 个 `delegate_task` calls；每个 call 都可以是 3-task batch，而每个 batch 只占一个后台 slot，所以可同时出现 3×3=9 个一层实际 child。开启嵌套后，理论并发还可继续增长。这个 N² 是由调用链推导的上界示例，不是源码里声明的全局承诺；同步回退和不同 turn/嵌套还会使实际全局情况更复杂。

## 4. 同步阻塞还是异步后台

### 顶层模型对话：强制后台，模型不能选择

`run_agent.py:5654-5683` 是主 agent 的真实模型调用路径：

```python
# Delegations from the top-level MODEL always run in the background —
# the model does not get to choose.
_is_subagent = getattr(self, "_delegate_depth", 0) > 0
return _delegate_task(
    ...
    background=(not _is_subagent),
    parent_agent=self,
)
```

`tools/delegate_tool.py:3383-3392` 的 schema 也说明 `background` 参数 deprecated/ignored，顶层单任务会自动后台执行。

### 实际 batch 语义：一个后台 handle，全部 children 完成后一次回传

`tools/delegate_tool.py:2759-2766` 的真实分支：

```python
# run the WHOLE batch as one async unit
# ... joins on every child and produces ONE consolidated results block,
# which re-enters the conversation as a single message when ALL children finish.
if background:
    from tools.async_delegation import dispatch_async_delegation_batch
```

`tools/delegate_tool.py:2830-2866` 调用一次 `dispatch_async_delegation_batch`，成功后返回一个 `delegation_id`。`tools/async_delegation.py:426-473` 在全部完成后向 `completion_queue` 放入一个带完整 `results` 数组的 `async_delegation` event。

**注意 wheel 内有 stale 文案矛盾。**`tools/delegate_tool.py:2380-2384`、`3198-3202` 和 `run_agent.py:5664-5669` 的注释/模型说明仍说 batch 是 N 个独立 handle、分别完成；但实际调用明确走 `dispatch_async_delegation_batch`，其实现只创建一个 record/`delegation_id`、一个 executor submit，并在整个 runner join 后发一个 consolidated event。因此本报告按执行调用链判定为“一批一个 handle、一起回传”。

### 以下情况会同步阻塞

1. **嵌套委派**：上面的 `_is_subagent` 为真时强制 `background=False`；orchestrator 必须等 workers 结果，才能合成自己的总结。
2. **直接 Python 调用**：`delegate_task(..., background=None)` 在 `tools/delegate_tool.py:2385` 归一化为 False；`tools/delegate_tool.py:3405-3415` 也明确说 direct Python caller 保留历史同步默认。
3. **stateless HTTP/API endpoint**：`tools/delegate_tool.py:2770-2796` 检查 `async_delivery_supported()`；没有持久回传通道时直接 `_execute_and_aggregate()` 同步运行。
4. **后台池满或调度失败**：`tools/delegate_tool.py:2868-2885` 同步 inline fallback，而不是排队。

### 后台不是 durable job

`tools/delegate_tool.py:3212-3217` 明确说 background delegation 不持久：关闭父 session、进程退出会丢弃，`/stop` 会取消。要跨 session/进程持久运行应使用 cronjob 或终端后台任务，而不是 subagent delegation。

## 5. 能否嵌套（子 agent 再派子 agent）

### 设计上的确定结论：默认关闭，配置后支持 orchestrator nesting

`tools/delegate_tool.py:118-131`：

```python
_DEFAULT_MAX_CONCURRENT_CHILDREN = 3
MAX_DEPTH = 1  # flat by default: parent (0) -> child (1)
...
# No upper ceiling on spawn depth
```

`hermes_cli/config.py:2175-2179` 也把默认写成：

```python
"max_spawn_depth": 1,  # 1 = flat, 2 = orchestrator→leaf, 3+ = deeper
"orchestrator_enabled": True,
```

`tools/delegate_tool.py:467-503` 规定深度只做 floor=1，没有 ceiling；`tools/delegate_tool.py:2387-2402` 在当前 parent depth 已达到上限时拒绝。

角色处理在 `tools/delegate_tool.py:1078-1087`：

```python
child_depth = getattr(parent_agent, "_delegate_depth", 0) + 1
max_spawn = _get_max_spawn_depth()
orchestrator_ok = _get_orchestrator_enabled() and child_depth < max_spawn
effective_role = role if (role == "orchestrator" and orchestrator_ok) else "leaf"
```

若角色最终是 orchestrator，`tools/delegate_tool.py:1137-1142` 会把之前剥掉的 `delegation` 工具集重新加入，允许子 agent 再调用 `delegate_task`。因此按设计：

- 默认 `max_spawn_depth=1`：顶层可生一层 child；child 再调会被深度 guard 拒绝。
- `max_spawn_depth=2`：顶层可生 depth-1 orchestrator；它可同步派生 depth-2 leaf。
- `max_spawn_depth=3+`：可继续更深；每一层要显式把需要再分解的 child 设为 `role="orchestrator"`。
- `orchestrator_enabled=false` 会把请求的 orchestrator 静默降级为 leaf。

### 重要实现缺口：`leaf` 角色不是可靠的工具隔离边界

源码声称 `DELEGATE_BLOCKED_TOOLS` 包含 `delegate_task`（`tools/delegate_tool.py:44-53`），并称 leaf 不能递归委派。但实际过滤函数在 `tools/delegate_tool.py:766-783` 过滤的是**工具集名字**：

```python
_COMPOSITE_BLOCKED_TOOLSETS = frozenset({"delegation", "code_execution"})
blocked_toolset_names = {
    name for name, defn in TOOLSETS.items()
    if name in _COMPOSITE_BLOCKED_TOOLSETS
    or all(t in DELEGATE_BLOCKED_TOOLS for t in defn.get("tools", []))
}
return [t for t in toolsets if t not in blocked_toolset_names]
```

`hermes-cli`、`coding` 等复合工具集不只含 blocked tools，所以不会被移除；而 `toolsets.py:64-65` 和 `346-361` 显示这些复合集里确实包含 `delegate_task`。child 随后在 `tools/delegate_tool.py:1314` 把这个复合工具集交给 `AIAgent(enabled_toolsets=child_toolsets)`，它会重新展开出 `delegate_task`。

同时，`delegate_task()` 的运行 guard（`tools/delegate_tool.py:2387-2402`）只检查 `_delegate_depth` 与 `max_spawn_depth`，没有检查调用者的 `_delegate_role`。因此可做如下**高置信度静态推断**：

- 默认深度 1 仍会挡住 leaf 的再次委派，所以默认行为表面正常；
- 一旦把 `max_spawn_depth` 提高，且父会话使用会展开出 `delegate_task` 的复合工具集，标为 `leaf` 的 child 仍可能看到并成功调用 `delegate_task`；甚至可在更深配置下给自己的 child 请求 orchestrator 角色。

所以回答“能否嵌套”时，正确的实际结论是：**默认深度阻止；配置后明确支持；而 role=leaf 本身不能当作强安全边界。**

## 6. skill 系统与子 agent 的关联、继承状态

### 二者是独立工具集，没有专门的“skill 驱动 subagent”类型

`toolsets.py:166-169` 把 skills 定义为独立工具集：

```python
"skills": {
    "description": "Access, create, edit, and manage skill documents...",
    "tools": ["skills_list", "skill_view", "skill_manage"],
},
```

delegation 则是另一独立工具集（`toolsets.py:245-248`）。`tools/delegate_tool.py` 内没有 skill-specific 参数、已加载 skill 列表或 skill 正文复制逻辑。

### 工具集层面：通常继承父 agent 的 skills 可用性

`tools/delegate_tool.py:1100-1135` 的 child toolset 选择逻辑：

```python
parent_enabled = getattr(parent_agent, "enabled_toolsets", None)
if parent_enabled is not None:
    parent_toolsets = set(parent_enabled)
elif parent_agent and hasattr(parent_agent, "valid_tool_names"):
    parent_toolsets = { ... from parent_agent.valid_tool_names ... }
...
child_toolsets = _strip_blocked_tools(...)
```

skills 不在 blocked tool 列表中。因此：

- 父 agent 的有效 toolsets 包含 `skills` 时，child 通常也有 `skills_list` / `skill_view` / `skill_manage`；
- 父 agent 没有 skills 工具时，child 不会凭空获得；
- 模型不能在某次 `delegate_task` 调用里自行打开/关闭 skills，因为 model-facing schema 没有 `toolsets` 参数。

`agent/system_prompt.py:260-290` 进一步显示：只有 child 实际加载了至少一个 skills tool，才会构造 skills index 放进它自己的 system prompt。

### 已在父会话加载/preload 的 skill 正文：不继承

子 agent 是 fresh conversation；它的专用 prompt 只由 `goal/context/workspace/role` 生成（`tools/delegate_tool.py:661-736`），构造 `AIAgent` 时传入的是新 `ephemeral_system_prompt`，没有复制父 agent 的当前 system message、preloaded skill payload 或对话中 `skill_view` 的结果（`tools/delegate_tool.py:1301-1324`）。

因此，父 agent 刚刚读取过某个 skill，并不意味着 child 已经拥有该正文。若子任务必须遵守某 skill，父 agent 需要把关键约束写入 `context`，或明确要求 child 自己调用 `skill_view`。

### 单个 skill 的 disabled 开关：共享 config 会被重新读取，而非复制快照

`agent/skill_utils.py:353-388` 从共享 config 读取 `skills.disabled`，并与解析出的平台级 `skills.platform_disabled[platform]` 取并集：

```python
global_disabled = _normalize_string_set(skills_cfg.get("disabled"))
if resolved_platform:
    platform_disabled = (skills_cfg.get("platform_disabled") or {}).get(
        resolved_platform
    )
    if platform_disabled is not None:
        return global_disabled | _normalize_string_set(platform_disabled)
return global_disabled
```

`tools/skills_tool.py:1199-1208` 在 `skill_view` 时再次拒绝 disabled skill；`tools/skills_tool.py:620-637` 的 `skills_list` 默认也过滤 disabled names。因此，在父子解析到同一个 active `HERMES_HOME`/profile 的前提下，全局 `skills.disabled` 对 child 有效；但这是 child 自己从共享配置重新读到的结果，不是父 agent 把某个“开关对象”复制给 child。

### 两个不能宣称“完整继承”的缺口

1. **父 agent 的 `disabled_toolsets` 没有显式传给 child。**`tools/delegate_tool.py:1104-1133` 在 `parent.enabled_toolsets` 非空时直接从该列表构造 child toolsets，而 `tools/delegate_tool.py:1301-1324` 只传 `enabled_toolsets=child_toolsets`，没有传父 agent 的 `disabled_toolsets`。如果父 agent 同时启用了复合包（如 `hermes-cli`）又用 `disabled_toolsets=["skills"]` 做减法，child 重新展开复合包时可能把 skills tools 带回来。这是由参数流直接推导出的实现风险，和源码注释“disabled tools don't leak”不完全一致。

2. **gateway 的 `platform_disabled` 没有显式做 thread ContextVar 传播。**平台级禁用依赖 `HERMES_SESSION_PLATFORM` ContextVar（`agent/skill_utils.py:375-387`）。`tools/thread_context.py:4-6` 明确说明裸 `ThreadPoolExecutor` worker 从空 ContextVars 开始；而子对话在 `tools/delegate_tool.py:1919-1929` 通过裸 `_timeout_executor.submit(_run_with_thread_capture)` 运行，没有使用该模块提供的 `propagate_context_to_thread` 包装。因此可做的保守结论是：全局 `skills.disabled` 可靠；gateway 的平台级 disabled 状态**没有完整继承保证，静态上可能退化成只看到全局禁用**。这是源码审计推断，未启动依赖完整的 gateway 做运行时复现。

## 最终总结

`hermes-agent 0.18.2` 已经有较完整的会话内子 agent 机制，核心是模型工具 `delegate_task`：动态创建隔离 `AIAgent`，支持单任务与 batch 并行、后台 completion、可观测/中断、可配置模型路由和可选多层 orchestrator。它不是静态“agent definition”系统；child 的身份主要由当次 goal/context/role 和继承的运行配置决定。

默认状态是：每批最多 3 个、一层 fan-out、顶层模型调用后台返回。用户可提高 `max_concurrent_children` 和 `max_spawn_depth`，两个参数都没有硬 ceiling。要注意 `max_concurrent_children` 并非全局实际 child 数上限，因为后台按 batch unit 计数；默认就可形成 3 个满 batch、共 9 个一层 child 的并发形态。

同步/异步的准确口径是：主 agent 顶层对话通常强制后台，但一个 batch 内部仍同步 join，最终一个 handle 对应一次合并回传；嵌套委派则同步阻塞。wheel 内少数说明仍写成 batch 的 N 个独立 handle，属于与实际调用链不一致的 stale 文案。

skills 与 subagent 没有直接编排关系。child 通常继承 skills 工具可用性、重新读取共享 disabled 配置，却不会继承父对话里已经加载的 skill 正文。更重要的是，复合 toolset 过滤存在边界缺口：它既可能让 leaf 保留 `delegate_task`，也可能让父侧用 `disabled_toolsets` 关闭的 skills 在 child 中重新出现；因此 `role="leaf"` 和“父 agent 的所有 skill 开关完整继承”都不宜被当作强安全承诺。
