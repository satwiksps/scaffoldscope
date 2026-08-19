# ScaffoldScope

<div align="center">

![ScaffoldScope](https://raw.githubusercontent.com/satwiksps/scaffoldscope/main/docs/assets/scaffoldscope.svg)

Controlled experiments for coding-agent harnesses.

[Website](https://scaffoldscope.vercel.app) | [Documentation](https://scaffoldscope.readthedocs.io/) | [Install](#quickstart) | [How it works](#why-this-is-different) | [SWE-bench](#swe-bench)

[![CI](https://github.com/satwiksps/scaffoldscope/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/satwiksps/scaffoldscope/actions/workflows/ci.yml?query=branch%3Amain)
[![CodeQL](https://github.com/satwiksps/scaffoldscope/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/satwiksps/scaffoldscope/actions/workflows/codeql.yml?query=branch%3Amain)
[![Codecov](https://codecov.io/gh/satwiksps/scaffoldscope/graph/badge.svg?branch=main)](https://codecov.io/gh/satwiksps/scaffoldscope)
[![Documentation](https://readthedocs.org/projects/scaffoldscope/badge/?version=latest)](https://scaffoldscope.readthedocs.io/en/latest/)
[![PyPI](https://img.shields.io/pypi/v/scaffoldscope?logo=pypi&logoColor=white)](https://pypi.org/project/scaffoldscope/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/scaffoldscope?logo=python&logoColor=white)](https://pypi.org/project/scaffoldscope/)
[![License Apache-2.0](https://img.shields.io/pypi/l/scaffoldscope)](https://github.com/satwiksps/scaffoldscope/blob/main/LICENSE)

</div>

ScaffoldScope measures how coding-agent harness choices affect solve rate, token use, cost, and constraint retention. It holds the model, tasks, evaluator, and budget fixed while one scaffold treatment changes.

> Same model. Same tasks. Same budget. One scaffold mechanism changed.

It combines a readable Python agent, a paired experiment runner, and an evidence pipeline. Use it to compare context policies, tool surfaces, and treatment instructions; evaluate locally or in a locked-down Docker backend; export a complete SWE-bench matrix; and publish deterministic evidence archives.

![ScaffoldScope compares context policies under a fixed model and task set](https://raw.githubusercontent.com/satwiksps/scaffoldscope/main/docs/assets/github-social-preview.png)

## Quickstart

ScaffoldScope supports Python 3.10 through 3.14 on Linux, macOS, and Windows. Docker is optional and only required for container-isolated evaluation.

Install from PyPI:

```bash
python -m pip install scaffoldscope
scaffoldscope --version
```

To work from source instead:

```bash
git clone https://github.com/satwiksps/scaffoldscope.git
cd scaffoldscope
python -m pip install .
```

Then run the zero-cost local starter:

```bash
scaffoldscope init my-study --name my-study
scaffoldscope validate my-study/experiment.json
scaffoldscope budget my-study/experiment.json
scaffoldscope run my-study/experiment.json
```

The generated project is safe to rerun and includes a tiny repository, fixed tests, a task
manifest, and three context treatments. Its deterministic scripted provider costs nothing and
validates the core local workflow without pretending to measure model intelligence.

For the larger built-in engine demonstration:

```bash
scaffoldscope demo
```

### Choose a workflow

| Goal | Start here |
|---|---|
| Learn the experiment format without API cost | `scaffoldscope init my-study --name my-study` |
| Exercise the complete local engine | `scaffoldscope demo` |
| Run a real OpenAI-compatible model | [Real models](#real-models) |
| Evaluate untrusted repository code | [Docker evaluation](#docker-evaluation) |
| Compare treatments on SWE-bench | [SWE-bench](#swe-bench) |

## Why this is different

| Capability | What ScaffoldScope does |
|---|---|
| Paired design | Can deterministically randomize treatment order inside each task and replicate block |
| Honest denominators | Keeps harness and protocol failures in intention-to-treat results while separating infrastructure-invalid trials |
| Resume integrity | Hashes config, implementation, plugin code, task source, and runtime identity before reusing a trial |
| Auditable context | Preserves the canonical trajectory; every derived view records retained and dropped source IDs |
| Cost provenance | Separates provider usage from estimates, cache reads/writes, retries, and incomplete ledgers |
| Governance metrics | Reports lexical constraint availability, behavioral checks, and governed solves separately |
| Evidence portability | Produces reports, raw traces, patches, immutable evaluator overlays, and deterministic ZIP bundles |
| Safe extension | Loads versioned entry-point plugins lazily and fingerprints their implementation |

The bundled scripted experiments are workflow tests only. ScaffoldScope withholds intervals for
scripted runs and panels below 10 tasks. It labels a comparison inferentially ready only when it is
the preregistered primary contrast, covers at least 20 tasks, and has at least 98% pair coverage.

## What it measures

### Context management

| Policy | Trigger | Mechanism |
|---|---|---|
| `none` | Never | Full canonical history until a typed overflow |
| `reactive` | Utilization threshold | Deterministic salient summary plus recent atomic bundles |
| `periodic` | Every *k* turns | Fixed-cadence compaction with emergency pressure handling |
| `selective` | Utilization threshold | Budgeted 0/1 selection scored by recency, references, subgoals, errors, task relevance, and constraints |

Assistant action and tool-result messages form atomic bundles: a policy keeps or drops the pair, never half of it.

### Tool and instruction treatments

Each variant can expose an exact subset of the built-in tools and append treatment-specific instructions:

```json
{
  "id": "symbol-first",
  "policy": "selective",
  "tools": ["list_files", "read_file", "search_symbols", "replace", "run_tests"],
  "instructions": "Use symbol search before opening broad files."
}
```

Available tools are `list_files`, `read_file`, `search`, `search_symbols`, `replace`, `write_file`, and `run_tests`. There is no model-controlled arbitrary shell.

## A complete operator loop

```bash
# Freeze and inspect the matrix before any provider call
scaffoldscope schema --out experiment.schema.json
scaffoldscope validate experiment.json
scaffoldscope doctor --config experiment.json
scaffoldscope budget experiment.json
scaffoldscope plan experiment.json

# Execute or safely resume
scaffoldscope run experiment.json
scaffoldscope status runs/my-study-abc12345
scaffoldscope trials runs/my-study-abc12345 --jsonl

# Inspect one trace without invoking a model or a tool
scaffoldscope replay runs/my-study-abc12345 <trial-id>

# Rebuild and verify publication artifacts
scaffoldscope report runs/my-study-abc12345
scaffoldscope check runs/my-study-abc12345
scaffoldscope bundle runs/my-study-abc12345 --out my-study-evidence.zip
scaffoldscope verify-bundle my-study-evidence.zip
```

Every trial owns its workspace and artifacts. Parallel workers never append to a shared result file, and an OS-released lock excludes a second experiment writer. Matching completed trials resume without another model call; identity drift is rejected instead of silently mixing evidence.

### What a run writes

| Artifact | Purpose |
|---|---|
| `manifest.json` and `config.resolved.json` | Frozen experiment identity and resolved configuration |
| `plan.jsonl` | Complete task, treatment, replicate, and execution-order matrix |
| `episodes.jsonl` | One durable result row per planned trial |
| `trials/<trial-id>/events.jsonl` | Ordered model, context, tool, evaluation, and lifecycle events |
| `trials/<trial-id>/patch.diff` | Exact repository change produced by the trial |
| `summary.json`, `report.md`, `report.html` | Machine-readable and human-readable analysis |
| Evidence ZIP | Workspace-free archive with checksums and integrity metadata |

## Real models

The built-in adapter targets OpenAI-compatible `/chat/completions` APIs. Start from a generated project so its task and workspace paths remain valid, then replace only the `model` object in `real-model-study/experiment.json`. Pin an immutable model revision whenever the provider exposes one:

```json
{
  "provider": "openai_compatible",
  "name": "pin-an-exact-model-revision",
  "base_url": "https://your-provider.example/v1",
  "api_key_env": "OPENAI_API_KEY",
  "requires_api_key": true,
  "context_window_tokens": 32768,
  "max_output_tokens": 2048,
  "json_mode": true
}
```

```bash
scaffoldscope init real-model-study --name real-model-study
# Edit real-model-study/experiment.json and configure its model object.
export OPENAI_API_KEY="..."
scaffoldscope validate real-model-study/experiment.json
scaffoldscope doctor --config real-model-study/experiment.json
scaffoldscope budget real-model-study/experiment.json
scaffoldscope plan real-model-study/experiment.json
scaffoldscope run real-model-study/experiment.json
```

`examples/openai-compatible.example.json` shows the complete optional model and pricing fields. It is a reference template, not a runnable experiment by itself: its provider URL and model revision are placeholders, and task paths are resolved relative to the configuration file.

Local Ollama or vLLM endpoints can explicitly disable authentication:

```json
{
  "provider": "openai_compatible",
  "name": "local-model-revision",
  "base_url": "http://127.0.0.1:11434/v1",
  "requires_api_key": false,
  "context_window_tokens": 32768
}
```

Remote endpoints carrying a key must use HTTPS. Provider-specific adapters can be installed as plugins.

## Docker evaluation

`LocalSandbox` is designed for trusted fixtures; it is not an OS security boundary. For untrusted repository tests, select the Docker backend with a locally available digest-pinned image:

```json
{
  "sandbox": {
    "backend": "docker",
    "test_timeout_seconds": 120,
    "docker": {
      "image": "python@sha256:<64-hex-digest>",
      "platform": "linux/amd64",
      "cpus": 2,
      "memory_bytes": 2147483648,
      "pids_limit": 256
    }
  }
}
```

The backend never pulls during a run. It preflights the exact local image, disables networking, runs as non-root, drops all capabilities, uses a read-only root, protects evaluator files, scrubs harness credentials, and applies CPU, memory, process, file-descriptor, output, and timeout limits. Read the [Docker threat model and setup guide](https://scaffoldscope.readthedocs.io/en/latest/docker.html).

## SWE-bench

ScaffoldScope generates patches; the official SWE-bench harness remains the correctness authority.

```bash
scaffoldscope import-swebench swe-bench-lite.json \
  --repo-cache /bench/repos \
  --out tasks/swe-bench-lite.jsonl

scaffoldscope run experiments/swe-bench-lite.json
scaffoldscope export-swebench-matrix runs/lite-ablation-abc12345 \
  --out-dir evaluator-matrix \
  --dataset-name SWE-bench/SWE-bench_Lite
```

The matrix contains one prediction file and a unique evaluator run ID for every treatment and replicate cell, plus a pinned runbook and checksums. After official grading, attach each cell as an immutable overlay:

```bash
scaffoldscope ingest-swebench runs/lite-ablation-abc12345 official-results.json \
  --strategy selective --replicate 1729 \
  --evaluator-version <commit> \
  --evaluator-run-id <unique-run-id> \
  --image-set-digest <image-manifest-digest>
```

See the [SWE-bench workflow](https://scaffoldscope.readthedocs.io/en/latest/swebench.html) for cache hazards, evaluation commands, and interpretation limits.

## Extensions

Context policies and model providers use normal Python entry points:

```bash
scaffoldscope plugins
scaffoldscope plugins --check
```

Discovery is deterministic. Built-in names cannot be shadowed, compatibility ranges are checked, plugin options are passed through a typed request, and loaded implementation files are hashed into experiment identity. Start with the [extension contract](https://scaffoldscope.readthedocs.io/en/latest/extensions.html) and the [standalone example plugin](https://github.com/satwiksps/scaffoldscope/tree/main/examples/plugins/pinned-tail).

## Reports and evidence

Reports keep the trade-offs visible instead of compressing them into one score:

- Solve rate, governed solve rate, and paired wins/losses/ties.
- Task-cluster bootstrap intervals and a paired sign-flip test when inference is defensible.
- Uncached input, cache-read, cache-write, output, reasoning, and total-token summaries.
- Configured-price estimates, model/tool/wall time, and incomplete usage disclosure.
- Context pressure, compaction exposure, compression ratio, and selection decisions.
- Lexical constraint availability and machine-checkable behavioral adherence.
- Infrastructure, harness, overflow, turn, token, cost, and evaluator failure rates.

The deterministic evidence bundle excludes mutable workspaces, retains archived attempt evidence, and includes a SHA-256 manifest. Raw traces can still contain source code and prompts; review them before publication.

## Scientific guardrails

- Replicates are nested within tasks; they do not inflate the independent task count.
- A primary comparison should be selected before evaluation. Other contrasts are descriptive.
- Budget reports disclose prospective minimum detectable effect and small-panel risk.
- Imported SWE-bench tasks remain pending until official evaluator results are ingested.
- Provider model/fingerprint drift, duplicate trajectories, estimated usage, incomplete pairing, and low treatment exposure produce explicit warnings.
- Absolute benchmark scores can be contaminated; the design supports relative within-model claims, not immunity from contamination.

Read the [experiment-design contract](https://scaffoldscope.readthedocs.io/en/latest/experiment-design.html) before spending API budget.

## Documentation

The complete, versioned manual is at [scaffoldscope.readthedocs.io](https://scaffoldscope.readthedocs.io/). Start with:

- [Installation](https://scaffoldscope.readthedocs.io/en/latest/getting-started/installation.html)
- [Zero-cost quickstart](https://scaffoldscope.readthedocs.io/en/latest/getting-started/quickstart.html)
- [Your first real experiment](https://scaffoldscope.readthedocs.io/en/latest/getting-started/first-experiment.html)
- [Operator guide](https://scaffoldscope.readthedocs.io/en/latest/operator-guide.html)
- [CLI reference](https://scaffoldscope.readthedocs.io/en/latest/reference/cli.html)
- [Configuration reference](https://scaffoldscope.readthedocs.io/en/latest/configuration.html)
- [Troubleshooting](https://scaffoldscope.readthedocs.io/en/latest/troubleshooting.html)

## Contributing

The highest-value contributions are falsifiable and reproducible: a mechanism with deterministic accounting tests, a safety invariant, a complete result bundle including failures, or a protocol RFC identifying one confound.

Read [CONTRIBUTING.md](https://github.com/satwiksps/scaffoldscope/blob/main/CONTRIBUTING.md), [GOVERNANCE.md](https://github.com/satwiksps/scaffoldscope/blob/main/GOVERNANCE.md), and [CODE_OF_CONDUCT.md](https://github.com/satwiksps/scaffoldscope/blob/main/CODE_OF_CONDUCT.md). Security reports follow [SECURITY.md](https://github.com/satwiksps/scaffoldscope/blob/main/SECURITY.md).

## Getting help

- Use [GitHub Discussions](https://github.com/satwiksps/scaffoldscope/discussions) for setup and experiment-design questions.
- Open an [issue](https://github.com/satwiksps/scaffoldscope/issues/new/choose) for reproducible defects or focused feature requests.
- Report vulnerabilities through [private vulnerability reporting](https://github.com/satwiksps/scaffoldscope/security/advisories/new), not a public issue.

## Maturity, license, and citation

ScaffoldScope 0.3 is an alpha research instrument with a tested core evidence contract. The scripted demo tests the workflow; it does not measure model performance.

Apache-2.0. See [LICENSE](https://github.com/satwiksps/scaffoldscope/blob/main/LICENSE) and [NOTICE](https://github.com/satwiksps/scaffoldscope/blob/main/NOTICE). If ScaffoldScope supports published work, cite the archived release via [CITATION.cff](https://github.com/satwiksps/scaffoldscope/blob/main/CITATION.cff) and include the config hash, evaluator revision, and evidence-bundle checksum.
