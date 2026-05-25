# SWE-bench interoperability

ScaffoldScope treats the [official SWE-bench evaluator](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/evaluation.md) as the correctness authority. It creates patches from trusted local checkouts and exports standard prediction JSONL; it does not reimplement environment images or grading.

## Prepare local repositories

Download the dataset rows separately and create one trusted local clone per repository at either:

```text
<repo-cache>/owner/repository
```

or:

```text
<repo-cache>/owner__repository
```

Then convert rows:

```bash
scaffoldscope import-swebench swe-bench-lite.json \
  --repo-cache /bench/repos \
  --out tasks/swe-bench-lite.jsonl
```

The converter retains `instance_id`, `repo`, `base_commit`, `problem_statement`, `FAIL_TO_PASS`, and `PASS_TO_PASS`. It leaves `test_command` empty because repository-specific setup belongs to the official images. You may augment a development manifest with a fixed command if the local environment is known and trusted.

ScaffoldScope locally clones each Git workspace and checks out the task's detached base commit. The generated patch includes tracked and untracked text changes.

## Run and export one cell

```bash
scaffoldscope plan experiments/swe-bench-lite.json
scaffoldscope run experiments/swe-bench-lite.json

scaffoldscope export-swebench runs/context-lite-abc12345 \
  --strategy reactive-95 \
  --replicate 1729 \
  --out predictions-reactive-95-r1729.jsonl
```

The output rows contain `instance_id`, `model_name_or_path`, and `model_patch`.

For a complete study, export every treatment and replicate at once:

```bash
scaffoldscope export-swebench-matrix runs/context-lite-abc12345 \
  --out-dir evaluator-matrix \
  --dataset-name SWE-bench/SWE-bench_Lite \
  --split test
```

The destination must be outside the experiment and absent or empty. ScaffoldScope validates every cell before writing it, then creates prediction files, SHA-256 digests, a `matrix.json` identity manifest, unique evaluator run IDs, and an `evaluate.sh` runbook. Inspect and pin the evaluator environment before executing the runbook.

After the official harness finishes, pass its aggregate JSON report (the object with
`resolved_ids`, `unresolved_ids`, and related buckets) or a supported per-instance
JSON/JSONL export to ScaffoldScope as an immutable overlay. Output filenames vary by
evaluator revision, so pin and record the evaluator commit rather than relying on a
particular name:

```bash
scaffoldscope ingest-swebench runs/context-lite-abc12345 \
  official-report.json \
  --strategy reactive-95 \
  --replicate 1729 \
  --evaluator-version <pinned-swe-bench-commit> \
  --evaluator-run-id context-lite-reactive-95-r1729 \
  --image-set-digest <digest-of-the-pinned-image-manifest>
```

Generation records remain unchanged. The report joins overlays by config, strategy, replicate, and instance. Missing, extra, conflicting, incomplete, or cross-config outcomes are rejected or reported as missing rather than silently scored as failures.

Official `empty_patch_ids` are retained as observed non-solves in the
intention-to-treat denominator. Evaluator errors and incomplete instances remain
pending and are disclosed separately instead of disappearing from a strategy's
failure accounting.

## Evaluate safely

Run the official harness in its documented container environment. For untrusted repositories:

- Use a non-root user.
- Disable network access during task execution.
- Use a read-only root filesystem and writable workspace mount only.
- Drop capabilities and enable `no-new-privileges`.
- Set CPU, memory, process, and wall-time limits.
- Pin image digests, not mutable tags.
- Never mount the Docker socket, SSH directory, cloud credentials, or your working repository.

Use a unique official-evaluator `run_id` for every `(config hash, strategy, replicate)` cell. Reusing a cached run ID for a different patch can silently substitute stale evaluation results.

## Interpretation

SWE-bench contamination and repository familiarity affect absolute capability claims. A paired within-model ablation is less sensitive to that issue but is not immune to model-policy interactions. Report exact task IDs and do not generalize a curated subset to all of Lite or Verified.

Tune on a development panel. Evaluate a frozen release candidate once on the reporting panel. Do not repeatedly inspect failures and update the strategy against the same final set.
