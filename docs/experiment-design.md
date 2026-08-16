# Experiment-design contract

ScaffoldScope automates accounting; it cannot turn a weak design into strong evidence. Freeze the protocol before buying a matrix.

## Pairing and order

The paired block is `(task_id, replicate)`. Every context variant should run exactly once in every block from the same base repository state.

Call repeated values **replicates** unless the provider documents and honors a seed. Three stochastic replicates reduce within-task noise; the generalization sample size is still the number of tasks.

ScaffoldScope hashes each episode's sequence of model responses. A high duplicate-trajectory rate is evidence that nominal replicates added little stochastic information; report it rather than counting identical runs as extra support.

Variant order is shuffled deterministically within each block using SHA-256-derived randomness. Run blocks near each other in time and hold concurrency fixed to reduce provider drift and load-related latency confounds.

## Freeze these fields

- Exact model revision and provider route.
- Sampling and reasoning settings.
- System prompt, action protocol, and tool descriptions.
- Context window, output reserve, token counter, and observation caps.
- Turn, token, cost, timeout, and retry budgets.
- Task manifest, base commits, setup images, and evaluator version.
- Pricing snapshot and cache behavior.
- Strategy implementation commit and config.

A model alias, mutable container tag, changed retry count, or altered truncation limit creates a different experiment.

## Primary analysis

For task `i`, strategy `s`, and replicate `r`, let `y_isr` be 1 when the evaluator resolves the task and 0 otherwise.

ScaffoldScope first averages replicates inside each task, then averages tasks. It reports strategy-minus-baseline effects over common paired blocks. Infrastructure-invalid episodes are missing cells; context overflow, turn exhaustion, timeout, and cost exhaustion are solve failures.

The primary interval is a task-cluster bootstrap:

1. Keep all replicates and strategies for one task together.
2. Sample task blocks with replacement.
3. Recompute solve rates and paired deltas.
4. Repeat the configured number of times.
5. Return the 2.5th and 97.5th percentiles.

For a curated panel, call this a **task-panel resampling interval**. It measures sensitivity to task mix; it is not a formal confidence interval for all future repositories.

The report also includes wins/losses/ties, a paired sign-flip diagnostic, a prospective minimum detectable effect (MDE) under a declared discordance assumption, an empirical standard-error diagnostic for panels of at least 10 tasks, and a declared smallest effect of practical interest (SESOI). Effect sizes and intervals are primary; a p-value is not a substitute. Task-bootstrap intervals are withheld for scripted runs and panels below 10 tasks.

## Power reality

Small panels only detect large harness effects. An approximate paired-binary sample size is

```text
N is approximately (z_critical + z_power)^2 * discordance / delta^2
```

With 50 tasks and 20% discordance, the 80%-power minimum detectable effect is roughly 18 percentage points. With 40% discordance it is roughly 25 points. A 2 to 3 point effect is not identifiable from such a panel, regardless of how polished the table looks.

Choose a deliberately non-saturated model and a task panel that actually creates context pressure. If fewer than half of non-control episodes compact, the study mostly measures policies while they are inactive.

## Resource estimands

Include failures in unconditional resource metrics. A policy that spends many tokens and fails is expensive.

Record and report:

- Uncached input, cache-read, cache-write, output, and reasoning tokens.
- Configured-price cost estimates with explicit provider-versus-local usage provenance.
- End-to-end, model, tool, and evaluator duration.
- Peak active context and peak canonical history.
- Compaction count, tokens before/after, summary tokens, and source IDs.
- Status-specific termination rates.

Cost per solve and successful-episode-only cost are secondary descriptive ratios because they condition on the outcome.

## Governance metrics

Keep representation and behavior separate.

**Lexical constraint availability** is the fraction of post-compaction checks where the active context contains a required constraint ID or exact normalized text. It does not establish semantic understanding or compliance.

**Behavioral adherence** is the fraction of deterministic constraint checks that pass in the resulting workspace.

**Governed completion** requires the task to resolve and every configured behavioral constraint to pass.

For a dedicated governance panel, use harmless delayed probes and canaries: preserve a file, avoid inserting a marker, or ignore a late instruction embedded in tool output. Report probe reach. A strategy that crashes before the probe is not proven safe.

## Pre-launch decision rule

Declare one primary comparison and the SESOI before the matrix. ScaffoldScope emits confirmatory labels only for that contrast, with at least 20 tasks and at least 98% complete paired blocks. Other contrasts remain exploratory. Classify the paired interval as:

- `meaningful_gain` when its lower bound reaches the positive SESOI.
- `meaningful_loss` when its upper bound reaches the negative SESOI.
- `practical_equivalence` when the full interval lies inside `[-SESOI, +SESOI]` and the prospective MDE is no larger than the SESOI.
- `inconclusive` otherwise.

Do not interpret "inconclusive" as "no effect." If the prospective MDE exceeds the SESOI, the study was underpowered for the decision you wanted to make. Equivalence is interval-based; directional gain/loss claims additionally require the paired sign-flip diagnostic to reject at 0.05.

## Release checklist

- Publish the preregistration or timestamped plan before the paid run.
- Include every declared cell, including failures and timeouts.
- Publish the raw task manifest, config, plan, episode records, patches, and sanitized traces.
- Run `scaffoldscope check` on the exact release bundle.
- Report contamination, task selection, tuning, exclusions, provider incidents, and missing cells.
- Reserve official benchmark evaluation for a release candidate; do development on a distinct panel.
