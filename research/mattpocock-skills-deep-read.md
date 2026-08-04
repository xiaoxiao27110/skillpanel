# Matt Pocock skills 仓库深读报告

> 调查目的：评估 https://github.com/mattpocock/skills 对"渐进式人机协同收敛"工作流（7 个能力维度）的覆盖度。
>
> **来源说明**：本报告全部结论基于仓库一手文件。抓取时 `main` 分支 HEAD 为 commit `2ab958093e83e0ec752e6c1c5932da465bf23e0c`，下文所有引用 URL 均固定在该 commit，避免 raw CDN 缓存到旧版本（未固定的 `raw/.../main/README.md` 实际返回的是旧版 README，skill 清单与当前目录树不符）。仓库实际布局为 `skills/<bucket>/<name>/SKILL.md`（bucket = engineering / productivity / misc / personal / in-progress / deprecated），任务书中的 `wayfinder/SKILL.md` 等即 `skills/engineering/wayfinder/SKILL.md`，`grilling`、`handoff` 在 `skills/productivity/` 下。
>
> 布局来源：[目录树 API](https://api.github.com/repos/mattpocock/skills/git/trees/2ab958093e83e0ec752e6c1c5932da465bf23e0c?recursive=1)、[CLAUDE.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/CLAUDE.md)。

---

## 1. Skill 清单总表

仓库把 skill 分为 **user-invoked**（只能人输入 `/name` 触发，frontmatter 带 `disable-model-invocation: true`，负责编排）和 **model-invoked**（人或模型都可触发，是可复用的"纪律"）。规则：user-invoked 可调用 model-invoked，**永远不能调用另一个 user-invoked**；skill 间依赖用 `/skill` 散文式调用表达，不跨目录深链文件。来源：[.agents/invocation.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/.agents/invocation.md)。

### Engineering（日常代码工作）— 来源：[README Reference 节](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/README.md)

| Skill                             | 调用方式 | 一句话                                                                                         |
| --------------------------------- | -------- | ---------------------------------------------------------------------------------------------- |
| `ask-matt`                      | user     | 路由器：回答"当前情况该用哪个 skill/流程"                                                      |
| `grill-with-docs`               | user     | 访谈式打磨计划，同时沉淀`CONTEXT.md` 与 ADR                                                  |
| `triage`                        | user     | 把 issue/外部 PR 推进 triage 角色状态机                                                        |
| `improve-codebase-architecture` | user     | 扫描"深化模块"机会，HTML 报告 + 逐项 grill                                                     |
| `setup-matt-pocock-skills`      | user     | 每仓库跑一次：配置 issue tracker、triage 标签、domain 文档布局                                 |
| `to-spec`                       | user     | 把当前对话合成 spec 发布到 issue tracker（不访谈）                                             |
| `to-tickets`                    | user     | 把计划/spec 拆成 tracer-bullet ticket，声明 blocking edges                                     |
| `implement`                     | user     | 按 spec/tickets 实现，内部驱动`/tdd`，收尾跑 `/code-review`                                |
| `wayfinder`                     | user     | 把超过一个会话的大块工作绘成 issue tracker 上的 decision ticket 共享地图，逐票推进直到路线清晰 |
| `prototype`                     | model    | 用一次性代码回答设计问题（逻辑/状态 → 终端程序；UI → 多变体）                                |
| `diagnosing-bugs`               | model    | 难 bug/性能回归的诊断循环：复现→最小化→假设→插桩→修复→回归测试                            |
| `research`                      | model    | 后台 agent 对一手来源做调研，产出带引用的 Markdown 存进仓库                                    |
| `tdd`                           | model    | 红-绿循环，一次一个 vertical slice                                                             |
| `domain-modeling`               | model    | 主动构建/打磨领域模型，inline 更新`CONTEXT.md` 与 ADR                                        |
| `codebase-design`               | model    | 深模块设计词汇表（module/interface/depth/seam/adapter/leverage/locality）                      |
| `code-review`                   | model    | 双轴 review（Standards + Spec），并行子 agent 各自独立报告                                     |
| `resolving-merge-conflicts`     | model    | 逐 hunk 解决进行中的 merge/rebase 冲突                                                         |

### Productivity（非代码通用）— 同上来源

| Skill                    | 调用方式 | 一句话                                                                   |
| ------------------------ | -------- | ------------------------------------------------------------------------ |
| `grill-me`             | user     | 无代码库场景的 relentless 访谈（`/grilling` 的薄封装）                 |
| `handoff`              | user     | 把当前对话压缩成 handoff 文档，供新会话/新 agent 继续                    |
| `teach`                | user     | 跨多会话教学，以当前目录为有状态工作区                                   |
| `writing-great-skills` | user     | 写好 skill 的词汇与原则参考                                              |
| `grilling`             | model    | relentless 访谈原语，`grill-me` / `grill-with-docs` 背后的可复用循环 |

另有不推广目录（不参与评估）：`skills/in-progress/`（batch-grill-me、claude-handoff、loop-me、setup-ts-deep-modules）、`skills/deprecated/`（design-an-interface、qa、request-refactor-plan、ubiquitous-language）、`skills/misc/`。来源：[目录树](https://api.github.com/repos/mattpocock/skills/git/trees/2ab958093e83e0ec752e6c1c5932da465bf23e0c?recursive=1)、[CLAUDE.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/CLAUDE.md)。

---

## 2. 逐个 skill 四要素

四要素 = ①做什么 ②读写什么持久状态 ③显式调用哪些其他 skill ④有无决策分类 / 决策状态追踪 / 证据反馈-重开机制。

### 2.1 wayfinder（重点）

来源：[skills/engineering/wayfinder/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/wayfinder/SKILL.md)、[setup-matt-pocock-skills/issue-tracker-local.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/setup-matt-pocock-skills/issue-tracker-local.md)、[.changeset/wayfinder-decision-tickets.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/.changeset/wayfinder-decision-tickets.md)

- **做什么**：把"超过一个 agent 会话容量、笼罩在雾中"的大块工作绘制成 issue tracker 上的一张 **shared map**，其组成单元是 **decision ticket**——"其解决结果是一个决策的问题，而不是要执行的构建切片"。每会话最多解决一张票（research 票例外），直到"通往目的地的路清晰"。明确 **plan, don't do**："produce decisions, not deliverables"。
- **持久状态**：
  - map = tracker 上一个带 `wayfinder:map` 标签的 issue，是**索引不是存储**，body 固定五节：`## Destination` / `## Notes` / `## Decisions so far`（只 gist + 链接，append-only）/ `## Not yet specified`（fog of war）/ `## Out of scope`。
  - ticket = map 的子 issue，body 只有 `## Question`，大小以"一个 100K token 会话"为度；带 `wayfinder:` 类型标签（`research`/`prototype`/`grilling`/`task`）；认领 = assign 给驱动者；答案在解决时以 **resolution comment** 记录并关闭 issue，map 的 Decisions-so-far 追加一条 context pointer。
  - blocking 用 tracker 原生依赖关系（让 frontier 在 tracker UI 里可视）；无原生 blocking 时退回 body 约定。**frontier = 开放 + 未被阻塞 + 未被认领的子票**。
  - 本地 markdown tracker 时落盘为：`.scratch/<effort>/map.md` + `.scratch/<effort>/issues/NN-<slug>.md`，票内有 `Type:`、`Status:`（`claimed`/`resolved`）、`Blocked by: NN, NN` 行；frontier 靠扫描该目录；解决 = 在 `## Answer` 下追加答案、置 `Status: resolved`、回写 map.md 的 Decisions-so-far。
- **显式调用的 skill**：`/setup-matt-pocock-skills`（未配置 tracker 时的前置）；`/grilling` + `/domain-modeling`（chart 阶段命名 destination、广度优先映射 frontier、以及解票时的默认手段）；`/research`（为每张 research 票起并行 subagent，成果放 `research/` 临时分支）；`/prototype`（prototype 票）。完成后不构建、交接出去——据 [ask-matt](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/ask-matt/SKILL.md)，地图清晰后汇入主流于 `/to-spec`。
- **决策分类**：**有，且是全套里最显式的**。四类票 + HITL/AFK 二分：research（AFK，查外部资料）、prototype（HITL，造便宜 artifact 给人反应）、grilling（HITL，一次一问，默认类型）、task（HITL 或 AFK，决策前置的手工活）。"A HITL ticket only resolves through that live exchange; the agent never stands in for the human's side of it."
- **决策状态追踪**：**有**。map 索引 + Decisions-so-far + frontier 查询 + 认领机制 + fog（Not yet specified）+ Out of scope；支持多会话并发（"expect other sessions to be editing the tracker concurrently"）。
- **证据反馈/重开**：**弱**。解票后："If the decision invalidates other parts of the map, update or delete those tickets"——只能改/删**仍未关闭**的票；答案使某票越界时关闭该票并记一行到 Out of scope；fog 可"毕业"成新票。但 **没有重开已关闭决策的机制**："a closed ticket is unambiguously off the frontier"，Decisions-so-far 只增不改。也没有从 tdd/code-review/implement 回流到 map 的边。

### 2.2 grill-with-docs

来源：[skills/engineering/grill-with-docs/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/grill-with-docs/SKILL.md)（全文仅 3 行）

- **做什么**：带文档产出的 grilling 会话——访谈打磨计划，同时建 domain model。正文全文："Run a `/grilling` session, using the `/domain-modeling` skill."
- **持久状态**：自身无；经由 domain-modeling 落 `CONTEXT.md` + `docs/adr/`。
- **调用**：`/grilling`、`/domain-modeling`。
- **决策分类/状态/反馈**：自身无，全部继承自 grilling 与 domain-modeling（见下）。

### 2.3 grilling

来源：[skills/productivity/grilling/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/productivity/grilling/SKILL.md)

- **做什么**：relentless 访谈原语——一次只问一个问题，沿决策树逐枝推进，每问附推荐答案，直到达成共识才行动。
- **持久状态**：无（纯会话内）。
- **调用**：无。
- **决策分类**：**有二分**——"If a *fact* can be found by exploring the environment (filesystem, tools, etc.), look it up rather than asking me. The *decisions*, though, are mine"（事实 AI 自查，决策归人）。
- **决策状态/反馈**：无持久状态、无重开机制。注意其收敛模式是**全量收敛**："until every branch of the decision tree is resolved"、"Do not act on it until I confirm we have reached a shared understanding"——与"不要求所有决策完成后才编码"存在张力（见 §4 维度 3）。

### 2.4 to-spec

来源：[skills/engineering/to-spec/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/to-spec/SKILL.md)

- **做什么**：把当前对话上下文合成 spec（PRD）发布到 issue tracker，**明确不做访谈**；发布前先与用户确认测试 seam。
- **持久状态**：spec 发布到 tracker（本地 tracker 时为 `.scratch/<feature>/spec.md`，见 issue-tracker-local.md 约定）；打 `ready-for-agent` 标签。模板节：Problem Statement / Solution / User Stories / Implementation Decisions / Testing Decisions / Out of Scope / Further Notes。允许 inline prototype 产出的"编码了决策的片段"（状态机、reducer、schema、type shape）。
- **调用**：`/setup-matt-pocock-skills`（前置）；读 `CONTEXT.md` 词汇、遵守相关 ADR。
- **决策分类/状态/反馈**：无分类；spec 本身是决策的沉淀物但无状态机；无重开机制。

### 2.5 to-tickets

来源：[skills/engineering/to-tickets/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/to-tickets/SKILL.md)

- **做什么**：把计划/spec/对话拆成 **tracer-bullet vertical slice** ticket（每片穿透 schema/API/UI/tests 全层、可独立演示、大小适配单个全新上下文窗口），逐票声明 **blocking edges**，发布前 quiz 用户确认粒度与边。宽重构走 expand–contract 例外。
- **持久状态**：
  - 本地 tracker：`.scratch/<feature>/issues/NN-<slug>.md`，一票一文件，按依赖序从 `01` 编号（blocker 在前），每文件含 `## Blocked by`（列编号/标题或 "None — can start immediately"）、`Status: ready-for-agent`、验收标准 checkbox、`## Parent`。
  - 真实 tracker：一票一 issue，用平台原生 blocking/sub-issue 关系；否则在 "Blocked by" 里列 issue 引用；打 `ready-for-agent` 标签。
  - "Work the **frontier**: any ticket whose blockers are all done."
- **调用**：`/setup-matt-pocock-skills`（前置）；读 `CONTEXT.md`/ADR。
- **决策分类/状态/反馈**：无决策分类；有执行层面的 frontier/阻塞状态；无证据反馈或重开（明确 "Do NOT close or modify any parent issue"）。

### 2.6 implement

来源：[skills/engineering/implement/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/implement/SKILL.md)（全文 8 行的薄编排器）

- **做什么**：实现 spec/tickets 描述的工作并提交。
- **持久状态**：无新增（从 ticket 读取上下文）。
- **调用**：`/tdd`（"where possible, at pre-agreed seams"）、`/code-review`（"Once done"）；定期跑 typecheck 与测试。
- **决策分类/状态/反馈**：无。review 的发现如何处理未规定——没有回流到 ticket/spec 的指令。

### 2.7 tdd

来源：[skills/engineering/tdd/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/tdd/SKILL.md)

- **做什么**：红→绿循环纪律：好测试的定义、seam（测试落点）、反模式（implementation-coupled / tautological / horizontal slicing）、循环规则。
- **持久状态**：无；读 `CONTEXT.md` 与相关 ADR；参考文件 [tests.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/tdd/tests.md)、[mocking.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/tdd/mocking.md)。
- **调用**：指向 `code-review`——"Refactoring is not part of the loop. It belongs to the review stage (see the `code-review` skill)"。
- **决策相关**：seam 需预先与用户确认（"No test is written at an unconfirmed seam"）；vertical slice "one test → one implementation → repeat"，每个测试是响应上一循环所得的 tracer bullet。无决策状态与反馈机制。

### 2.8 code-review

来源：[skills/engineering/code-review/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/code-review/SKILL.md)

- **做什么**：对固定点（commit/branch/tag/merge-base）以来的 diff 做双轴 review：**Standards**（仓库成文标准 + Fowler《重构》第 3 章 12 种 smell 基线）与 **Spec**（是否忠实实现 originating issue/PRD），两个并行子 agent 互不污染，分别报告、不合并不重排。
- **持久状态**：不写任何文件（只出报告）；读 `docs/agents/issue-tracker.md`、标准文件（`CODING_STANDARDS.md`/`CONTRIBUTING.md`）、spec（从 commit message 的 issue 引用溯源）。
- **调用**：`/setup-matt-pocock-skills`（配置缺失时）。
- **决策分类/状态/反馈**：发现只呈现给人（"Present the two reports"）；spec 缺失则跳过 Spec 轴。**没有**把发现写回 spec/ticket/ADR 的机制。

### 2.9 handoff

来源：[skills/productivity/handoff/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/productivity/handoff/SKILL.md)

- **做什么**：把当前对话压缩成 handoff 文档，让新 agent/新会话接续工作；含 "suggested skills" 节；不复制已被其他 artifact（spec/计划/ADR/issue/commit/diff）承载的内容，按路径/URL 引用；脱敏。
- **持久状态**：写到 **OS 临时目录、明确不进工作区**——因此它是会话桥，不是仓库内共享状态。
- **调用**：无显式调用（仅在文档里"建议"后续 skill）。
- **决策分类/状态/反馈**：无。

### 2.10 prototype

来源：[skills/engineering/prototype/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/prototype/SKILL.md)、[LOGIC.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/prototype/LOGIC.md)、[UI.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/prototype/UI.md)

- **做什么**："throwaway code that answers a question"——逻辑/状态问题 → 一次性交互终端程序；UI 问题 → 单路由多个可切换的激进变体。
- **持久状态**：原型本体是临时的；**完成后捕获**：提交到临时分支（不进 main），在 implementation issue 上留指向该分支的 context pointer；"Capture the answer too — the verdict and the question it settled — in the issue or a commit"。
- **调用**：无显式调用（wayfinder/ask-matt 从外部调它）。
- **决策分类/反馈**：它本身就是一种"需要原型验证"的决策类别执行器；结论以 verdict 形式回到 issue——是最接近"证据反馈"的机制，但靠人工/约定，无自动化。

### 2.11 research

来源：[skills/engineering/research/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/research/SKILL.md)

- **做什么**：起后台 agent 对**一手来源**（官方文档、源码、spec、第一方 API）调研问题，产出逐条带引用的 Markdown。
- **持久状态**：调研文件写到仓库里符合既有约定的位置。
- **调用**：无；被 wayfinder 用作 research 票的解决器（并行 subagent）。
- **决策分类/反馈**：对应"AI 可查"类别；结果进仓库文件，供主流（grill-with-docs/to-spec）消费，无结构化反馈边。

### 2.12 diagnosing-bugs

来源：[skills/engineering/diagnosing-bugs/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/diagnosing-bugs/SKILL.md)

- **做什么**：六阶段诊断纪律：①构建紧密反馈回路（核心，10 种构造方式）②复现+最小化 ③3–5 个可证伪假设排序 ④插桩（一次一个变量）⑤先回归测试后修复 ⑥清理+post-mortem。
- **持久状态**：读 `CONTEXT.md`/ADR；正确假设写进 commit/PR message；附 `scripts/hitl-loop.template.sh`（人在回路脚本模板）。
- **调用**：post-mortem 阶段——若结论是架构问题（无好 seam），"hand off to the `/improve-codebase-architecture` skill with the specifics"。
- **证据反馈**：有工程层的反馈（"If no correct seam exists, that itself is the finding"），但去向是架构 review，不是决策图。

### 2.13 improve-codebase-architecture

来源：[skills/engineering/improve-codebase-architecture/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/improve-codebase-architecture/SKILL.md)

- **做什么**：按热点（git log）圈定范围，Explore subagent 扫描"深化机会"（浅模块→深模块的重构），产出自包含 HTML 报告（Tailwind+Mermaid，写到 OS 临时目录），用户选一个后进入 grilling 循环。
- **持久状态**：HTML 报告在临时目录（不留仓库）；决策经 domain-modeling 落 `CONTEXT.md`/ADR。
- **调用**：`/codebase-design`（词汇与 design-it-twice 并行子 agent 模式）、`/grilling`、`/domain-modeling`。
- **决策/反馈**：**ADR 冲突处理是全仓库最接近"重开旧决策"的明文**：候选与 ADR 矛盾时标注 "_contradicts ADR-0007 — but worth reopening because…_"；用户以有分量的理由否决候选时，建议记 ADR "so future architecture reviews don't re-suggest it"。

### 2.14 domain-modeling

来源：[SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/domain-modeling/SKILL.md)、[CONTEXT-FORMAT.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/domain-modeling/CONTEXT-FORMAT.md)、[ADR-FORMAT.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/domain-modeling/ADR-FORMAT.md)

- **做什么**：主动构建/打磨 domain model：挑战与 glossary 冲突的用词、锐化模糊术语、编造边界场景压测、与代码交叉验证、inline 落盘。
- **持久状态（CONTEXT.md 与 ADR 的形态）**：
  - `CONTEXT.md`（仓库根；多上下文时根 `CONTEXT-MAP.md` + `src/<ctx>/CONTEXT.md`）：纯 glossary，每条术语 1–2 句定义 + `_Avoid_:` 列禁用同义词；"totally devoid of implementation details"；懒创建。
  - `docs/adr/NNNN-slug.md`：顺序编号；正文可仅 1–3 句（context + 决定 + 原因）；可选 Status frontmatter `proposed | accepted | deprecated | superseded by ADR-NNNN`、Considered Options、Consequences；创建需同时满足三条件（难逆转 / 无上下文会令人惊讶 / 有真实权衡）。
- **调用**：无（它是被调用方）。
- **决策状态/反馈**：ADR 的 `deprecated`/`superseded by` 状态是仓库中唯一的**决策生命周期**原语；但无谁触发 supersede 的流程。

### 2.15 codebase-design

来源：[skills/engineering/codebase-design/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/codebase-design/SKILL.md)（另含 DEEPENING.md、DESIGN-IT-TWICE.md）

- **做什么**：深模块设计的共享词汇与原则参考：module/interface/depth/seam/adapter/leverage/locality、deletion test、"the interface is the test surface"、"one adapter = hypothetical seam, two = real"、可测试性设计三则。
- **持久状态**：无（纯参考）；[DESIGN-IT-TWICE.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/codebase-design/DESIGN-IT-TWICE.md) 提供并行子 agent 设计多个备选接口再比较的模式。
- **调用**：无（被 tdd / improve-codebase-architecture 消费）。
- **决策相关**：无状态与反馈机制。

### 2.16 triage

来源：[skills/engineering/triage/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/triage/SKILL.md)

- **做什么**：把 issue/外部 PR 推过 triage 角色状态机：分类角色 `bug`/`enhancement` + 状态角色 `needs-triage`/`needs-info`/`ready-for-agent`/`ready-for-human`/`wontfix`；含验证诉求（复现 bug、checkout PR 跑测试）、必要时 grill、产出 agent brief。
- **持久状态**：tracker 标签 + triage notes 评论（模板含 "What we've established so far"，供跨会话恢复）；被否决的 enhancement 写入 `.out-of-scope/*.md` 知识库并链接（防止重复提议）；所有 AI 评论须带免责声明。
- **调用**：`/grilling` + `/domain-modeling`（step 4）、`/setup-matt-pocock-skills`（标签映射前置）。
- **决策分类**：有面向工单的分流（agent 可抓 / 必须人来 / 拒绝），但对象是 issue 而非决策；无重开决策机制（有 needs-info → needs-triage 的回流边）。

### 2.17 setup-matt-pocock-skills

来源：[SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/setup-matt-pocock-skills/SKILL.md) 及同目录模板（[issue-tracker-local.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/setup-matt-pocock-skills/issue-tracker-local.md) 等）

- **做什么**：每仓库跑一次的前置配置：选定 issue tracker（GitHub/GitLab/本地 markdown/其他自由文本）、triage 标签词汇、domain 文档布局（单/多上下文）。
- **持久状态（写入）**：`CLAUDE.md` 或 `AGENTS.md` 中的 `## Agent skills` 块；`docs/agents/issue-tracker.md`、`docs/agents/triage-labels.md`、`docs/agents/domain.md`。本地 tracker 约定：`.scratch/<feature>/spec.md`、`.scratch/<feature>/issues/NN-<slug>.md`、`Status:` 行、`## Comments` 追加；含专门的 **Wayfinding operations** 节（map/child/blocking/frontier/claim/resolve 的文件级协议）。
- **调用**：无。
- **决策相关**：它是所有持久化约定的源头——"决策状态存哪"这个问题在该体系里的答案 = `docs/agents/` 配置 + tracker + `.scratch/`。

### 2.18 grill-me / ask-matt（附带）

- `grill-me`（[来源](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/productivity/grill-me/SKILL.md)）：全文一行——"Run a `/grilling` session." 无状态版封装。
- `ask-matt`（[来源](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/ask-matt/SKILL.md)）：作者官方流程图（见 §3），并给出上下文卫生规则：grilling→spec→tickets 保持在**一个不断裂的上下文窗口**内；每个 `/implement` 用全新上下文；接近 "smart zone"（~120K tokens）就用 `/handoff` 换线。

---

## 3. Skill 间调度关系（显式 `/skill` 调用边）

来源：各 SKILL.md 正文 + [ask-matt 流程图](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/ask-matt/SKILL.md) + 调用约束 [.agents/invocation.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/.agents/invocation.md)（user-invoked 只能调 model-invoked）。

**作者定义的主流（idea → ship）**：`grill-with-docs` →（必要时经 `handoff` 桥接 `prototype` 往返）→ `to-spec` → `to-tickets` → 逐票 `implement`（内部驱 `tdd`、收尾 `code-review`）→ commit。

显式调用边一览：

- `grill-me` → `grilling`
- `grill-with-docs` → `grilling`、`domain-modeling`
- `wayfinder` → `grilling`、`domain-modeling`（chart 与默认解票手段）；`research`（research 票的并行 subagent）；`prototype`（prototype 票）；`setup-matt-pocock-skills`（前置）。完成后交接 → `to-spec`（ask-matt 规定，非 wayfinder 正文）
- `to-spec` → `setup-matt-pocock-skills`（前置）；消费 `CONTEXT.md`/ADR、可 inline `prototype` 片段
- `to-tickets` → `setup-matt-pocock-skills`（前置）
- `implement` → `tdd`、`code-review`
- `tdd` → `code-review`（重构归属的 review 阶段）；消费 `codebase-design` 词汇（ask-matt 注明）
- `code-review` → `setup-matt-pocock-skills`（配置缺失时）
- `triage` → `grilling`、`domain-modeling`、`setup-matt-pocock-skills`；产出的 `ready-for-agent` issue 由 `implement` 拾取（ask-matt）
- `diagnosing-bugs` → `improve-codebase-architecture`（post-mortem 交接，经人触发）
- `improve-codebase-architecture` → `codebase-design`、`grilling`、`domain-modeling`；其产出可作为新 idea 进入主流 `grill-with-docs`（ask-matt）
- `handoff` → 无显式调用，但文档内 "suggested skills" 指向后续 skill；ask-matt 规定它是跨上下文窗口的桥（grilling ↔ prototype 往返、smart zone 换线）
- `setup-matt-pocock-skills` → 无调用；被几乎所有 engineering skill 以前置条件引用
- `ask-matt` → 覆盖全部 user-invoked skill 的路由器
- `prototype`、`research`、`domain-modeling`、`codebase-design`、`grilling` → 无出边（叶子/原语）

**关键观察**：整张图是**有向无环的"编排 → 原语"结构**。所有边都从"计划/编排层"指向"执行/参考层"；**没有任何一条边从 tdd / implement / code-review 指回 wayfinder、to-spec、to-tickets 或 ADR**——即执行证据没有结构化回流通道，回流只能靠人重新发起某个 user-invoked skill。

---

## 4. 七个能力维度逐项判断

### 维度 1：广度优先展开决策空间 — ✅ 已覆盖

`wayfinder` chart 阶段第 2 步明文："Map the frontier. Grill again, **breadth-first** this time: fan out across the whole space rather than deep on any one thread, surfacing the open decisions and the first steps takeable now."（[wayfinder/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/wayfinder/SKILL.md)）。另有 fog of war / "Not yet specified" 承载"看得见但还切不 sharp"的决策域。注意 `grilling` 原语本身是逐枝深入的（"Walk down each branch of the decision tree"），广度优先只存在于 wayfinder 的编排层。

### 维度 2：问题四分类（人判断 / AI 查 / 原型验证 / AI 自主） — ✅ 已覆盖（分类轴略有差异）

- wayfinder 的四票型 + HITL/AFK 二分几乎一一对应：`grilling`(HITL)≈人做价值判断；`research`(AFK)≈AI 查（偏向仓库外一手来源）；`prototype`(HITL)＝原型验证；`task`(AFK)≈AI 自主执行。且明文禁止 agent 代替人回答 HITL 票（[wayfinder/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/wayfinder/SKILL.md)）。
- grilling 内部也有同类二分："fact 可查环境就自查，decision 归人"（[grilling/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/productivity/grilling/SKILL.md)）。
- 差异：轴是 HITL/AFK × 解决手段，不是按"决策性质"的四象限；"AI 可查代码库"这一具体类别没有专门票型（research 定位是"current working directory 之外"的知识）。判定已覆盖但非逐字对齐。

### 维度 3：推进决策前沿，不要求全决策完成才编码 — ⚠️ 部分覆盖（且设计哲学相反）

- 有的部分：`frontier` 概念是一等公民（wayfinder 的 "open, unblocked, unclaimed children — the edge of the known"；to-tickets 的 "Work the frontier"）；ticket 可并行 grab；逐会话一票。
- 缺的部分：wayfinder 明文 **plan, don't do**——"produce decisions, not deliverables"，地图完成的定义是"nothing left to decide before someone goes and does the thing"，然后经 `to-spec` 才进入构建；`grilling` 也要求"Do not act on it until I confirm we have reached a shared understanding"。即这套体系的默认模式是**决策收敛完成后才编码**，"边决策边编码"被显式排除（唯一例外是 Notes 里声明 override 的 task 票，且 task 的存在理由是"unblocking a decision, not delivering the destination"）。来源同上两条 URL。

### 维度 4：路径明确时立即 TDD 最小 vertical slice — ✅ 已覆盖

`tdd`：红→绿、"One slice at a time. One seam, one test, one minimal implementation per cycle"、反对 horizontal slicing（[tdd/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/tdd/SKILL.md)）；`implement` 薄编排直接驱动 `/tdd`（[implement/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/implement/SKILL.md)）；`to-tickets` 的 tracer-bullet ticket 即"可独立演示的 vertical slice"；ask-matt："Reach for `/tdd` on its own when you just want to build a concrete behaviour test-first without a full spec"（[ask-matt](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/ask-matt/SKILL.md)）。

### 维度 5：测试/实现/review 证据反馈回决策图、必要时重开旧决策 — ❌ 基本缺失（仅零星静态原语）

- 无任何从 `tdd`/`implement`/`code-review` 回流到 wayfinder map、spec、ticket 或 ADR 的边（§3 图无回向边）。`code-review` 的发现只报告给人（[code-review/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/code-review/SKILL.md)）。
- wayfinder 内只有对**未关闭**票据的修正："update or delete those tickets"；已关闭决策不可重开，Decisions-so-far append-only，"a closed ticket is unambiguously off the frontier"（[wayfinder/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/wayfinder/SKILL.md)）。
- 仅有的重开原语散落在别处：ADR 的 `superseded by ADR-NNNN` 状态（[ADR-FORMAT.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/domain-modeling/ADR-FORMAT.md)）、improve-codebase-architecture 的 "contradicts ADR-0007 — worth reopening" 标注（[SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/improve-codebase-architecture/SKILL.md)）、prototype 的 verdict 回写 issue、diagnosing-bugs 的 "no correct seam 本身就是发现" → 架构 review。这些都是人工触发、各自孤立的，不构成"证据→决策图→重开"的闭环。

### 维度 6：轻量、持久化的决策状态，不同 AI/会话可续推 — ✅ 已覆盖（全套最强维度）

- map/ticket 在 tracker 上天然跨会话、跨 agent；本地模式落 `.scratch/<effort>/{map.md,issues/NN-*.md}`，纯文本、带 `Type:`/`Status:`/`Blocked by:` 行，frontier 靠扫描目录即可计算（[issue-tracker-local.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/setup-matt-pocock-skills/issue-tracker-local.md)）。
- 并发协议：claim-before-work（assign 即认领）、"never resolve more than one ticket per session"、明确预期多会话并发编辑 tracker（[wayfinder/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/wayfinder/SKILL.md)）。
- 配套：`handoff` 跨上下文窗口、triage notes 的 "established so far" 恢复、`CONTEXT.md`/ADR 持久化领域决策。
- 扣分点：`handoff` 写 OS 临时目录而非工作区（[handoff/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/productivity/handoff/SKILL.md)），跨机器/跨 agent 共享需自行传递文件。

### 维度 7：面向日常编码，不是庞大 Agent OS — ✅ 已覆盖

README 开宗明义反对 GSD/BMAD/Spec-Kit 式"owning the process"，定位 "small, easy to adapt, and composable"（[README](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/README.md)）。实证：grill-with-docs 全文 3 行、implement 全文 8 行、grill-me 全文 1 行；多数 SKILL.md 在 1–8KB；无守护进程、无框架代码，全是 Markdown 纪律文件；作者自评 wayfinder 是"the most cognitively demanding flow here... save it for exactly that, never a well-scoped feature"（[ask-matt](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/ask-matt/SKILL.md)）——日常任务走 grill-with-docs → implement 的轻路径。

**汇总**：

| 维度                           | 判定                                              |
| ------------------------------ | ------------------------------------------------- |
| 1 广度优先                     | ✅ 已覆盖（wayfinder）                            |
| 2 问题四分类                   | ✅ 已覆盖（HITL/AFK × 票型，轴略异）             |
| 3 决策前沿先行、边决策边编码   | ⚠️ 部分覆盖（frontier 有，但默认 plan-then-do） |
| 4 立即 TDD vertical slice      | ✅ 已覆盖                                         |
| 5 证据反馈/重开决策            | ❌ 基本缺失（只有孤立静态原语）                   |
| 6 轻量持久决策状态、跨会话续推 | ✅ 已覆盖                                         |
| 7 轻量、非 Agent OS            | ✅ 已覆盖                                         |

---

## 5. 专项：wayfinder 的 decision ticket 离"轻量决策前沿"还差什么

**已经到位的**（[wayfinder/SKILL.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/wayfinder/SKILL.md) + [issue-tracker-local.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/setup-matt-pocock-skills/issue-tracker-local.md)）：frontier 的可计算定义（open + unblocked + unclaimed）、blocking 边（tracker 原生依赖或 `Blocked by:` 行）、认领协议、每会话一票、四类决策票 + HITL/AFK、fog（Not yet specified）与 Out of scope 的边界管理、本地纯文件落盘。这部分与"轻量决策前沿"的骨架几乎重合。

**差距（按重要度）**：

1. **已关闭决策不可重开，无决策生命周期**。解决即关闭，Decisions-so-far 只增不改；修正能力只覆盖未关闭票（update/delete）。用户要的"证据到位时重开旧决策"需要补上：`Status: resolved → reopened/superseded` 的状态机、以及 Decisions-so-far 条目的失效标注（现在只有 ADR 有 `superseded by`，ticket 层没有）。
2. **执行证据没有回流边**。tdd/code-review/implement 的输出不进 map；wayfinder 正文也没有任何"实现后发现 X，回改某决策"的指令。需要新增一类从执行层指回 map 的显式约定（例如 review 发现违反某 decision ticket 的结论时，在该票下追加 evidence comment 并重开）。
3. **plan 与 do 被制度性隔离**。wayfinder 默认"produce decisions, not deliverables"，地图收敛后才交接 `to-spec`。用户的"决策前沿与编码并行推进"需要放宽这条：允许 destination 的 Notes 里声明执行票与决策票混排（现在只有 task 票例外，且其合法性来自"解锁决策"而非"交付"）。
4. **决策与其影响物之间无可追溯链接**。map 是 index not store，决策只在票内；没有"该决策影响了哪些 spec 条目/代码区域"的反向索引，证据出现时无法定位该重开哪张票。
5. **分类轴与用户的四类不完全对齐**（次要）：research/prototype/grilling/task 是按解决手段分的；"AI 可查代码库"没有专属票型；"AI 可自主决定"没有"谁拍的板、依据是什么"的记录位（答案评论自由文本，无 decided-by/rationale 结构）。
6. **状态是约定而非工具**（轻量路线的代价）：frontier 靠人肉/agent 扫描 `Status:` 行计算，无校验、无查询命令。对"轻量"目标而言这是合理取舍，但意味着状态一致性完全依赖各会话守规矩。

---

## 附：本次阅读的全部一手来源清单

- [README.md @2ab95809](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/README.md)
- [CLAUDE.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/CLAUDE.md)、[.agents/invocation.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/.agents/invocation.md)、[.changeset/wayfinder-decision-tickets.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/.changeset/wayfinder-decision-tickets.md)
- [wayfinder](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/wayfinder/SKILL.md)、[grill-with-docs](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/grill-with-docs/SKILL.md)、[grilling](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/productivity/grilling/SKILL.md)、[grill-me](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/productivity/grill-me/SKILL.md)
- [to-spec](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/to-spec/SKILL.md)、[to-tickets](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/to-tickets/SKILL.md)、[implement](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/implement/SKILL.md)、[tdd](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/tdd/SKILL.md)、[code-review](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/code-review/SKILL.md)
- [handoff](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/productivity/handoff/SKILL.md)、[prototype](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/prototype/SKILL.md)、[research](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/research/SKILL.md)、[diagnosing-bugs](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/diagnosing-bugs/SKILL.md)
- [improve-codebase-architecture](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/improve-codebase-architecture/SKILL.md)、[domain-modeling](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/domain-modeling/SKILL.md) + [CONTEXT-FORMAT.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/domain-modeling/CONTEXT-FORMAT.md) + [ADR-FORMAT.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/domain-modeling/ADR-FORMAT.md)、[codebase-design](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/codebase-design/SKILL.md)
- [triage](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/triage/SKILL.md)、[setup-matt-pocock-skills](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/setup-matt-pocock-skills/SKILL.md) + [issue-tracker-local.md](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/setup-matt-pocock-skills/issue-tracker-local.md)、[ask-matt](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/ask-matt/SKILL.md)
- [仓库目录树（git trees API）](https://api.github.com/repos/mattpocock/skills/git/trees/2ab958093e83e0ec752e6c1c5932da465bf23e0c?recursive=1)
