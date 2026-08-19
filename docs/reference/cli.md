# Command-line reference

The executable is `scaffoldscope`. Every command accepts `-h` or `--help`. The same interface is available as `python -m scaffoldscope`.

```{program-output} python -m scaffoldscope --help
```

## Path conventions

Commands accept three distinct path types:

| Path | Source of truth | Commands |
|---|---|---|
| Configuration file | JSON you edit | `validate`, `doctor --config`, `budget`, `plan`, `run` |
| Experiment directory | Generated evidence directory | `status`, `trials`, `replay`, `report`, `check`, `bundle`, `clean`, SWE-bench exports and ingestion |
| Bundle archive | Deterministic ZIP | `verify-bundle` |

`plan` and `run` print the resolved experiment directory. Use that path. Its suffix is derived from the full configuration identity.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Command completed. Episode-level non-solves can still be valid results. |
| `2` | Configuration, integrity, operating-system, or infrastructure failure. `run` also uses this when it records infrastructure-invalid trials. |
| `130` | Interrupted by the operator. |

Use `status --json`, `trials --jsonl`, `replay --json`, and `budget --json` for automation. Human-readable output is not a stable machine protocol.

## `init`

Create a safe, runnable starter project.

```{program-output} python -m scaffoldscope init --help
```

```bash
scaffoldscope init my-study --name my-study
```

`directory` defaults to `./scaffoldscope-study`. `--name` sets `experiment.name`; it does not merely label the folder. The initializer refuses nonempty unowned directories, conflicts, and symlink targets. Repeating the same initialization can recover missing managed files while preserving operator edits.

## `validate`

Load the source config, task manifest, task sources, prompt, and selected plugins. Validate strict types and fields, then compute the full experiment identity.

```{program-output} python -m scaffoldscope validate --help
```

```bash
scaffoldscope validate experiment.json
```

This command does not call a model, execute tests, or inspect Docker images.

## `doctor`

Inspect package or experiment-specific readiness without running a trial.

```{program-output} python -m scaffoldscope doctor --help
```

```bash
scaffoldscope doctor
scaffoldscope doctor --config experiment.json
```

With `--config`, the command resolves the provider, reports credential readiness without exposing the secret or variable name, validates selected plugins, and preflights the Docker image when the Docker backend is selected.

## `schema`

Print or export the packaged Draft 2020-12 experiment schema.

```{program-output} python -m scaffoldscope schema --help
```

```bash
scaffoldscope schema --out scaffoldscope-experiment.schema.json
```

Without `--out`, the schema is printed to standard output. Export refuses to replace a different existing file.

## `budget`

Show the planned matrix size and declared resource ceilings before a run.

```{program-output} python -m scaffoldscope budget --help
```

```bash
scaffoldscope budget experiment.json
scaffoldscope budget experiment.json --json
```

The estimate is based on configured limits. It is not a provider invoice and does not predict early termination.

## `plan`

Freeze the resolved configuration and full task, replicate, and treatment matrix without making provider calls.

```{program-output} python -m scaffoldscope plan --help
```

```bash
scaffoldscope plan experiment.json
```

`plan` writes the experiment identity files and `plan.jsonl`. Host runtime and observed Docker identity remain unpinned until the first real run so a reviewed plan can move to its intended worker.

## `run`

Execute or safely resume the complete experiment matrix.

```{program-output} python -m scaffoldscope run --help
```

```bash
scaffoldscope run experiment.json
```

The command performs provider, plugin, sandbox, runtime, and evidence preflight before reusing or starting trials. It pins runtime identity on first execution, creates isolated per-trial workspaces, writes atomic results, rebuilds the aggregate in plan order, and regenerates reports. Integrity-invalid partial attempts are archived before re-execution.

There is no flag to run only selected treatments or tasks. Filtering a paired matrix at execution time would alter the declared experiment.

## `status`

Summarize progress without changing experiment evidence.

```{program-output} python -m scaffoldscope status --help
```

```bash
scaffoldscope status runs/my-study-1a2b3c4d
scaffoldscope status runs/my-study-1a2b3c4d --json
```

The output distinguishes planned, completed, pending, failed, interrupted, and external-evaluation states and reports recorded usage-provenance gaps.

## `trials`

List the stable per-trial inventory.

```{program-output} python -m scaffoldscope trials --help
```

```bash
scaffoldscope trials runs/my-study-1a2b3c4d
scaffoldscope trials runs/my-study-1a2b3c4d --variant selective --status resolved
scaffoldscope trials runs/my-study-1a2b3c4d --task parser-001 --jsonl
```

Filters affect display only. `--jsonl` emits one JSON object per selected trial.

## `replay`

Verify and display one persisted event timeline without invoking a provider, model, tool, evaluator, or mutable workspace.

```{program-output} python -m scaffoldscope replay --help
```

```bash
scaffoldscope replay runs/my-study-1a2b3c4d <trial-id>
scaffoldscope replay runs/my-study-1a2b3c4d <trial-id> --json
```

JSON replay can contain prompts, source excerpts, model content, tool observations, evaluator output, and error details. Treat it as sensitive evidence.

## `report`

Rebuild derived analysis files from frozen raw evidence.

```{program-output} python -m scaffoldscope report --help
```

```bash
scaffoldscope report runs/my-study-1a2b3c4d
scaffoldscope report runs/my-study-1a2b3c4d --open
```

`--bootstrap-samples`, `--analysis-seed`, and `--sesoi` perform a post-run sensitivity analysis and overwrite the reports in that directory. They do not change raw trials. Evidence bundling always regenerates the canonical report from the frozen configuration, so keep sensitivity outputs separately.

## `check`

Validate the experiment's cross-file evidence contract.

```{program-output} python -m scaffoldscope check --help
```

```bash
scaffoldscope check runs/my-study-1a2b3c4d
scaffoldscope check runs/my-study-1a2b3c4d --strict
```

Normal mode verifies required artifacts, identities, exact plan coverage and ordering, task and treatment provenance, pricing, aggregate equality, trace lifecycle and context evidence, patch hashes, evaluator overlays, and canonical reports. Strict mode also fails on analysis warnings. Tiny scripted demos are expected to fail strict publication checks.

## `bundle`

Create a deterministic, workspace-free evidence archive.

```{program-output} python -m scaffoldscope bundle --help
```

```bash
scaffoldscope bundle runs/my-study-1a2b3c4d --out my-study-evidence.zip
```

The output must be outside the experiment directory and must not already exist. The command validates raw evidence, regenerates canonical reports, and hashes every included file. It never includes mutable generated workspaces.

## `verify-bundle`

Verify a ScaffoldScope evidence ZIP without extracting it into the caller's workspace.

```{program-output} python -m scaffoldscope verify-bundle --help
```

```bash
scaffoldscope verify-bundle my-study-evidence.zip
```

Verification checks safe canonical archive paths, allowed files, declared sizes and hashes, outer and inner experiment identity, experiment semantics, and canonical report regeneration for the current integrity profile.

## `clean`

Remove generated trial workspaces after evidence has been verified.

```{program-output} python -m scaffoldscope clean --help
```

```bash
scaffoldscope clean runs/my-study-1a2b3c4d --workspaces
```

The confirmation flag is mandatory. The command retains configs, plans, results, traces, patches, reports, overlays, and archived failed-attempt evidence. It rejects symlinked or escaping workspace paths.

## `plugins`

Discover installed extension metadata or explicitly import and validate extensions.

```{program-output} python -m scaffoldscope plugins --help
```

```bash
scaffoldscope plugins
scaffoldscope plugins --check
scaffoldscope plugins --json
```

Discovery reads distribution metadata without importing plugin modules. `--check` authorizes imports, validates registration and compatibility, and computes implementation provenance for every discovered plugin.

## `demo`

Copy and run the larger bundled offline engine demonstration.

```{program-output} python -m scaffoldscope demo --help
```

```bash
scaffoldscope demo
scaffoldscope demo --directory ./engine-demo --open
```

The demo is deterministic and costs nothing. It exercises four context policies across three fixtures. It is a system demonstration, not a benchmark.

## `import-swebench`

Convert SWE-bench dataset JSON or JSONL rows into a ScaffoldScope task manifest using trusted local repository clones.

```{program-output} python -m scaffoldscope import-swebench --help
```

```bash
scaffoldscope import-swebench swe-bench-lite.json \
  --repo-cache /bench/repos \
  --out tasks/swe-bench-lite.jsonl
```

The imported tasks intentionally have no local evaluator command. Official SWE-bench grading is attached later through immutable external-evaluation overlays.

## `export-swebench`

Export one treatment and replicate cell as official prediction JSONL.

```{program-output} python -m scaffoldscope export-swebench --help
```

```bash
scaffoldscope export-swebench runs/lite-1a2b3c4d \
  --strategy selective \
  --replicate 1729 \
  --out predictions.jsonl
```

The output contains `instance_id`, `model_name_or_path`, and `model_patch`.

## `export-swebench-matrix`

Export every treatment and replicate cell, unique evaluator run IDs, checksums, matrix identity, and a pinned runbook.

```{program-output} python -m scaffoldscope export-swebench-matrix --help
```

```bash
scaffoldscope export-swebench-matrix runs/lite-1a2b3c4d \
  --out-dir evaluator-matrix \
  --dataset-name SWE-bench/SWE-bench_Lite \
  --split test
```

The destination must be absent or empty and outside the experiment directory.

## `ingest-swebench`

Attach official evaluator results as an immutable overlay for one treatment and replicate cell.

```{program-output} python -m scaffoldscope ingest-swebench --help
```

```bash
scaffoldscope ingest-swebench runs/lite-1a2b3c4d official-results.json \
  --strategy selective \
  --replicate 1729 \
  --evaluator-version <commit> \
  --evaluator-run-id <unique-run-id> \
  --image-set-digest <image-manifest-digest>
```

Overlays are keyed to the frozen experiment cell and never rewrite generation results. Re-ingestion must be byte-identical. Missing or incomplete evaluator outcomes remain explicit instead of becoming silent non-solves.
