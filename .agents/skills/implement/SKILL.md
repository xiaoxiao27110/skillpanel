---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

Implement the work described by the user in the spec or tickets.

## Orchestration — working from a ticket directory

When the tickets live in a directory (e.g. `.scratch/<feature>/issues/`), act as the **orchestrator**, not the implementer. Take tickets in dependency order — never start one whose "Blocked by" is unfinished — and for each:

1. **Delegate** it to a sub-agent, one at a time (serial — parallel tickets in the same working tree invite conflicts). Put the ticket's full text and the constraints it must respect into the delegation prompt; do not rely on the sub-agent inheriting your skills or configuration.
2. **Verify** the returned work yourself: acceptance criteria met, tests green, typecheck clean. Send it back or fix forward before moving on — don't accept on trust.
3. **Commit**, then take the next ticket.

When every ticket is done, use /code-review across the whole diff.

## Direct implementation

Working from a spec or a bare description, implement it yourself. Use /tdd where possible, at pre-agreed seams. Run typechecking regularly, single test files regularly, and the full test suite once at the end. Once done, use /code-review to review the work. Commit your work to the current branch.
