# AGENTS.md

## 技能地图(3 入口 + 8 内部)

用户入口只有三个:
- `/grill-with-docs` — 新功能/需求变更:拷问澄清,顺手落 CONTEXT.md 术语与 ADR
- `/diagnosing-bugs` — Bug、异常、回退
- `/improve-codebase-architecture` — 代码膨胀、架构治理

内部能力(由入口或本文件触发,用户一般不直接调用):grilling、domain-modeling、
codebase-design、to-spec、to-tickets、implement、tdd、code-review。
(/tdd、/code-review 直接调用也合法。)

## 拷问收敛后的路由

- 一个上下文窗口能装下 → 当前会话直接按 implement 技能实施(内部驱动 tdd,收尾 code-review)。
- 真正跨会话的大工程 → `/to-spec` 把会话落成 spec(`docs/specs/<feature>.md`),再 `/to-tickets` 拆成带阻塞关系的 ticket(本地文件,`.scratch/<feature>/issues/`),每个 ticket 开新会话 `/implement`,做完一个清一次上下文。
- 接近聪明区(~120k tokens)前:浓缩成 handoff 文件(如 HANDOFF.md)并开新会话引用它,不硬撑。
