# Zero-cost quickstart

The starter creates one small repository task and three context-policy treatments. Its provider responses are scripted, so the workflow is deterministic and makes no network request.

## 1. Create the project

```bash
scaffoldscope init my-study --name my-study
cd my-study
```

The directory contains:

```text
my-study/
├── .gitignore
├── .scaffoldscope-project.json
├── README.md
├── experiment.json
├── tasks.jsonl
└── workspaces/
    └── text-cleaner/
        ├── test_text_cleaner.py
        └── text_cleaner.py
```

The marker file lets `init` recover an interrupted initialization without overwriting unrelated directories. Repeating the same command preserves your edits and restores missing managed files.

## 2. Validate and estimate

```bash
scaffoldscope validate experiment.json
scaffoldscope doctor --config experiment.json
scaffoldscope budget experiment.json
```

These commands do not call a provider. `validate` resolves task and source fingerprints. `doctor` checks local prerequisites without probing a model endpoint. `budget` shows the planned trials and declared upper bounds.

## 3. Freeze the plan

```bash
scaffoldscope plan experiment.json
```

The command prints an experiment directory similar to:

```text
runs/my-study-1a2b3c4d
```

The suffix comes from the full experiment identity. Use the printed path in later commands. Do not rename or hand-edit generated evidence files.

## 4. Run the matrix

```bash
scaffoldscope run experiment.json
```

The same command safely resumes an interrupted run. Completed trials are reused only when their full persisted identity and trace lifecycle remain valid.

## 5. Inspect results

Replace the sample path with the one printed by `plan` or `run`:

```bash
scaffoldscope status runs/my-study-1a2b3c4d
scaffoldscope trials runs/my-study-1a2b3c4d
scaffoldscope report runs/my-study-1a2b3c4d --open
```

Copy a trial ID from `trials` to replay its event timeline without running a model or tool:

```bash
scaffoldscope replay runs/my-study-1a2b3c4d <trial-id>
```

## 6. Verify and bundle

```bash
scaffoldscope check runs/my-study-1a2b3c4d
scaffoldscope bundle runs/my-study-1a2b3c4d --out my-study-evidence.zip
scaffoldscope verify-bundle my-study-evidence.zip
```

The ZIP excludes mutable workspaces. It contains the frozen configuration, plan, pricing snapshot, aggregate results, per-trial results, traces, patches, reports, and a SHA-256 bundle manifest.

```{warning}
The starter demonstrates mechanics only. Its repeated scripted trajectories are not independent model samples, and its tiny task panel cannot support performance claims.
```

## Next steps

- Read [Your first real experiment](first-experiment.md).
- Learn every lifecycle command in the [operator guide](../operator-guide.md).
- Review field-level options in the [configuration reference](../configuration.md).
- Read the [experiment-design contract](../experiment-design.md) before paying for a matrix.
