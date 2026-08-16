# Changelog

This file records user-visible changes to ScaffoldScope. The project follows
[Semantic Versioning](https://semver.org/); unreleased work belongs under
`Unreleased` and release entries are never rewritten after publication.

## [Unreleased]

### Changed

- The README and project website now use the published PyPI package in their
  quickstart instructions.
- Website copy and metadata are shorter. Social assets and Codecov reporting are
  included.
- The README now has clearer workflow guidance, artifact documentation, current
  branch links, and focused project badges.
- Repository maintenance adds an explicit Codecov policy and code ownership.
- The Apache-2.0 license appendix uses the canonical upstream placeholder instead
  of a stale project name.
- Python source distributions exclude website files, repository workflows, and
  promotional images that are not used by the installed package.

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

[Unreleased]: https://github.com/satwiksps/scaffoldscope/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/satwiksps/scaffoldscope/releases/tag/v0.3.1
