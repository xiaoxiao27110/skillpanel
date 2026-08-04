---
name: grill-with-docs
description: A relentless interview to sharpen a plan or design, which also creates docs (ADR's and glossary) as we go.
disable-model-invocation: true
---

Run a `/grilling` session, using the `/domain-modeling` skill.

Keep the grilling and everything that follows in **one unbroken context window** — don't compact or clear mid-flow.

When the grilling converges, branch on the size of the build:

- **Fits in one context window** → `/implement` right here, in the same context window.
- **Multi-session build** → `/to-spec` to turn the thread into a spec, then `/to-tickets` to split it into tracer-bullet tickets, each declaring its **blocking edges**. Kick off `/implement` per ticket in a fresh session, working blockers-first.

If the session approaches the **smart zone** (~120k tokens) before the work is ticketed, compact the conversation into a handoff markdown file and continue in a fresh session that references it — don't push on degraded.
