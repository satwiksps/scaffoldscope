# Preregistration template

Copy this file into a study directory before pilot results are inspected. Replace every bracketed field and archive the completed protocol with the source config. This template is operational guidance, not a substitute for domain-specific statistical review.

## Study identity

- Title: `[short falsifiable question]`
- Protocol version and date: `[version, UTC date]`
- Owners and conflicts of interest: `[names and disclosures]`
- Development panel: `[manifest path/hash and selection rule]`
- Frozen reporting panel: `[manifest path/hash and selection rule]`

## Causal contrast

- Baseline variant: `[variant ID]`
- Primary comparison: `[one variant ID]`
- Mechanism changed: `[one precise scaffold component]`
- Mechanisms held fixed: `[model, prompt, tools, budgets, evaluator, retry policy, sandbox]`
- Expected interaction risks: `[model by task by treatment interactions]`

## Outcomes

- Primary outcome: `[for example, official solve rate]`
- Smallest effect of practical interest: `[absolute proportion]`
- Secondary outcomes: `[tokens, configured-price cost, latency, governed solve]`
- Diagnostic outcomes: `[treatment exposure, compression, lexical availability]`
- Claims that will not be made: `[absolute capability, invoice accuracy, semantic policy survival, etc.]`

## Power and panel

- Independent task count: `[N]`
- Replicates nested per task: `[IDs and rationale]`
- Prospective paired MDE: `[output from scaffoldscope budget]`
- Planned bootstrap draws and analysis seed: `[values]`
- Decision if MDE exceeds the SESOI: `[increase frozen panel or publish descriptive/inconclusive result]`

## Execution contract

- ScaffoldScope version/source revision: `[tag and commit]`
- Config hash after freeze: `[SHA-256]`
- Exact provider route and model revision: `[immutable identifiers]`
- Sampling and seed support: `[settings and provider evidence]`
- Turn/token/cost/time limits: `[all caps]`
- Docker image and platform: `[declared digest plus observed image ID]`
- Official evaluator revision and image-set digest: `[identifiers]`
- Unique evaluator run-ID scheme: `[or use export-swebench-matrix output]`

## Incident and denominator rules

- Harness/policy exceptions: `[normally intention-to-treat non-solves]`
- Provider errors and retries: `[predeclared retry and missing-usage treatment]`
- Infrastructure-invalid trials: `[classification and rerun rule before outcomes are viewed]`
- Official evaluator incomplete/error outcomes: `[pending, not silently excluded or scored]`
- Duplicate trajectories or unhonored seeds: `[disclosure and analysis rule]`
- Missing cells and stopping rule: `[when execution ends and what remains in denominator]`
- No post-outcome exclusions: `[explicit confirmation]`

## Analysis

- Pairing unit: `task_id + replicate`
- Resampling unit: `task_id`
- Confirmatory comparison: `[must equal experiment.primary_comparison]`
- Multiplicity rule for exploratory contrasts: `[descriptive or declared adjustment]`
- Sensitivity analyses: `[declared before reporting-panel results]`
- Contamination and generalization statement: `[task/model limitations]`

## Frozen procedure

```bash
scaffoldscope doctor --config experiment.json
scaffoldscope validate experiment.json
scaffoldscope budget experiment.json --json
scaffoldscope plan experiment.json
scaffoldscope run experiment.json
scaffoldscope status <experiment-dir> --json
scaffoldscope check <experiment-dir> --strict
scaffoldscope bundle <experiment-dir> --out <study>-evidence.zip
scaffoldscope verify-bundle <study>-evidence.zip
```

For SWE-bench, insert `export-swebench-matrix`, official evaluation, and one `ingest-swebench` command per cell before report/check/bundle.

## Publication checklist

- [ ] Protocol was frozen before reporting-panel outcomes were inspected.
- [ ] Complete config, plan, task IDs, task source hashes, and price snapshot are public.
- [ ] All cells, failures, retries, patches, traces, and evaluator overlays are retained.
- [ ] Report warnings are quoted and explained rather than removed.
- [ ] Configured-price estimates are not described as invoices.
- [ ] Lexical constraint availability is not described as semantic compliance.
- [ ] Scripted-provider results are described only as engine validation.
- [ ] Evidence ZIP passes `verify-bundle`; its checksum is in the release notes.
- [ ] At least one independent reproduction path is documented.
