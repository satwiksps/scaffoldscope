# Core concepts

## The unit of comparison

ScaffoldScope uses paired blocks. For each task and replicate, every treatment starts from the same source state and runs under the same fixed model and limits. Treatment order can be deterministically randomized inside the block.

This design removes task difficulty from the direct treatment comparison. It does not remove provider randomness, temporal drift, model-policy interactions, or contamination.

## What stays fixed

- model route and exact revision;
- task, repository state, evaluator, and constraints;
- system prompt and action protocol;
- context window and output reservation;
- turn, token, cost, timeout, and retry limits;
- token counter and observation caps;
- replicate policy and treatment order algorithm; and
- sandbox and container identity.

Anything intentionally changed belongs in the treatment definition or a separate experiment identity.

## Canonical trajectory and context view

The agent appends every message to one canonical trajectory. A context policy receives that trajectory and derives the view sent to the next model call. It does not rewrite the source trajectory.

Each decision records message and bundle IDs, token estimates, constraint availability, selected and dropped sources, summary sources, scores where applicable, and whether effective compaction occurred. Assistant actions and their tool observations are atomic bundles.

## Outcome, resource, and governance populations

ScaffoldScope keeps three questions separate:

1. **Did the task resolve?** Solve rate and paired outcome comparisons use analysis-valid episodes. Declared model and protocol-limit outcomes remain non-solves when evaluation evidence exists; infrastructure and harness errors are invalid missing cells.
2. **What resources did generation consume?** Token, cost, latency, provider, and context metrics use infrastructure-valid generation rows, even when an external evaluator is still pending.
3. **Were standing constraints available and obeyed?** Lexical availability measures representation in the active context. Behavioral adherence comes from deterministic workspace checks. Governed completion requires both task resolution and configured constraint adherence.

## Evidence instead of a leaderboard row

A run is a directory of linked evidence, not just a score. The plan defines intended cells. Each trial owns an event log, result, patch, and generated workspace. Aggregates are rebuilt from atomic trial results. Reports are derived and reproducible. Bundles omit workspaces, commit every included file by hash, and can be verified independently.

Read [Architecture](../architecture.md) for implementation boundaries and [Experiment design](../experiment-design.md) for estimands, pairing, power, and release rules.

```{toctree}
:maxdepth: 1
:hidden:

../architecture
../experiment-design
```
