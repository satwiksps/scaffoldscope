# Architecture

ScaffoldScope separates the immutable evidence of an episode from the context view shown to a model.

## Runtime path

1. `RunConfig` validates a versioned JSON config and task manifest.
2. An OS-released experiment lock excludes concurrent writers; `build_plan` expands `(task, replicate, variant)` cells and randomizes variant order inside each paired block.
3. `prepare_workspace` creates an isolated generated copy or local Git clone for the cell.
4. `CodingAgent` appends messages to a canonical `Trajectory`.
5. A `ContextPolicy` derives a `ContextView` without mutating that trajectory.
6. A provider returns one text-JSON action; a restricted local or Docker backend executes the declared tool surface.
7. The manifest-owned test command and deterministic constraints evaluate the final workspace.
8. The runner writes the trace, patch, and result atomically, then rebuilds `episodes.jsonl`.
9. The reporter analyzes complete paired blocks and resamples tasks, not flat episodes.

## Important boundaries

| Boundary | Contract |
|---|---|
| Model | Normalized messages in; content, provider identity, usage, request ID, and latency out |
| Context policy | Canonical trajectory + fixed budget in; auditable view and decision out |
| Tool | JSON arguments in; bounded observation and metadata out |
| Task | Trusted workspace, issue, constraints, and fixed evaluator command |
| Trial | One task, one variant, one paired replicate, one immutable identity hash |

Built-ins use explicit registration. Third-party policies and providers use named Python entry points: configuration never executes an arbitrary dotted import. Referenced plugins are compatibility-checked, loaded lazily, fingerprinted, and included in experiment identity.

## Atomic message bundles

Provider transcripts commonly require an assistant action and its corresponding tool observation to remain together. ScaffoldScope assigns both messages the same bundle ID. Every compaction policy selects entire bundles.

Pinned system messages are always retained. The task and standing constraints are deliberately compaction-eligible so lexical-availability measurements are not trivially 100%. A production deployment can move hard policy into an immutable system layer; a governance stress study should keep harmless constraints in the measured layer.

## Context policy behavior

`none` returns the full trajectory and raises a typed overflow before a provider call when it exceeds the input limit.

`reactive` activates at a utilization threshold. It retains pinned and recent bundles, then constructs a deterministic salient-line summary of eligible history.

`periodic` uses the same summary mechanism on fixed turn boundaries, with an emergency threshold. Once activated, it maintains a compacted view between boundaries rather than silently restoring the full history.

`selective` scores optional bundles and solves a deterministic 0/1 knapsack under the target token budget. Scores and final selections are part of the trace.

The bundled `char4-v1` token counter is an approximate, provider-independent budget unit: four UTF-8 bytes per token plus a fixed message overhead. Provider-reported usage is the preferred token-accounting source, while the provider invoice remains the billing authority. Both identities are logged so they are never confused.

## Tool surface

The model can request:

- `list_files`
- `read_file`
- `search`
- `search_symbols`
- `replace`
- `write_file`
- `run_tests`

`run_tests` accepts no model-controlled command. The trusted task manifest supplies an argument vector, executed with `shell=False`.

Each variant can declare an exact subset of these tools. The local sandbox rejects absolute and escaping paths, resolves symlink parents, caps file and observation sizes, scrubs the evaluator environment, and works on a generated copy. These controls limit mistakes but do not isolate hostile test code at the operating-system level. The Docker backend additionally locks down the operating-system boundary and records the preflight-resolved image identity.

## Provider design

`scripted` is a deterministic engine fixture. Each task contains a sequence of protocol responses.

`openai_compatible` calls `/chat/completions` with the standard library. It supports JSON mode, seeds, configurable retries, usage parsing, common cache/reasoning details, configurable prices, and provider request IDs.

All model costs currently belong to the agent category because the bundled summaries are deterministic. A future model-backed summarizer must route its calls through the same provider dispatcher and tag them `compaction`; hiding those calls would be an experimental confound.

## Why no framework dependency

The full agent loop lives in [src/scaffoldscope/agent.py](../src/scaffoldscope/agent.py). A researcher can read it without learning a framework's callback, memory, or retry semantics. Those semantics would otherwise become uncontrolled harness components.
