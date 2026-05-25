# Result bundle and schema

Versioned evidence objects—manifest, plan row, event, per-trial result, analysis summary, external
evaluation overlay, and bundle manifest—include `schema_version`. Resolved user configuration and
the separately hashed pricing snapshot are provenance inputs rather than versioned evidence
objects. The persisted experiment, plan, event, and per-trial result contracts remain at v1 and
are append-only within a protocol release.

## Analysis summary v2

`summary.json` uses schema v2. It separates two validity populations:

- Outcome fields such as solve rate, governed solve rate, behavioral adherence, and paired outcome
  comparisons use analysis-valid episodes. This includes intention-to-treat harness failures as
  solve failures; ordinary generated episodes require a completed evaluator outcome.
- Resource, context, and provider-provenance fields use every infrastructure-valid generation,
  including episodes awaiting an external evaluator outcome. Strategy rows expose this denominator
  as `infrastructure_valid_attempts`; `valid_attempts` remains the analysis-valid outcome count.

Each treatment-versus-baseline comparison constructs its own pair set. An incomplete unrelated
treatment cannot remove an otherwise complete treatment/baseline pair. Top-level `pair_coverage`
continues to measure complete all-treatment cells, so operators can still audit the full factorial
panel; each comparison also exposes its contrast-specific `pair_coverage`.

Consumers of v1 summaries should accept the new strategy field and use `schema_version` to select
the v2 denominator rules. This change does not alter persisted episode records or their v1 schema.
Summary v2 records `resampling_algorithm: sha256-counter-v1`; bootstrap and approximate sign-flip
draws come from that project-owned SHA-256 counter stream rather than an interpreter RNG.
Each strategy also exposes a `token_ledger` with count, mean, median, p90, and total summaries for
uncached input, total input, cache-read, cache-write, output, reasoning, and total tokens. Provider
usage sources and incomplete-ledger counts remain separate so locally estimated tokens are not
mistaken for provider billing records.

## Trial identity

`trial_hash` is SHA-256 over canonical JSON containing:

- `config_hash`
- `task_id`
- `variant_id`
- `replicate`

`trial_id` includes readable fields plus an eight-character digest. A completed result resumes only
when the hash matches.

Randomized treatment order uses the manifest-declared `sha256-rank-v1` algorithm: each treatment
is ranked by a canonical SHA-256 digest of the config hash, task, replicate, and treatment ID. This
project-owned ordering is stable across supported Python versions and is reconstructed by
`scaffoldscope check`.

## Manifest integrity profile

New manifests declare `integrity_version: 1`. This additive marker is the durable selector for the
exact deterministic plan-grid check, the `config.resolved.json` content hash, and runtime provenance
checks. Evidence recorded by ScaffoldScope 0.3 or newer is also required to carry the marker, so
deleting it cannot downgrade validation. Genuine legacy manifests remain readable under their
original structural checks.

An offline `plan` leaves `runtime_identity` as `null`, so a reviewed plan can be moved to its intended
worker. The first real execution pins the Python implementation/version, operating system, machine,
and token-counter identity before any trial starts. It can be backfilled only while no trial result
exists; later resumes must match it exactly.

The manifest and frozen resolved configuration also carry exact `task_toolsets`,
`task_constraints`, and `task_provenance` maps. These bind each result's effective tools, standing
constraint ID/text pairs, repository, base commit, and canonical source-tree digest to the frozen
experiment. Constraint rows retain canonically redacted text, its original SHA-256 commitment, and
whether redaction occurred. Model requests likewise commit to the original message list and record
whether the portable trace contains a redacted view. The integrity checker recomputes tokens and
lexical constraint availability whenever request and constraint text are unredacted; otherwise it
binds the committed pre-redaction observations while still reconstructing append-only message IDs
and atomic assistant/tool bundles. The profile also binds provider seed support, the declared sandbox
configuration, and observed Docker identity when applicable; portable checks do not have to guess
from missing private task manifests.

## `events.jsonl`

Each line contains:

```json
{
  "schema_version": 1,
  "sequence": 1,
  "timestamp": "RFC-3339 UTC",
  "type": "event_name",
  "payload": {}
}
```

Events cover trial start, agent start, context decisions, full model requests, provider responses, tool results, errors, evaluation, and terminal outcome. Common credential shapes are redacted before writing; the corresponding agent, evaluator, and error payloads in `result.json` use the same canonical redacted representation. Redaction is best effort, and private source may still be present in traces or patches.

## `result.json` and `episodes.jsonl`

The per-trial result contains:

- Protocol, package, experiment, config, trial, task, variant, replicate, and model identity.
- Order position inside the paired block.
- Infrastructure validity and terminal status.
- Solve and governed-solve booleans.
- Start/finish timestamps and wall duration.
- Full agent ledger and compaction decisions.
- Effective provider model/fingerprint sets, usage provenance, and a model-response trajectory hash for detecting ineffective replicates.
- Evaluator output and deterministic constraint checks.
- Patch digest, byte count, and artifact paths.
- Pinned host runtime identity matching the manifest.
- Sandbox backend identity. Docker episodes include the declared image, resolved immutable image ID,
  and observed platform; the manifest retains the full preflight record and its canonical hash.

`episodes.jsonl` is rebuilt from per-trial atomic results in plan order. Workers never append to it concurrently.

The usage ledger includes a `complete` flag and source labels. A failed or retried provider call without usage marks the ledger incomplete; configured-price total cost becomes `null` because the provider may have billed unreported work.

## Terminal statuses

- `resolved`
- `unresolved`
- `context_overflow`
- `turn_limit`
- `token_limit`
- `cost_limit`
- `cost_unobservable`
- `model_error`
- `infrastructure_error`
- `harness_error`
- `awaiting_external_evaluation`
- `external_evaluation_incomplete`

Harness/policy exceptions are intent-to-treat failures (`harness_error`), so a broken variant cannot improve its score by excluding itself. Exogenous workspace/evaluator failures are infrastructure-invalid. Imported SWE-bench episodes remain pending until an immutable external-evaluation overlay is ingested. Model/provider incidents should be classified by a preregistered rule; never recode outcomes after seeing which strategy benefits.

## Integrity check

`scaffoldscope check EXPERIMENT_DIR` verifies required files, unique planned/recorded trial identity, config hashes, aggregate/per-trial equality, artifact containment, trace/patch hashes, and terminal trace events. `--strict` also fails on report warnings; the offline demo intentionally fails strict mode because it is scripted and underpowered.
