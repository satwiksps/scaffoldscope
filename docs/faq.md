# Frequently asked questions

## What problem does ScaffoldScope solve?

It measures the effect of a coding-agent harness treatment while keeping declared model, task, evaluator, budget, and replicate conditions fixed. It also preserves enough evidence to audit the comparison.

## Is ScaffoldScope a production coding assistant?

No. It is an experiment runner and readable reference harness. Its agent edits isolated task workspaces to produce benchmark or research evidence.

## Is it another model leaderboard?

No. Cross-model comparison is not its primary design. The core unit is a within-model, paired treatment comparison.

## Why use a weak or inexpensive model?

A saturated model can solve almost every task under every treatment, leaving no signal. A cheaper model with headroom can make harness differences easier to observe and lets you afford repeated paired cells. The chosen model still needs to follow the action protocol reliably.

## Why are replicates nested within tasks?

Repeated model samples reduce within-task randomness, but they do not create new independent tasks. ScaffoldScope averages replicates inside tasks and resamples task clusters for the primary interval.

## Does a replicate always set a provider seed?

No. The provider seed is sent only when the provider is configured and documented to support it. The replicate still identifies a paired repeated block. Reports disclose seed support and duplicate model trajectories.

## Which context policy should I use?

Use `none` as a control when the model can reach context pressure. Compare a policy whose trigger and target are appropriate for the declared window. A policy that rarely activates cannot explain a performance difference through compaction.

## Can one experiment vary more than context management?

Yes. A variant can also declare an exact built-in tool subset and append treatment-specific instructions. Keep one causal question per study. Multiple simultaneous changes make attribution difficult.

## Does ScaffoldScope use provider-native tokenization?

The harness uses the explicit provider-independent `char4-v1` estimate for pre-call context and hard-budget decisions. Provider-reported usage is retained separately and is preferred for post-run token accounting. Provider invoices remain the billing authority.

## Are cost caps exact?

Before a call, ScaffoldScope reserves estimated input and maximum output against the configured token and price budget. It will not begin a call whose worst case exceeds the remaining declared cap. A complete monetary cap needs configured input and output prices. Provider retries or failures without usage can make observed cost incomplete, and a local estimate is not an invoice.

## Is the local sandbox secure for untrusted repositories?

No. It restricts the model's structured file and test interface, protects paths, scrubs subprocess environments, and operates on generated copies, but local test code still runs on the host. Use the Docker backend inside a disposable VM for hostile inputs.

## Why does the Docker backend never pull?

Pulling during a run changes network use, latency, and potentially image identity. Preload an immutable image on every worker. ScaffoldScope resolves it before trials and launches the resolved image ID.

## Can ScaffoldScope evaluate SWE-bench locally?

It can generate and export patches, but the official SWE-bench harness remains the correctness authority. Imported tasks stay pending until you ingest official results as immutable overlays.

## What does lexical constraint availability mean?

It means a declared constraint ID or normalized text was present in the model's active context at a recorded decision point. It does not prove the model understood or followed the constraint.

## What is governed completion?

A task is governed-complete when the evaluator resolves it and all configured deterministic behavioral constraint checks pass.

## Why are infrastructure and harness failures different?

An exogenous evaluator or workspace failure and a treatment implementation failure both make an episode infrastructure-invalid. Preserve either result as a missing cell and apply the incident policy declared before outcomes are viewed; do not selectively delete or recode failed rows.

## Can I run only one variant from a declared matrix?

No execution filter is provided. The plan is the experiment. Running selected cells creates selective missingness and weakens pairing. Use a separate development config when you need a smoke test.

## Can I edit `episodes.jsonl` or reports?

Do not edit raw or aggregate evidence. Reports are derived and can be regenerated. Bundling regenerates canonical reports from the frozen config. Manual changes to evidence break integrity checks.

## Can I publish the evidence ZIP safely?

The bundle excludes generated workspaces, but traces and patches can still contain source code, prompts, paths, tool output, or private data. Credential-pattern redaction is best effort. Review legal, privacy, benchmark, and repository-sharing obligations before publication.

## Can third parties add providers or policies?

Yes. Plugins use normal Python entry points, explicit versioned registration objects, compatibility bounds, typed factory requests, and implementation fingerprints. Installing a plugin authorizes its Python code to run, so pin and audit it like any trusted dependency.

## What is stable for plugin authors?

The symbols listed in the [Python extension API](reference/python-api.rst) and plugin API version 1 are the supported surface. Other modules are readable but internal unless documented otherwise.

## Why does `check --strict` fail on small studies?

Strict mode treats analysis warnings as errors. A small, scripted, incomplete, underpowered, provider-confounded, or weakly activated study can be internally valid without being publication-ready.

## Where should I report a bug or security issue?

Use [GitHub Issues](https://github.com/satwiksps/scaffoldscope/issues) for reproducible non-sensitive defects. Follow [SECURITY.md](https://github.com/satwiksps/scaffoldscope/blob/main/SECURITY.md) for private vulnerability reporting.
