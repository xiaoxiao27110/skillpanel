
# Matt Pocock "smart zone" 概念调研

> 调研日期：2026-08-04（会话环境日期；`date` 命令调用未获批准，采用系统提供的会话起始日期）。
>
> 调研方法：全部结论基于一手来源。用 FetchURL 抓取了 aihero.dev 上 Matt Pocock 本人撰写的 AI Coding Dictionary 五个直接相关词条（smart-zone、attention-degradation、clearing、compaction、handoff，页面署名 "Matt Pocock"，词条间互相链接形成闭环）；另使用本仓库内的两份一手/准一手材料：`.agents/skills/ask-matt/SKILL.md`（mattpocock/skills 仓库 ask-matt skill 的本地副本，上游固定 commit URL 见下文引用）与既有调研报告 `research/mattpocock-skills-deep-read.md`（其结论均固定在 mattpocock/skills 的 commit `2ab958093e83e0ec752e6c1c5932da465bf23e0c` 上）。X 推文、播客、YouTube 未抓取（任务中途被指示停止广泛搜索，基于已收集材料成文）；未能支撑的点在 §6 逐条标注「未找到一手出处」。

---

## 1. 定义：smart zone 到底是什么

来源：[AI Coding Dictionary / smart-zone](https://www.aihero.dev/ai-coding-dictionary/smart-zone)（分类：Failure Modes）。

他的原话：

> "Early in a session the agent is in a 'smart zone' — sharp, focused, recall is good. As the session grows it drifts into a 'dumb zone': sloppier, forgetful, more mistakes — and more faithfulness hallucinations. Same model, same harness — just more context. The felt effect of attention degradation."

转述：会话早期 agent 处于 "smart zone"——敏锐、专注、记忆好；随着会话变长，它会漂进 "dumb zone"——更马虎、健忘、错误更多、忠实性幻觉更多。模型和 harness 都没变，只是上下文变多了。smart zone 是注意力退化（attention degradation）的"可感受效应"。

关于具体数值，词典条目说：

> "On frontier models, the dumb zone commonly begins around 125K-150K tokens — though this is debated."

转述：在前沿模型上，dumb zone 通常从 125K–150K tokens 左右开始——但他明确标注"这有争议"。注意：任务书提到的"约 120k"出自他的 ask-matt skill 而非词典条目，两处数字并不相同（见 §5）。

他还强调衰退是渐进的、没有报错和可见边界："The decline is gradual, which makes it easy to miss. There's no error message and no visible boundary"，典型征兆是"it forgets an instruction you gave twenty turns ago, repeats a mistake it had already corrected, or confidently asserts something the context contradicts"（忘了二十轮前给的指令、重复已纠正过的错误、自信地断言与上下文矛盾的内容）。而且"push through and re-explain"（硬撑并重新解释）反而会"adds more context and makes the problem worse"。

关键的一条：**smart zone 不跟随上下文窗口上限**。

> "The zones don't track the context window limit. A session can be deep in the dumb zone with most of the window still free: the limit is where the harness refuses to continue, but quality falls off long before that. Plan around the smart zone, not the window — the practical budget for a task is the tokens the agent works well within, not the tokens it can technically hold."

转述：窗口上限只是 harness 拒绝继续的位置，质量早在那之前就掉了。一个任务的实际预算应该是"agent 能工作得好的 token 数"，而不是"它技术上装得下的 token 数"。

关于"是否随模型代际变化"：词典措辞是 "On frontier models"（在前沿模型上），ask-matt 措辞是 "state-of-the-art models"，都暗示数值依附于当前一代模型而非固定常数；但他没有明确论述该数值如何随模型代际移动（见 §6）。

## 2. 机制：为什么过了这个区推理质量下降

来源：[AI Coding Dictionary / attention-degradation](https://www.aihero.dev/ai-coding-dictionary/attention-degradation)（该词条明确标注自己是 "Cause of the smart zone / dumb zone effect"）。

他的原话：

> "As a session grows, each token's attention budget is spread across more competitors. The signal on any one meaningful relationship shrinks; noise from irrelevant context crowds in. Same model, same parameters — just more mouths to feed from the same plate."

转述：会话越长，每个 token 的注意力预算被摊到越多竞争者头上；任何一段有意义关系上的信号都在缩水，无关上下文的噪音挤进来。模型和参数都没变——"同一只盘子，更多张嘴"。

外在表现："constraints it followed for an hour start slipping, it re-asks things it was told, it writes code that ignores a file it read earlier"（遵守了一小时的约束开始松动、重复问已告知的事、写代码时无视先前读过的文件）。恢复方向是减而不是加："You recover by removing context, not adding more. Re-pasting the ignored instruction adds another competitor to the same crowded window and helps only briefly."

他的术语体系：词典里 smart-zone 的前一个词条是 **Attention degradation**，再前一个是 **Attention budget**；smart-zone 的后一个词条是 **Clearing**。即他用自己造的词（attention budget / attention degradation / smart zone / dumb zone）构成因果链：注意力预算被摊薄 → 注意力退化 → 可感受到的 smart/dumb zone 效应。

与 context rot / attention dilution / "lost in the middle" 的关联：**在已收集材料中，未找到他引用 Chroma context rot 报告或 lost-in-the-middle 论文（或任何外部研究）的一手出处**。他的词典条目全部是自洽的自有术语，没有外链参考文献。概念上 "attention degradation" 与学界/业界的 context rot、attention dilution 描述的是同一现象，但这属于我们的对应，不是他自己的引证。

## 3. 操作建议：怎么留在 smart zone 里

以下全部来自他的词典词条原文，按手段分述。

**总量控制：一个会话只做一件事。**（[smart-zone](https://www.aihero.dev/ai-coding-dictionary/smart-zone)）

> "The smart zone is a budget, and unrelated work spends it. … Doing one task per session gives each task the sharpest part of the session. When a single task is bigger than one smart zone, split it: hand off or compact at a natural boundary, and let a fresh session do the next piece."

转述：smart zone 是预算，无关工作也在花它。一会话一任务，让每个任务都吃到会话最锐利的前段；单任务大过一个 smart zone 就在自然边界拆开——handoff 或 compact，让新会话做下一段。同条目还有一句总纲："Clear or compact when the session bloats; don't push through."

**Clearing（清空）**（[clearing](https://www.aihero.dev/ai-coding-dictionary/clearing)，分类：Handoffs）：结束当前会话、以空上下文开新会话，通常由用户驱动。他说 "Clearing is the cure for a polluted context"——会话积累了一切（失败尝试、错误转弯、过期工具结果、废弃计划），模型每轮都要重读，坏历史拖垮新工作。清空不会删掉本地 transcript（"Most harnesses keep session history on your computer"），但模型是无状态的，新会话对旧会话一无所知，所以 "If the session holds decisions or progress the next one will need, have the agent write a handoff artifact first"。与 compaction 对比："Clearing is the blunter tool: nothing carries over, including the junk."

**Compaction（压缩）**（[compaction](https://www.aihero.dev/ai-coding-dictionary/compaction)）："A handoff done in-memory"——旧会话历史被总结，总结作为新会话的种子。"Lossy by design: the transcript is a primary source, the summary a secondary source — detail traded for headroom."（原话概念：transcript 是一手来源，摘要是二手来源，用细节换余量。）可手动触发或由 autocompact 自动触发。两个操作要点：(a) 摘要由模型写，所以可以被提示——"Preserve the schema decisions" 这类提示能让产物更有意；(b) 时机重要——"compact at a phase boundary, after the plan is settled, not mid-task"。有些 harness 会在摘要里留指向磁盘 transcript 的 context pointer，丢失的细节可以回读原文找回。

**Handoff（交接）**（[handoff](https://www.aihero.dev/ai-coding-dictionary/handoff)）：把 agent 上下文从一个会话转移到另一个，**"no return path"**（无回程）是塑造 carry 方式的核心约束——新会话无法回头问旧会话，携带材料必须能独立成立。载体分两种（他给了对照表）：handoff artifact（落在环境里的文件；可以在任何东西依赖它之前先读和改；可跨多个会话复用）与 compaction（上下文窗口里的摘要；自动且便宜；难检查；只喂给一个后继）。坏 handoff 的可见失败是 **relitigation**："the new session re-opens decisions the old one had settled, because the carry recorded what was decided but not why"（新会话重开旧会话已定的决策，因为携带材料只记了 what 没记 why）。验收标准："Judge a handoff by what a session with zero context could do with it."

**删除无关上下文**：作为原则体现在 attention-degradation 的 "You recover by removing context, not adding more" 和 clearing 的 "removes the noise"；他没有给出比"clear and reload only what the task needs"更细的操作清单。

**子代理/并行**：handoff 词条把 "fanning out to parallel sessions" 列为做 handoff 的理由之一，但在已收集材料中他没有把"派子代理"明确列为留在 smart zone 的手段（见 §6）。

## 4. smart zone 在他的 skills 体系里的角色

来源：`.agents/skills/ask-matt/SKILL.md`（本地副本；上游一手 URL：[ask-matt/SKILL.md @ 2ab9580](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/ask-matt/SKILL.md)）；另参照 `research/mattpocock-skills-deep-read.md` §2.18、§2.1、§3。

smart zone 在体系里是**上下文卫生的硬约束 / 容量单位**，具体落点有三处：

1. **ask-matt 的 "Context hygiene" 节**（主流工作流的路由规则）：

   > "Keep steps 1–3 in **one unbroken context window** — don't compact or clear until after `/to-tickets` … Each `/implement` then starts fresh, working from the ticket.
   >
   > The limit on this is the **[smart zone](https://www.aihero.dev/ai-coding-dictionary/smart-zone)**: the window (~120k tokens on state-of-the-art models) within which the model still reasons sharply. If a session approaches it before `/to-tickets`, don't push on degraded — `/handoff` and continue in a fresh thread."
   >

   转述：grilling → spec → tickets 必须留在**一个不断裂的上下文窗口**里（`/to-tickets` 之前不要 compact/clear），让计划阶段的思考连续累积；之后每个 `/implement` 用全新上下文、只从 ticket 出发。这个"不断裂"的上限就是 smart zone——逼近它就 `/handoff` 换新线程，不要带伤硬撑。注意这里直接链到了词典的 smart-zone 条目，两处文本是同一套话语。
2. **`/handoff` 是跨上下文窗口的桥**：ask-matt 规定 handoff 用于 grilling ↔ prototype 往返和 smart zone 换线（deep-read §3 的调用边总结：`handoff` 无显式出边，但"它是跨上下文窗口的桥"）。即体系把"换上下文"做成了一等公民的 user-invoked skill，而不是临时救急动作。
3. **smart zone 是工作切分的度量单位**：wayfinder 把 decision ticket 的大小定为"一个 100K token 会话"（deep-read §2.1，一手来源：[wayfinder/SKILL.md @ 2ab9580](https://github.com/mattpocock/skills/blob/2ab958093e83e0ec752e6c1c5932da465bf23e0c/skills/engineering/wayfinder/SKILL.md)）；"每会话最多解决一张票"。这与词典里"单任务大过一个 smart zone 就拆开"互为表里：**票的大小 ≈ 一个可用上下文窗口的容量**。本仓库 `test.md` 的流程图也把 "上下文接近 Smart Zone 上限" 画为触发 `/handoff` 的人工判断节点，说明该概念已进入本项目自己的工作流建模。

## 5. 不同场合说法的演化 / 不一致

已收集材料中可确认的不一致只有一处，但很说明问题：

- ask-matt SKILL.md："~120k tokens on state-of-the-art models"。
- 词典 smart-zone 条目："around 125K-150K tokens — though this is debated"。

数字从 ~120k 变成 125K–150K 的区间，并且词典版加了"有争议"的明示对冲。词典条目无发布时间、skills 仓库固定在 commit `2ab9580`（该 commit 日期未在已收集材料中核实），**无法从现有材料建立先后时间线**，因此只能陈述两处说法并存、口径一紧一松，不能断言演化方向。

其余场合（X、播客、YouTube/课程）的说法未采集，无法比对（见 §6）。

## 6. 未找到一手出处的点

- **他是否引用过 Chroma context rot 报告、lost-in-the-middle 论文等外部研究**：五个词典词条与 ask-matt SKILL.md 中均无外部文献引用。未找到一手出处。
- **X 账号 @mattpocockuk 上关于 smart zone 的推文**：未采集（x.com 常有登录墙，且任务中途被指示停止广泛搜索）。未找到一手出处。
- **播客、YouTube 视频或课程文字中的相关表述**：未采集。未找到一手出处。
- **smart zone 数值随模型代际如何移动的明确论述**：只有 "On frontier models" / "state-of-the-art models" 的依附性措辞，没有"下一代模型会扩大/不改变该区间"的明示说法。未找到一手出处。
- **子代理作为留区手段**：handoff 词条提到 "fanning out to parallel sessions" 是 handoff 的理由之一，但没有把"用子代理隔离上下文"明确列为留在 smart zone 的建议。未找到（针对 smart zone 的）一手出处。
- **两处数字（120k vs 125K–150K）的时间线与演化方向**：词典条目无日期，无法排序。无法确证。
