# ScaffoldScope starter experiment

This is a small, runnable experiment for learning the ScaffoldScope workflow. It
uses the deterministic scripted provider, so the first run makes no network calls
and spends no API credits. Its results validate the machinery; they are not model
performance evidence.

## Run the starter

From this directory:

```bash
scaffoldscope validate experiment.json
scaffoldscope plan experiment.json
scaffoldscope run experiment.json
```

The final command prints the versioned result directory containing the immutable
trial evidence and reports. Verify a result bundle before sharing it:

```bash
scaffoldscope check runs/<experiment-directory>
```

## Turn it into your experiment

1. Copy real repositories into `workspaces/` or import a SWE-bench manifest.
2. Replace `tasks.jsonl` with a fixed, version-controlled task panel.
3. Change `model.provider` to `openai_compatible`, set a pinned model name and
   endpoint, and remove each task's scripted `script` field.
4. Predeclare a primary comparison, effect size, budgets, and at least three
   independent replicates before observing evaluation results.
5. Keep model, prompt, task, tool, retry, and budget settings fixed while changing
   only the harness component under study.

`LocalSandbox` controls tool access and records changes, but it is not an operating
system security boundary. Run untrusted repositories inside a disposable container
or virtual machine.

See the main ScaffoldScope documentation for the full configuration reference,
SWE-bench handoff, evidence schema, and statistical interpretation rules.
