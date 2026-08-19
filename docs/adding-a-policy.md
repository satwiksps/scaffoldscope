# Adding a context policy

Use the public entry-point API for a separately distributed policy; start with [extensions.md](extensions.md) and the runnable [pinned-tail example](https://github.com/satwiksps/scaffoldscope/tree/main/examples/plugins/pinned-tail). Contribute a built-in only when its semantics belong in the long-term core protocol.

## Contract

A policy factory returns a `ContextPolicy`. Its `prepare` method receives:

- the append-only canonical `Trajectory`;
- a `ContextBudget` with the estimated input limit;
- machine-readable standing constraints; and
- the current turn.

It returns a `ContextView` whose `ContextDecision` identifies retained and dropped canonical messages, summary sources, estimated tokens, mechanism metadata, and lexical constraint availability.

## Invariants

1. Never mutate or delete canonical messages.
2. Never retain only half of an assistant-action/tool-result bundle.
3. Preserve pinned messages.
4. Raise `ContextOverflowError` when mandatory content cannot fit.
5. Be deterministic for fixed inputs unless stochasticity is seeded and recorded.
6. Account for every model-backed summary call, including tokens, retries, latency, and cost.
7. Never read evaluator outcomes or gold patches.
8. Never alter tools, prompt, retry behavior, or budgets outside the declared treatment.

## Tests required

- Below-trigger identity and exact trigger boundary.
- Mandatory-content overflow.
- Atomic bundle selection.
- Stable output for repeated fixed inputs.
- Budget accounting after message overhead.
- Constraint availability under pressure.
- Complete retained/dropped/source identity.
- Factory option validation and implementation provenance.

Run `scaffoldscope plugins --check` after installing the package. A policy PR should explain its trigger, information invariants, complexity, cache implications, and failure modes. A benchmark-result contribution is separate and must include the complete declared matrix, not only winning cells.
