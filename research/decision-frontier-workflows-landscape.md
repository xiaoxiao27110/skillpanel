# 决策前沿（Decision Frontier）式 AI 编码工作流生态调查

- 调查日期：2026-07-29
- 方法：以一手来源为主（GitHub 仓库 README、官方文档站、GitHub REST API）；二手来源单独标注。Star/活跃度数据来自 GitHub API，快照时间 2026-07-29。
- 目标范式（用户描述）：广度优先展开决策空间 → 按"人做价值判断 / AI 查代码库 / 原型验证 / AI 自主决定"分类 → 人机共同推进"决策前沿" → 路径明确就立即 TDD 最小 vertical slice → 测试/review 证据反馈回决策图并重开旧决策 → 轻量持久化决策状态跨会话续推。用户明确不喜欢 Superpowers / GSD 式笨重多阶段、"AI 生成大方案让人审批"的模式。

---

## 1. Trellis 专节

### 1.1 身份与同名消歧

AI 编码语境下的 Trellis 是 **[mindfold-ai/Trellis](https://github.com/mindfold-ai/Trellis)**（文档站 [docs.trytrellis.app](https://docs.trytrellis.app/)），自我定位 "The best agent harness"——"面向 AI 编码的开箱即用工程框架"。注意与以下同名项目区分，均不相关：

- `roots/trellis`：WordPress LEMP 部署工具（老项目）。
- 各类名为 Trellis 的 BI/数据看板项目。

### 1.2 基本事实（GitHub API，2026-07-29）

- 创建：2026-01-26；**stars ≈ 13.3k**，forks 740，open issues 23；**AGPL-3.0**；主语言 TypeScript。
- 活跃度极高：最近 push 即调查当天（2026-07-29）。背靠 Mindfold 团队，npm 包 `@mindfoldhq/trellis`，支持 20 个 AI 编码平台（Claude Code、Cursor、Codex、OpenCode 等）。
- 仓库自身 dogfood：根目录即有 `.trellis/` 目录。

### 1.3 核心理念

官方文档的说法：AI 助手"每次会话从零开始"，Trellis **不依赖记忆，而是每次会话新鲜注入上下文**——把规范、任务、会话历史持久化进仓库，让任何编码 agent 按你的工程标准工作（[概念总览](https://docs.trytrellis.app/concepts/overview)、[README](https://github.com/mindfold-ai/Trellis)）。即它的核心命题是**上下文工程/规范注入**，而不是决策管理。

### 1.4 状态文件形态

全部落在仓库内的 `.trellis/`（[概念总览](https://docs.trytrellis.app/concepts/overview)）：

```
.trellis/
├── spec/            # 分层规范 markdown（按 package × layer 组织 + guides/）
├── tasks/<task>/    # 每个任务一个目录
│   ├── task.json    #   元数据、状态、分支
│   ├── prd.md       #   需求文档
│   ├── implement.jsonl  # implement agent 应读的文件/规范清单
│   └── check.jsonl      # check agent 应审查的清单
└── workspace/       # 按开发者分的会话日志（index.md + journal-N.md）
```

另有 `config.yaml` 与（据二手拆解）一个约 708 行的 `workflow.md` 作为运行时状态机定义；该二手拆解称其内部是 Plan/Execute/Finish 三相状态机，与 README 对外宣传的"四阶段循环"措辞不同——**此点未从一手来源验证**。

概念清单（[文档首页](https://docs.trytrellis.app/)）：Spec / Task / Workspace / Skill（`brainstorm`、`before-dev`、`check`、`update-spec`、`break-loop`）/ Sub-agent（`trellis-research`、`trellis-implement`、`trellis-check`）/ Command（`finish-work`、`continue`）/ Hook。

### 1.5 工作流与证据反馈

README 描述的 4 阶段循环（[README](https://github.com/mindfold-ai/Trellis)）：

1. **Plan** — `trellis-brainstorm` 一次只问一个问题，逐步澄清需求写出 `prd.md`；研究重的条目交给 `trellis-research` 子代理；产出按任务精选的 spec/research 引用清单（implement.jsonl / check.jsonl）。
2. **Implement** — `trellis-implement` 子代理带着注入的精选上下文写码，不提交 git。
3. **Verify** — `trellis-check` 子代理对照 spec 审查 diff，并跑 lint、类型检查和测试，能自修则自修。
4. **Finish** — 终检后 `trellis-update-spec` 把新学到的经验**回写进 `.trellis/spec/`**，"让下一次会话更聪明"。

评估：

- **证据反馈：有，且是两个层面的。** 任务内是 check 子代理的 lint/type/test + diff 审查（执行证据）；跨任务是 update-spec 把经验沉淀回规范库（制度性反馈）。这在所有被调查项目里是少有的"回写"机制。
- **是否以决策树/渐进收敛/决策重开为核心：否。** Trellis 的一等公民是 **任务（task）+ 规范（spec）**，不是决策。brainstorm 的"一次一个问题"是需求澄清的收敛式对话，最接近用户的"渐进收敛"，但它收敛的是 PRD，不是一棵可回退的决策树；证据反馈回写的是规范条目，不回链到任何"决策节点"，也没有"重开旧决策"的语义。
- **阶段形态：** 固定的任务级循环（Plan→Implement→Verify→Finish），但粒度是单个任务而非整个项目；有 workflow gate（研究发生在 PRD 之后、实现发生在任务激活之后）。比 Superpowers 轻，但不是"可跳跃的决策图"。
- **跨会话续推：有。** workspace journal 按人记录每次会话，新会话注入历史。

### 1.6 与用户理念的契合点与根本差异

- 契合：markdown/JSONL 持久化进仓库、跨会话续推、证据回写 spec、对话式渐进澄清需求、子代理隔离上下文。
- 根本差异：用户要的是"**决策**的一等公民化 + 决策图 + 证据驱动重开"，Trellis 做的是"**任务**的一等公民化 + 规范注入 + 规范沉淀"。Trellis 里没有任何东西对应"决策分类（人判断/AI查证/原型/AI自主）"或"决策前沿"。

---

## 2. 同类项目逐个分析

### 2.1 GitHub Spec Kit — [github/spec-kit](https://github.com/github/spec-kit)

- 数据：stars ≈ 124k，forks 11.1k，2025-08 创建，MIT，调查当天仍有 push（极其活跃）。30+ agent 集成。
- 核心抽象：**spec → plan → tasks → implement 的文档流水线**。`/speckit.constitution`（治理原则）→ `/speckit.specify` → （可选 `/speckit.clarify` 澄清欠规范区域）→ `/speckit.plan` → `/speckit.tasks` → （可选 `/speckit.analyze` 跨产物一致性）→ `/speckit.implement`；另有 `/speckit.converge`：对照 spec/plan/tasks 评估代码库，把剩余工作追加为新任务。
- 状态形态：`.specify/`（模板/扩展/预设）+ `specs/` 下的 spec.md、plan.md、tasks.md 等 markdown。
- 证据反馈：`analyze` 做产物间一致性检查，`converge` 做"代码 vs 规约"差距收敛——是最接近"证据反馈"的内置命令，但反馈目标是任务清单，不是决策。
- 阶段：**固定流水线**（clarify/analyze 可选但主线顺序固定），官方自承 multi-step refinement；可用 extensions/presets/bundles 深度定制。
- 契合/差异：持久化意图、可审计，与用户理念同向；但它是"先写全规约再实现"的瀑布式细化，没有决策分类、没有重开机制。

### 2.2 OpenSpec — [Fission-AI/OpenSpec](https://github.com/Fission-AI/OpenSpec)

- 数据：stars ≈ 63k，forks 4.4k，2025-08 创建，MIT，调查当天有 push。
- 核心抽象：**change（变更提案）文件夹**。`openspec/changes/<id>/` 内含 `proposal.md`（为什么/改什么）、`specs/`（增量需求+场景）、`design.md`、`tasks.md`；`/opsx:propose` → `/opsx:apply`（实现）→ `/opsx:archive`（归档并把增量 spec 合并回活动规范库）。扩展命令：`/opsx:continue`、`/opsx:ff`、`/opsx:verify`、`/opsx:onboard`。
- 状态形态：`openspec/` 目录纯 markdown；活动规范 + 变更提案 + 归档三层。
- 证据反馈：`/opsx:verify`；archive 动作本身就是"实现完成后回写规范"的循环（living spec）。
- 阶段：**明确不设固定 phase gate**——官方哲学原文 "fluid not rigid, iterative not waterfall"，"update any artifact anytime, no rigid phase gates"，并公开对标 Spec Kit"太重、阶段门太死"。为棕地（brownfield）设计。
- 契合/差异：**气质上与用户理念最接近**（轻、可跳跃、随时改任何产物、归档回写）；但一等公民仍是"变更/规约"，没有决策分类、没有证据→重开的显式语义。

### 2.3 BMAD-METHOD — [bmad-code-org/BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD)

- 数据：stars ≈ 51k，forks 5.9k，2025-04 创建，调查当天有 push（README 声明 MIT + 商标条款）。
- 核心抽象：**敏捷团队角色扮演**。12+ 专职 agent 人格（Analyst、PM、Architect、Scrum Master、Dev、QA/Test Architect、UX…），34+ 工作流，scale-adaptive（按项目复杂度自动调节规划深度）；PRD + 架构文档 → story 文件驱动 SM→Dev→QA 循环。
- 状态形态：项目文档（PRD、architecture、story 文件）+ 安装配置。
- 证据反馈：QA/Test Architect 把关、风险驱动测试策略（TEA 模块）——评审门禁式，非证据回流。
- 阶段：结构化工作流序列 + 人工交接点，重流程、`bmad-help` 引导下一步；比 Superpowers 更"组织化"。
- 契合/差异：与用户理念基本相反（用户反感"大方案审批"，BMAD 正是完整组织流程的 AI 复刻）；仅"scale-adaptive 按复杂度调节深度"值得借鉴。

### 2.4 Taskmaster — [eyaltoledano/claude-task-master](https://github.com/eyaltoledano/claude-task-master)

- 数据：stars ≈ 28k，forks 2.6k，2025-03 创建，**最后 push 2026-04-28（近 3 个月未动，注意活力下降）**；MIT + Commons Clause（不得售卖/托管竞争产品）。
- 核心抽象：**PRD → 带依赖关系的任务图**。MCP server（36 个工具，可按 token 预算分档加载）或 CLI：`parse-prd` 生成任务、`expand` 拆分、`next` 按依赖找下一任务、tags/workstreams、复杂度分析、`research` 注入外部新信息。
- 状态形态：`.taskmaster/` 目录，任务是**结构化 JSON**（tasks.json）而非 markdown。
- 证据反馈：无内建验证回路；任务状态由人/AI 显式设置。
- 阶段：无固定流水线——本质是任务 DAG 遍历，这点最自由；但也没有"为什么"的决策层。
- 契合/差异：任务依赖图 + 持久化 JSON 状态与用户理念局部同构；缺证据反馈、缺决策语义。

### 2.5 GSD（Get Shit Done）— 旧 [gsd-build/get-shit-done](https://github.com/gsd-build/get-shit-done) → 新 [open-gsd/gsd-core](https://github.com/open-gsd/gsd-core)

- 数据：旧仓库 stars ≈ 64.8k，2025-12 创建，MIT，**2026-06-26 归档（只读）**，README 置顶声明迁移至 open-gsd/gsd-core。新仓库 star 数**未验证**（API 调用被中止）。
- 核心抽象：**里程碑级五步相位循环 + 上下文工程**。每个 phase 重复：Discuss（**规划前先捕获实现决策**）→ Plan（研究、分解、验证计划能装进新鲜上下文）→ Execute（并行 wave，每个执行器干净 200k 上下文）→ Verify（走查产出、诊断修复后才算完成）→ Ship（建 PR、归档 phase、进入下一个）。主打解决 context rot：重活全在新鲜上下文的子代理里跑。
- 状态形态：`STATE.md`、`CONTEXT.md` 等结构化产物跨会话存续（README 确认）；早期版本另有 PROJECT.md / REQUIREMENTS.md / ROADMAP.md / PLAN.md（二手来源，新版未逐一验证）。
- 证据反馈：**Verify 相位是硬门禁**——"走查已构建物、先生成修复计划，再宣布完成"，是被调查项目中最强调执行证据的之一。
- 阶段：固定的五相循环（per milestone），不可跳跃；相位粒度比 Superpowers 的项目级瀑布细，但仍是固定流水线。
- 契合/差异：Discuss 相位"先记录决策再规划"是唯一显式提到 decision 捕获的环节，STATE.md 跨会话续推也契合；但整体仍是用户明确不喜欢的重流程、多阶段、子代理大军模式。

### 2.6 Superpowers（对照组）— [obra/superpowers](https://github.com/obra/superpowers)

- 数据：stars ≈ 263k（本次调查中最高），forks 23.5k，2025-10 创建，MIT，2026-07-28 有 push。
- 核心抽象：**技能库 + 强制方法论**。SessionStart 钩子加载引导技能，技能触发即强制使用。
- 固定流水线（README "The Basic Workflow"）：brainstorming（苏格拉底式提问，设计分块呈现等签字）→ git worktrees → writing-plans（2–5 分钟粒度的任务，含精确文件路径与完整代码）→ subagent-driven-development（每任务新子代理 + 两阶段评审：先规约合规后代码质量）→ 强制 TDD（RED-GREEN-REFACTOR，删除先于测试写的代码）→ 任务间 code review（严重问题阻断）→ finishing-a-development-branch。
- 状态形态：设计文档 + 计划文档（markdown，随分支生命周期）；方法论载体是技能而非状态文件。
- 证据反馈：强（TDD + 双阶段评审 + verification-before-completion），哲学明写 "Evidence over claims"。
- 阶段：**固定管线 + 人工批准门**，正是用户反感的原型：设计一次性成型、人签字、然后长时间自主执行。
- 契合/差异：TDD 纪律与证据哲学契合用户的 vertical-slice 环节；但其"先完整设计再大计划"与用户"广度优先展开决策空间、渐进收敛"正相反。

### 2.7 Agent OS（对照组）— [buildermethods/agent-os](https://github.com/buildermethods/agent-os)

- 数据：stars ≈ 5.1k，forks 813，2025-07 创建，MIT，最后 push 2026-05-05。
- 核心抽象（v3 README）：**规范注入层**——Discover Standards（从代码库提取惯例）/ Deploy Standards（按构建内容智能注入）/ Shape Spec（增强各工具的 Plan Mode）/ Index Standards。二手来源称 v3 砍掉了 spec 写作、任务分解、编排和子代理，收缩为"规范 + 塑形"工具（未从一手迁移文档验证，但与 README 一致）。
- 状态形态：用户级 `~/.agent-os/` + 项目级 `.agent-os/` markdown 规范 + index.yml。
- 证据反馈：无。阶段：无固定流水线（它是注入层不是工作流）。
- 契合/差异：轻、与任意 plan 工具叠加的思路契合；但它不管理决策也不管理任务，只是上下文供给。

### 2.8 ai-dev-tasks — [snarktank/ai-dev-tasks](https://github.com/snarktank/ai-dev-tasks)

- 数据：stars ≈ 7.8k，forks 1.7k，2025-04 创建，Apache-2.0，**最后 push 2025-11-05（基本停更）**。
- 核心抽象：**三个 markdown 文件的最小循环**：`create-prd.md` → `generate-tasks.md` → `process-task-list.mdc`；一次只做一个子任务，每步等人工确认。
- 状态形态：`/tasks/prd-*.md` + 任务清单 markdown 复选框。
- 证据反馈：无机制，全靠人逐步 review。阶段：固定线性三步，但轻到极致。
- 契合/差异：证明了"纯 markdown 提示词文件"即可承载工作流（用户理念的轻量下限）；此外无决策维度。

### 2.9 计划外发现（与"决策状态/决策记录"直接相关）

- **Beads — [steveyegge/beads](https://github.com/steveyegge/beads)**：git 支撑的分布式**图结构 issue tracker**，专为 AI agent 设计（Go，MIT，2025-10 创建；stars 未验证，2025-12 第三方快照约 6.5k）。用**依赖感知的图 + JSONL 同步**替代"杂乱的 markdown 计划"，为长程任务提供跨会话持久结构化记忆（[pkg.go.dev 文档](https://pkg.go.dev/github.com/steveyegge/beads)）。它不是决策工具，但"**轻量、git 原生、图结构、agent 可读写**"的状态底座与用户的"轻量持久化决策状态"需求高度同构，是最接近的可复用基础设施。
- **ADR 一脉**：经典 Architecture Decision Record（[GitHub 官方博客：Why Write ADRs](https://github.blog/engineering/architecture-optimization/why-write-adrs/)）本就以"决策日志"为一等概念；近期出现面向 AI 的变体，如 [Agent Decision Records（AgDR）](https://me2resh.com/blog/agent-decision-records)（ADR 的轻量扩展，记录 AI 辅助下的技术决策；配套仓库未验证）及 GitHub [decision-log 话题](https://github.com/topics/decision-log)下的"捕获决策的 agent skill + 浏览仪表盘"类项目（未深入验证）。方向对口但均为被动文档化，没有与工作流/证据回路整合。

---

## 3. 对照表

数据快照 2026-07-29（GitHub API）。"未验证"指一手抓取被中止、仅有二手或未及验证的信息。

| 项目 | Stars | 核心抽象 | 状态形态 | 证据反馈 | 阶段固定与否 |
|---|---|---|---|---|---|
| [Trellis](https://github.com/mindfold-ai/Trellis) | 13.3k | 任务（PRD+上下文清单）+ 分层规范注入 | `.trellis/`：spec/ md、tasks/（task.json+prd.md+jsonl）、workspace 日志 | **有**：check 子代理跑 lint/type/test 审 diff；update-spec 把经验回写规范库 | 任务级固定 4 相循环，有 gate |
| [Spec Kit](https://github.com/github/spec-kit) | 124k | spec→plan→tasks 文档流水线 | `.specify/` + specs/ md | 弱-中：analyze 一致性、converge 差距→追加任务 | **固定流水线**（可选步骤但顺序固定） |
| [OpenSpec](https://github.com/Fission-AI/OpenSpec) | 63k | 变更提案文件夹（proposal+delta specs+design+tasks） | `openspec/` md，活动规范+变更+归档三层 | 中：verify 命令；archive 把增量回写 living spec | **明确无 phase gate，随时改任何产物** |
| [BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD) | 51k | 敏捷团队多角色 + story 驱动 | PRD/架构/story 文档 | QA/TEA 评审门禁 | 重固定流程（scale-adaptive 深度） |
| [Taskmaster](https://github.com/eyaltoledano/claude-task-master) | 28k | PRD→依赖任务图（MCP/CLI） | `.taskmaster/` **JSON** 任务库 | 无 | 无流水线（DAG 遍历），但无验证环 |
| [GSD Core](https://github.com/open-gsd/gsd-core) | 64.8k（旧仓，已归档） | 里程碑五相循环 + 新鲜上下文子代理 | STATE.md / CONTEXT.md 等 md | **强**：Verify 相位走查+修复计划才算完成 | 固定五相循环（per milestone） |
| [Superpowers](https://github.com/obra/superpowers) | 263k | 技能库+强制方法论 | 设计/计划 md（随分支） | **强**：强制 TDD + 双阶段评审 | 固定管线+人工批准门（最重的对照） |
| [Agent OS](https://github.com/buildermethods/agent-os) | 5.1k | 规范发现/注入/索引 | `.agent-os/` md + index.yml | 无 | 无（注入层非工作流） |
| [ai-dev-tasks](https://github.com/snarktank/ai-dev-tasks) | 7.8k | 3 个 md 提示词的最小 PRD→任务循环 | `/tasks/` md 复选框 | 无（人工逐步确认） | 固定线性三步，极轻 |
| [Beads](https://github.com/steveyegge/beads) | 未验证 | agent 可读的**依赖图 issue tracker** | git 内 JSONL 图数据库 | 无（状态底座） | 无（基础设施非工作流） |

---

## 4. 结论

### 4.1 用户范式是否已有现成实现？

**没有完整实现。** 生态里所有活跃项目的一等公民都是 **spec、任务或角色**，没有一个是"**决策**"。把决策空间显式建模为图、给决策分类（人判断 / AI 查证 / 原型验证 / AI 自主）、让测试与 review 证据回链到决策节点并触发重开——这套语义在被调查项目中均不存在。

### 4.2 最接近的是谁？

按用户理念的三个关键维度分别看，没有单一赢家：

- **轻量 + 无固定阶段 + 回写**：**OpenSpec** 气质最合——官方明言 "fluid not rigid / no rigid phase gates"，任何产物随时可改，archive 把实现后的增量回写 living spec。但它管理的是变更，不是决策。
- **证据反馈回路**：**Trellis** 最完整——check 子代理用 lint/type/test 做执行证据，update-spec 把经验沉淀回规范库；且 workspace journal 原生支持跨会话续推。GSD 的 Verify 相位证据门禁更硬，但整体是用户反感的重流程。
- **决策/状态持久化底座**：**Beads**（git 原生依赖图 + JSONL）是"轻量持久化图状态"的最佳现成基础设施；ADR/AgDR 一脉则提供了"决策记录"的文档范式，但都未与证据回路整合。

若必须选一个"精神近邻"：OpenSpec（工作流哲学）+ Trellis（证据回写机制）各占一半。

### 4.3 没人做的缺口（用户范式的机会点）

1. **决策作为一等公民**：带类型分类（人价值判断 / AI 查代码 / 原型验证 / AI 自主决定）的决策节点模型——现有工具里只有 GSD 的 Discuss 相位和 ADR 文档沾边，均无类型化与可操作性。
2. **显式决策图 + 重开语义**：广度优先展开决策空间、追踪"决策前沿"、允许证据驱动地 reopen 旧决策。现有工具的反馈最多到"spec 回写"或"任务状态变更"，**没有任何工具把测试/review 证据链接回具体决策节点**。
3. **证据→决策的回路**：Trellis/OpenSpec 的"回写"对象是规范库（约定层面），不是决策（选择层面）。"这个选择被哪条测试证据支持/推翻"这一关联无人建模。
4. **轻量与图结构的结合**：轻的工具（ai-dev-tasks、Agent OS）都是线性或注入式的；有图结构的（Taskmaster 的任务 DAG、Beads 的依赖图）都没有决策语义。用户的"决策前沿"恰好落在这个空档。
5. **实践路径提示**：若自研，Beads 式 git+JSONL 图底座 + OpenSpec 式 markdown 产物 + Trellis 式 verify 钩子，是可拼接出的最近路线；Superpowers/BMAD/GSD 的重流程路线已被用户明确排除，也与本次调查的高 star 集中度（Superpowers 263k、Spec Kit 124k）形成有趣张力——**主流生态正向"更重编排"演化，决策前沿范式确实是空位**。

---

## 附：数据口径与未验证项

- Stars/forks/时间戳：GitHub REST API，2026-07-29 快照。
- 未验证项：Trellis `workflow.md` 内部状态机细节（仅二手拆解）；open-gsd/gsd-core 与 steveyegge/beads 的精确 star 数（API 抓取被中止）；GSD 早期版本的完整文件清单（二手）；AgDR 是否有配套开源仓库；decision-log 话题下项目的成熟度。
- 二手来源已在正文随文标注；其余结论均出自各仓库 README 或官方文档站。
