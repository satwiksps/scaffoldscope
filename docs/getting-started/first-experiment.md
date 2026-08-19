# Your first real experiment

A useful first study is small enough to audit and large enough to create context pressure. Start with a development panel. Do not begin by spending on a full benchmark.

## 1. State one question

Use a question that maps to one declared treatment axis:

> Under the same model, task panel, evaluator, and budget, does selective context retention change solve rate or total tokens relative to no compaction?

Avoid changing the policy, tool list, prompt, retries, and budget in the same comparison. You would not know which change caused the result.

## 2. Build a development task panel

Each JSONL task row needs a stable ID, a workspace, a problem statement, and usually a fixed evaluator command:

```json
{"id":"parser-001","workspace":"workspaces/parser-001","problem":"Preserve comments while normalizing whitespace.","test_command":["{python}","-m","pytest","-q"],"protected_paths":["tests"],"constraints":[{"id":"keep-license","text":"Do not modify LICENSE.","check":{"type":"file_unchanged","path":"LICENSE"}}]}
```

Use repository snapshots or immutable Git commits. Keep evaluator files outside the model's writable surface with `protected_paths`. A local evaluator is trusted infrastructure, not a security boundary; use the [Docker backend](../docker.md) for untrusted code.

## 3. Replace the scripted model

Start from a generated project and replace its `model` object:

```json
{
  "provider": "openai_compatible",
  "name": "exact-provider-model-revision",
  "base_url": "https://provider.example/v1",
  "api_key_env": "OPENAI_API_KEY",
  "requires_api_key": true,
  "context_window_tokens": 32768,
  "max_output_tokens": 2048,
  "temperature": 0,
  "supports_seed": false,
  "json_mode": true,
  "input_price_per_million": null,
  "output_price_per_million": null
}
```

Pin an immutable model revision when the provider exposes one. Leave prices as `null` when you cannot support the estimate. Setting prices does not turn the result into an invoice.

Export the credential only in the process environment:

```bash
export OPENAI_API_KEY="..."
```

On PowerShell:

```powershell
$env:OPENAI_API_KEY = "..."
```

ScaffoldScope reads the named variable at runtime and does not print its name or value in `doctor`. Never put credentials in JSON, task files, prompts, or shell history committed to Git.

## 4. Declare baseline and treatment

```json
{
  "variants": [
    {"id":"none","policy":"none"},
    {
      "id":"selective",
      "policy":"selective",
      "trigger_ratio":0.8,
      "target_ratio":0.65,
      "keep_recent_bundles":2
    }
  ]
}
```

Set `experiment.baseline` to `none` and `experiment.primary_comparison` to `selective`. Freeze the smallest effect of practical interest before running the reporting panel.

## 5. Set hard operational limits

Declare turn, token, output, timeout, worker, and cost limits before provider calls. Begin with one worker while validating a provider integration. Increase concurrency only after you understand provider rate limits and evaluator resource use.

Run:

```bash
scaffoldscope validate experiment.json
scaffoldscope doctor --config experiment.json
scaffoldscope budget experiment.json
scaffoldscope plan experiment.json
```

Inspect `config.resolved.json`, `manifest.json`, and `plan.jsonl` before execution. Archive or timestamp the plan if the study will support a public claim.

## 6. Run development, then freeze

Use the development panel to find integration defects and confirm that the treatment activates. Check:

- effective model and fingerprint do not differ by treatment;
- usage is complete enough for the intended cost claim;
- context pressure actually triggers the treatment;
- evaluator and constraint checks behave as intended;
- paired coverage is complete; and
- duplicate trajectory rates do not make nominal replicates misleading.

After tuning, freeze a separate reporting panel. Do not inspect reporting failures and modify the mechanism against the same panel.

## 7. Interpret the report conservatively

ScaffoldScope preserves solve failures and separates infrastructure-invalid cells. It cannot repair low statistical power, benchmark contamination, provider drift, or a saturated model. Read the [experiment-design contract](../experiment-design.md) before describing a difference as meaningful.
