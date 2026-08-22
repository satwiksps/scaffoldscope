# Changelog

This file records user-visible changes to ScaffoldScope. The project follows
[Semantic Versioning](https://semver.org/); unreleased work belongs under
`Unreleased` and release entries are never rewritten after publication.

## [Unreleased]

## [1.0.0] - 2026-08-22

### Changed

- Harness and evaluator errors are infrastructure-invalid and excluded from solve
  denominators. Model and protocol outcomes remain in intention-to-treat results.
- Built-in policies and the scripted provider reject configuration fields they do
  not use.
- `doctor` reports `preflight_passed` and explicitly marks provider connectivity as
  `not-checked`; it does not send a model request.
- Python support is bounded to 3.10 through 3.14. CI covers every supported
  Python/operating-system combination and runs a real Docker trial on Linux.
- Configuration schema 1, evidence schema 2, and plugin API 1 are supported for
  the 1.x release line.

### Fixed

- Model failures can no longer be recorded as solved, and final integrity checks
  run before reports are written.
- Malformed SWE-bench inputs, invalid manifests, missing checkouts, and unknown
  trial filters now fail with actionable errors.
- Evidence bundles are replaced atomically; failed writes do not leave partial
  archives.
- Trial workspaces and temporary homes are removed after execution. Missing-path
  CLI failures no longer leave locks or marker files.
- CLI output remains readable under restricted Windows encodings.

### Migration

- Consumers of `doctor` JSON must replace `ready` with `preflight_passed`.
- Plugins compatible with core 1.x should declare an exclusive upper bound of
  `2.0.0`. Plugin API version remains 1.
- No configuration or evidence-schema migration is required.

## [0.3.1] - 2026-08-16

Initial public release from the current repository. Version `0.3.1` is used
because GitHub's immutable-release protection prevents reuse of a release tag
from the deleted predecessor repository.

### Added

- A readable, zero-runtime-dependency Python harness for paired coding-agent
  ablations across context policies, tool surfaces, and treatment instructions.
- Four built-in context treatments: no compaction, reactive summarization,
  periodic summarization, and budget-aware selective retention.
- Deterministic planning, hard experiment budgets, resumable trials, provider
  usage provenance, and intention-to-treat reporting.
- Local and network-disabled Docker execution backends with recorded runtime,
  task-source, implementation, plugin, and image provenance.
- SWE-bench import, full-matrix export, and immutable official-evaluation
  overlays without rewriting raw generation results.
- Integrity-checked traces, patches, reports, and deterministic evidence bundles
  with redaction-aware context commitments.
- A starter project, a zero-cost scripted demonstration, a versioned plugin API,
  operator documentation, and a statically rendered Next.js project website.

### Analysis contract

- Summary schema version 2 separates infrastructure-valid generation accounting
  from evaluator-valid outcome analysis, so pending external evaluations retain
  their token, cost, latency, and context-exposure records.
- Inferential labels require a preregistered primary contrast, at least 20
  independent tasks, and at least 98% pair coverage. Scripted runs and panels
  below 10 tasks do not receive intervals.

### Known limits

- ScaffoldScope 0.3 is an alpha research instrument, not a security boundary or
  a guarantee that a benchmark is uncontaminated.
- The built-in scripted demonstration checks the workflow; it is not evidence of
  model capability.
- Imported SWE-bench generations remain outcome-pending until official evaluator
  results are ingested.

[Unreleased]: https://github.com/satwiksps/scaffoldscope/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/satwiksps/scaffoldscope/releases/tag/v1.0.0
[0.3.1]: https://github.com/satwiksps/scaffoldscope/releases/tag/v0.3.1
