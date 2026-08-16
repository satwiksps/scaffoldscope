# Operator guide

This guide covers the complete local experiment lifecycle: initialize, validate,
preflight, execute, inspect, verify, and share. ScaffoldScope is a measurement
instrument. Treat the source configuration and task panel as a preregistration,
and treat the generated experiment directory as evidence rather than a working
directory.

## Know the three paths

Commands accept three different kinds of identifier:

| Kind | Example | Used by |
|---|---|---|
| Source config | `experiment.json` | `validate`, `budget`, `plan`, `run` |
| Experiment directory | `runs/context-study-a1b2c3d4` | `status`, `trials`, `replay`, `report`, `check`, `bundle` |
| Trial ID | `collapse-spaces--selective--r101--1a2b3c4d` | `replay`; obtain it from `trials` |

The experiment directory name ends with the first eight characters of its
configuration identity. Do not guess this path: `plan` and `run` print it. The
identity includes the resolved configuration, task rows and source fingerprints,
prompt contents, harness implementation, and loaded plugin provenance. A meaningful
change therefore creates a different experiment directory instead of silently
resuming incompatible evidence.

## 1. Initialize a starter

Create a zero-cost, offline project:

```bash
scaffoldscope init context-study --name context-study
cd context-study
```

Without arguments, `init` creates `./scaffoldscope-study` and uses the experiment
name `my-context-ablation`. The generated project contains:

- `experiment.json`, with three context-policy variants;
- `tasks.jsonl`, with one deterministic task;
- a deliberately broken repository and its protected tests under `workspaces/`;
- a project README and `.gitignore`; and
- `.scaffoldscope-project.json`, which identifies the directory as managed by the
  initializer.

The initializer never force-overwrites a nonempty unowned directory. Repeating the
same command with the same name preserves edited files and restores missing managed
files. A different name is rejected because it would change the project identity.

The starter uses the scripted provider. It validates execution, trace capture,
patching, and comparison plumbing without contacting a model API; its solve rate is
not model-performance evidence.

## 2. Validate the source configuration

Run validation after every edit to the config, task manifest, prompt, or task
workspace:

```bash
scaffoldscope validate experiment.json
```

Success prints the task-by-replicate-by-variant matrix size and full config hash.
Validation checks the schema, identifiers, policy settings, baseline and primary
comparison, task selection, workspace availability, model settings, budget fields,
plugin compatibility, and source fingerprints. It makes no paid model calls.

For editor or CI integration, print or export the packaged JSON Schema:

```bash
scaffoldscope schema --out scaffoldscope-experiment.schema.json
```

The export is idempotent for identical content and refuses to replace a different
file.

Check environment-specific readiness without creating a plan or making a provider call:

```bash
scaffoldscope doctor --config experiment.json
```

This reports the resolved config identity, a literal credential-readiness status
(`configured`, `missing`, or `not-required`), sandbox backend, loaded plugin provenance,
and, when Docker is selected, the locally resolved image identity. It never prints a
credential value or its environment-variable name.

Before running untrusted repository tests, choose an isolated execution environment.
`LocalSandbox` limits the agent's structured tools but is **not an operating-system
security boundary**. Use a disposable container or virtual machine and keep source,
SSH, cloud, and package-registry credentials out of the test environment.

## 3. Materialize and review the plan

Create the durable trial matrix without calling the model:

```bash
scaffoldscope plan experiment.json
```

The command prints the exact experiment directory and writes its identity files,
including:

- `config.resolved.json`: resolved paths, fingerprints, and config identity;
- `manifest.json`: environment, model, variant, task, and implementation provenance;
- `pricing.json`: the user-supplied price snapshot and its hash; and
- `plan.jsonl`: deterministic task, replicate, treatment, block, and execution order.

Review all four before authorizing spend. Within each task-and-replicate block,
variants execute sequentially in the recorded order; independent blocks may execute
concurrently. Do not edit generated identity files. Change the source config or task
panel and run `validate` and `plan` again.

## 4. Inspect budget and statistical headroom

Estimate the declared upper bounds before a paid run:

```bash
scaffoldscope budget experiment.json
scaffoldscope budget experiment.json --json
```

The estimate reports grid size, maximum model calls, maximum configured tokens, a
configured-price cost bound when one can be computed, and the prospective paired
minimum detectable effect. This is a conservative planning calculation, not a
quote. Prices are the snapshot in your configuration, the `char4-v1` token counter
is an approximation, and the provider invoice remains the billing authority.

Before continuing, resolve rather than waive these warnings:

- `LOW_TASK_COUNT`: fewer than 20 independent tasks keeps results descriptive;
- `SEED_UNCONFIRMED`: replicates may be identical when the provider does not honor
  seeds; and
- `COST_UNBOUNDED`: neither a hard per-trial cost cap nor complete configured
  pricing bounds the matrix.

A professional run should predeclare its task panel, primary comparison, smallest
effect size of interest, replicate policy, retry behavior, and every turn, token,
cost, process, and timeout limit. Do not increase power or change the primary
comparison after inspecting outcomes.

## 5. Run or resume

For an OpenAI-compatible provider, set the environment variable named by
`model.api_key_env`, then run:

```bash
scaffoldscope run experiment.json
```

The command executes the recorded matrix, rebuilds `episodes.jsonl` from atomic
per-trial results, and generates Markdown, HTML, JSON, and CSV reports. It returns a
nonzero status when an infrastructure failure occurred.

Running the same source configuration again is a resume operation. A trial is
skipped only when its configuration, implementation, task source, identity, trace,
and patch hashes all match. A partial or integrity-invalid trial directory is moved
under `aborted-attempts/` before that trial is re-executed, preserving the failed
attempt for diagnosis. Workers write only inside their own trial directories;
aggregate output is rebuilt after execution.

An operating-system lock rejects concurrent experiment mutations. It covers `plan`, `run`,
report regeneration, strict checks, bundle creation, workspace cleanup, and evaluator-overlay
ingestion for the same directory. The lock is released automatically when the owning process
exits; do not bypass it.
Concurrency is controlled by `experiment.max_workers`, with paired variants kept
sequential inside each block.

## 6. Monitor durable progress

From another terminal, use the experiment directory printed by `plan` or `run`:

```bash
scaffoldscope status runs/context-study-a1b2c3d4
scaffoldscope status runs/context-study-a1b2c3d4 --json
```

`status` reads the plan and atomic per-trial results; it does not depend on a
possibly stale aggregate and does not rewrite experiment files. It reports completed
trials, remaining trials, paired-block coverage, terminal status counts,
recorded provider-or-estimated tokens, configured-price cost, and incomplete-usage counts. The
JSON view also exposes traces that started without a durable result and malformed
result IDs.

Cost totals include only trials with observable usage. If a failed provider attempt
did not report usage, the ledger is incomplete and ScaffoldScope does not invent a
charge.

## 7. Find and filter trials

List every planned trial, including trials without a result:

```bash
scaffoldscope trials runs/context-study-a1b2c3d4
```

Filter with exact values from the plan or result:

```bash
scaffoldscope trials runs/context-study-a1b2c3d4 --status infrastructure_error
scaffoldscope trials runs/context-study-a1b2c3d4 --variant selective
scaffoldscope trials runs/context-study-a1b2c3d4 --task collapse-spaces
```

Filters may be combined. Use JSONL for scripts and notebooks:

```bash
scaffoldscope trials runs/context-study-a1b2c3d4 --jsonl
```

The plain view shows trial ID, status, solve outcome, and tokens. The JSONL view also
contains task, variant, replicate, block and order position, validity flags, cost,
and wall time. A trace that started but has no result still appears as `planned` in
this inventory; `status --json` reports the separate `started_without_result`
count.

## 8. Replay one trace offline

Copy a trial ID from `trials`, then inspect its timeline:

```bash
scaffoldscope replay runs/context-study-a1b2c3d4 \
  collapse-spaces--selective--r101--1a2b3c4d
```

For the full event payloads:

```bash
scaffoldscope replay runs/context-study-a1b2c3d4 \
  collapse-spaces--selective--r101--1a2b3c4d --json
```

Replay is strictly offline. It makes no provider calls, runs no tools, and does not
reconstruct a mutable workspace. It verifies trial identity, trace hash, contiguous
sequence numbers, and the terminal event before returning a chronological view.
JSON replay can contain prompts, source excerpts, tool observations, and error
details; treat it as sensitive experiment evidence.

## 9. Regenerate reports

Use the preregistered analysis settings from the source config:

```bash
scaffoldscope report runs/context-study-a1b2c3d4
scaffoldscope report runs/context-study-a1b2c3d4 --open
```

This rewrites only derived report files. Raw per-trial traces, patches, and results
remain unchanged. The report includes solve and governed-solve summaries, paired
comparisons, resource deltas, interval and randomization analysis where justified,
pair coverage, and explicit warnings.

The command also accepts `--bootstrap-samples`, `--analysis-seed`, and `--sesoi`.
Those are post-run analysis overrides and replace the derived reports in that
directory. For published primary results, use the frozen config values. If you run a
sensitivity analysis, record the override and keep its output separate from the
preregistered report.

## 10. Verify evidence integrity

After the matrix and any external evaluation overlays are complete, run:

```bash
scaffoldscope check runs/context-study-a1b2c3d4
```

The check validates plan and result identity, uniqueness, aggregate equality,
artifact containment, trace and patch hashes, terminal events, and evaluator overlay
integrity. Do not publish or bundle a directory that fails.

For a release candidate, also run:

```bash
scaffoldscope check runs/context-study-a1b2c3d4 --strict
```

Strict mode regenerates the report and treats every report warning as a failure.
Warnings are not automatically defects: the offline starter is intentionally
scripted and underpowered, so it is expected to fail strict publication checks.

## 11. Create and verify a shareable bundle

Write the archive outside the experiment directory:

```bash
scaffoldscope bundle runs/context-study-a1b2c3d4 \
  --out context-study-evidence.zip
scaffoldscope verify-bundle context-study-evidence.zip
```

`bundle` first runs the normal integrity check and refuses to overwrite an existing
archive. It then regenerates every report artifact from the resolved experiment
configuration; manual report edits and one-off `report` overrides are not published.
It creates a deterministic ZIP with an internal hash manifest. The archive
contains resolved configuration, plan, pricing, reports, aggregate results,
per-trial traces, patches, results, and external evaluator overlays when present.
Generated workspaces are excluded. If interrupted or integrity-invalid attempts were archived,
their traces, patches, and results are included under `aborted-attempts/`; their mutable workspaces
remain excluded.

Workspace-free does not mean secret-free. Traces can retain private code, prompts,
tool output, repository paths, and data that does not resemble a common credential.
Inspect the archive's intended contents and your data-sharing obligations before
publishing it. `verify-bundle` checks paths, entry sizes, declared files, hashes, bundle
identity, cross-file experiment semantics, and canonical report regeneration for the current
integrity profile. It materializes validated regular files only inside an isolated temporary
directory; it never extracts them into the caller's workspace.

After verifying a bundle, generated workspaces can be removed while retaining the
evidence required by `check`:

```bash
scaffoldscope clean runs/context-study-a1b2c3d4 --workspaces
```

## Failure recovery

Preserve evidence first. Do not repair a run by hand-editing `manifest.json`,
`plan.jsonl`, `result.json`, `events.jsonl`, `patch.diff`, or `episodes.jsonl`.

| Symptom | Meaning | Safe response |
|---|---|---|
| `init` refuses the destination | The directory is unowned, conflicting, or was initialized with another name | Use a new or empty directory. Repeat with the original name for a marked starter; there is no force option. |
| `validate` reports a missing workspace or manifest | A source path does not resolve relative to the config or manifest | Fix the source path, validate again, and create a new plan. |
| `plan` reports differing generated identity files | Generated evidence was edited or two incompatible inputs resolved to the same location | Preserve the directory. Revert edits to source-of-truth inputs or change the experiment identity and plan again; never rewrite the generated plan. |
| Run fails before model calls | API-key environment, Docker, plugin, repository, or filesystem preflight failed | Correct the environment and rerun the same config. `scaffoldscope doctor --config experiment.json` checks experiment readiness; `scaffoldscope plugins --check` validates every installed plugin. |
| Process exits with `130` or the host stops | One or more trials may have a trace but no result | Run `status --json`, then rerun the same config. Matching completed trials resume; partial attempts are archived and re-executed. |
| A terminal result is `infrastructure_error` | The trial completed with infrastructure-invalid evidence | A same-config run preserves that terminal result and will not retry it. Follow the preregistered incident policy. For a clean rerun, fix the cause, choose a new experiment identity, and run a fresh paired matrix rather than deleting selected failures. |
| A result is `harness_error` | The treatment implementation failed | It remains an intent-to-treat non-solve. Fix the harness, which changes implementation identity, then run a new matrix; do not recode it as missing. |
| A result is `model_error`, `context_overflow`, `turn_limit`, `token_limit`, `cost_limit`, or `cost_unobservable` | A declared protocol or observability boundary terminated the episode | Keep it in the denominator under the declared analysis. Change limits or provider policy only in a new experiment identity. Check the provider invoice when usage is incomplete. |
| Result is `awaiting_external_evaluation` | Generation succeeded but no authoritative evaluator outcome is attached | Complete the documented SWE-bench export/evaluate/ingest workflow, then regenerate the report. |
| `replay` reports a hash or sequence error | The trace or result is incomplete or changed | Preserve a copy and run `check`. A later same-config `run` treats an integrity-invalid trial as incomplete, archives it, and re-executes it; this may incur model cost. |
| `check` fails after manual edits | Evidence no longer matches its recorded identity | Restore the original bytes from trusted storage. If unavailable, retain the failed bundle for audit and run a fresh experiment. |
| `bundle` refuses its output | The destination exists, lies inside the experiment, or integrity failed | Choose a new external filename, or resolve the reported integrity problem. Bundles are never overwritten. |

For SWE-bench-specific pending outcomes and evaluator incident handling, follow
[`docs/swebench.md`](swebench.md). For status definitions and denominator rules, see
[`docs/results-schema.md`](results-schema.md).

## Automation and exit behavior

Use machine-readable views instead of scraping prose:

- `budget CONFIG --json`
- `status EXPERIMENT_DIR --json`
- `trials EXPERIMENT_DIR --jsonl`
- `replay EXPERIMENT_DIR TRIAL_ID --json`

Successful commands return `0`. CLI-level configuration, integrity, and
operating-system failures return `2`; `run` also returns `2` when it records one or
more infrastructure failures. Episode-level model or protocol terminations are
durable outcomes and do not necessarily make the overall command fail. An
interrupted command returns `130`. Reports can contain warnings while their command
still succeeds, which is why publication automation should finish with `check
--strict` and `verify-bundle`.
