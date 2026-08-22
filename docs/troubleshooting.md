# Troubleshooting

Start with the narrowest read-only command that covers the failing boundary:

```bash
scaffoldscope --version
scaffoldscope doctor
scaffoldscope validate experiment.json
scaffoldscope doctor --config experiment.json
scaffoldscope plugins --check
scaffoldscope status <experiment-directory> --json
scaffoldscope check <experiment-directory>
```

Do not repair evidence by editing generated JSON, JSONL, trace, or patch files. Preserve the directory, correct the source or environment, and rerun the source configuration.

## Installation and shell

### `scaffoldscope` is not found

Confirm the expected interpreter and package:

```bash
python -m pip show scaffoldscope
python -m scaffoldscope --version
```

If the module command works but the console command does not, activate the environment where the package was installed. On Windows, inspect `Get-Command python` and `Get-Command scaffoldscope`. On POSIX shells, inspect `command -v python` and `command -v scaffoldscope`.

### The installed version is not the expected version

```bash
python -m pip install --upgrade "scaffoldscope==1.0.0"
python -m scaffoldscope --version
```

Check that `pip` and `python` refer to the same environment. Prefer `python -m pip` over an unqualified `pip` command.

### Windows reports an execution-policy error when activating the environment

You can invoke the environment interpreter without activation:

```powershell
.\.venv\Scripts\python.exe -m pip install scaffoldscope
.\.venv\Scripts\python.exe -m scaffoldscope --version
```

Follow your organization's PowerShell policy rather than weakening it globally.

## Configuration and paths

### Unknown field or wrong type

ScaffoldScope uses strict JSON and rejects ignored or misspelled fields. Export the matching schema:

```bash
scaffoldscope schema --out experiment.schema.json
scaffoldscope validate experiment.json
```

Third-party settings belong only in `model.plugin_options` or `variants[].plugin_options`.

### Task manifest or workspace is missing

Configuration-relative and manifest-relative paths use different bases:

- `tasks.manifest` resolves relative to the experiment JSON.
- A task row's `workspace` resolves relative to the task manifest.
- `agent.prompt_file` resolves relative to the experiment JSON.

Use absolute paths only for local diagnosis. Portable studies should keep relative paths under a controlled project root.

### A config change still points to an old run

The experiment directory suffix is based on the full resolved identity. If the directory did not change, confirm that the edited file is the one passed to the command. `validate` prints the full hash. Do not rename a generated directory to simulate a new identity.

### JSON from PowerShell fails to load

Current ScaffoldScope readers accept UTF-8 with or without a byte-order mark. Ensure the file is valid JSON rather than PowerShell object formatting:

```powershell
Get-Content .\experiment.json -Raw | ConvertFrom-Json | Out-Null
```

JSON does not allow comments, trailing commas, `NaN`, or `Infinity`.

## Provider and credentials

### `doctor` reports `missing`

Set the environment variable named by `model.api_key_env` in the same process tree that launches ScaffoldScope. `doctor` deliberately does not echo the variable name or value.

```bash
export OPENAI_API_KEY="..."
scaffoldscope doctor --config experiment.json
```

PowerShell:

```powershell
$env:OPENAI_API_KEY = "..."
scaffoldscope doctor --config experiment.json
```

An empty variable is not a usable credential. Do not place secrets in the config.

### A local endpoint does not use authentication

Set `requires_api_key` to `false` only for a deliberate local endpoint. A remote endpoint carrying a key must use HTTPS.

### HTTP 404 or endpoint mismatch

The bundled adapter calls an OpenAI-compatible `/chat/completions` route under `base_url`. Confirm whether the provider expects a base such as `https://host/v1`, whether JSON mode is supported, and whether the configured model name is accepted by that route.

### Output is not a valid agent action

The model must return one JSON action under the documented prompt protocol. Inspect a failed trial with:

```bash
scaffoldscope trials <experiment-directory> --status model_error
scaffoldscope replay <experiment-directory> <trial-id> --json
```

If a model ignores JSON mode or tool instructions, that model/provider route may not be compatible without a provider plugin. Do not silently add retries or prompt changes to only one treatment.

### Usage or cost is incomplete

ScaffoldScope labels locally estimated and provider-reported usage separately. Failed or retried calls without complete provider usage make the cost ledger incomplete. A configured price cannot recover unknown billed work. Check provider invoices independently and avoid cost claims from incomplete ledgers.

## Plans, runs, and resume

### `plan` refuses existing identity files

Generated identity files differ from the source configuration or were edited. Preserve the directory for audit. Restore the original source inputs or make a meaningful source change that creates a new experiment identity. Never force-overwrite the generated plan.

### A run appears stuck

Use another terminal:

```bash
scaffoldscope status <experiment-directory> --json
```

Then inspect provider rate limits, Docker capacity, evaluator timeouts, and the latest trial event logs. Do not start a second writer against the same experiment directory; the experiment lock will reject it.

### A run was interrupted

Run the same source config again:

```bash
scaffoldscope run experiment.json
```

Valid completed trials resume. Partial or integrity-invalid attempts are preserved under `aborted-attempts` and re-executed. Re-execution can incur provider cost.

### Resume rejects runtime drift

Once the first trial starts, the manifest pins Python implementation/version, OS, machine, and token-counter identity. Resume on the original runtime or create a new experiment identity. Mixing host runtimes inside one result matrix is not supported.

### A trial is `harness_error`

The treatment implementation failed, so the result is infrastructure-invalid and excluded from the solve denominator. Preserve the failed row as a missing cell. Fixing the harness changes implementation identity and requires a fresh matrix.

### A trial is `infrastructure_error`

The workspace, evaluator, filesystem, or other exogenous infrastructure failed. Preserve it under the incident policy declared before the run. A same-config resume does not selectively retry a terminal infrastructure failure.

### A trial reached a protocol limit

`context_overflow`, `turn_limit`, `token_limit`, `cost_limit`, `cost_unobservable`, and `model_error` are durable protocol outcomes. Keep them in the declared denominator. Changing the limit or retry policy requires a new experiment identity.

## Local and Docker evaluation

### Local tests cannot see an expected environment variable

Evaluator subprocesses receive a scrubbed, deterministic allowlist rather than the full harness environment. Put non-secret test inputs in the task workspace or evaluator image. Provider credentials are never a valid evaluator dependency.

### The model can edit a test file

Add evaluator files and configuration to `protected_paths`. ScaffoldScope evaluates against protected copies and checks evaluator integrity, but task design remains your responsibility.

### Docker image is unavailable locally

ScaffoldScope uses `--pull=never`. Pull or build the exact image before the run:

```bash
docker pull --platform linux/amd64 ghcr.io/example/eval@sha256:<digest>
docker image inspect ghcr.io/example/eval@sha256:<digest>
scaffoldscope doctor --config experiment.json
```

### Docker reports a platform mismatch

Pull or build the configured platform. Do not mix native and emulated evaluation under one experiment identity. See [Docker evaluator backend](docker.md) for image, user, mount, resource, and remote-daemon failures.

## Reports and evidence

### The report has zero solves for imported SWE-bench tasks

Imported tasks intentionally have no local evaluator. Generated episodes remain `awaiting_external_evaluation` until official results are ingested. Follow the [SWE-bench workflow](swebench.md).

### Pair coverage is below 100 percent

Inspect `trials --jsonl` and status counts by treatment. Missing infrastructure cells, pending official evaluation, or incomplete matrix execution reduce coverage. A comparison may have pairwise coverage even when an unrelated treatment is incomplete, but the full factorial coverage remains disclosed.

### `check --strict` fails on the demo

This is expected. Strict mode fails on every report warning. The scripted demo is small, deterministic, and intentionally not inference-ready.

### `check` reports a hash, trace, or aggregate mismatch

Do not edit files to make the checker pass. Restore original bytes from trusted storage or retain the invalid evidence and run a fresh experiment. A bundle created before the change can serve as a recovery source if its own verification passes.

### `bundle` refuses the output path

The output must not exist and must be outside the experiment directory. Choose a new file:

```bash
scaffoldscope bundle <experiment-directory> --out evidence-2.zip
```

Bundles are immutable and never overwritten.

### `verify-bundle` fails

Keep the archive unchanged and record the exact failure. Re-download or recopy it, then retry. A hash failure means the bytes do not match the bundle manifest. A semantic failure means the contained evidence is internally inconsistent even if the ZIP is structurally readable.

## Plugins

### A plugin is listed but fails under `--check`

Metadata discovery does not import plugin code. `--check` does. The error includes the distribution, entry point, compatibility range, and a remediation hint. Upgrade, remove, or pin the plugin before planning a study.

### Name collision

Names are normalized case-insensitively and treat `.`, `_`, and `-` as the same separator. Two distributions cannot claim the same normalized name, and plugins cannot shadow built-ins. Rename one entry point with an organization prefix.

### Plugin implementation hash changes unexpectedly

Editable installs and local source edits change plugin provenance. Build and install a pinned wheel in the experiment environment. Record the environment lock next to the evidence bundle.

## Asking for help

Before opening an issue, collect:

- `scaffoldscope --version`;
- Python version and host OS;
- the command and complete error text;
- whether the provider, Docker backend, or a plugin is involved;
- a minimal redacted configuration and task fixture when possible; and
- `scaffoldscope check` output for evidence problems.

Do not post API keys, private source, full traces, or confidential evaluator output. Use the private process in [SECURITY.md](https://github.com/satwiksps/scaffoldscope/blob/main/SECURITY.md) for vulnerabilities.
