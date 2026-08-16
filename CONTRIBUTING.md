# Contributing to ScaffoldScope

ScaffoldScope is a measurement instrument. A contribution is useful when another
person can understand what changed, reproduce it, and tell whether it altered the
experiment rather than merely the presentation.

## Before writing code

- Search existing issues and discussions.
- Use a bug report for incorrect behavior and a feature request for a bounded,
  backward-compatible addition.
- Open a Protocol RFC before changing prompts, task selection, evaluator
  semantics, treatment assignment, evidence schemas, governance metrics,
  licensing, or compatibility guarantees.
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

Small fixes do not need an issue first.

## Development setup

ScaffoldScope supports Python 3.10 and newer.

```bash
git clone https://github.com/satwiksps/scaffoldscope.git
cd scaffoldscope
python -m venv .venv
python -m pip install -e ".[dev]"
```

The website is an independent Next.js project under `site/`:

```bash
npm --prefix site ci --ignore-scripts
npm --prefix site run dev
```

## Make a focused change

Keep treatment differences explicit and preserve these invariants:

- The model, task, budget, seed policy, and evaluator stay fixed unless the
  experiment declares otherwise.
- Failures are retained; they are not removed to improve a result.
- Raw traces, patches, usage provenance, and evaluator overlays remain auditable.
- Credentials and private task data never enter fixtures, logs, or reports.
- A persisted evidence-contract change includes a schema/version decision and a
  migration note.

Add tests for behavior changes. Prefer small fixtures and deterministic providers
over paid or network-dependent tests.

## Local checks

Run the checks relevant to your change. Before requesting merge, the complete
project gate is:

```bash
python -m ruff format --check src tests
python -m ruff check src tests
python -m mypy --platform linux
python -m mypy --platform win32
python -m mypy --platform darwin
python -m pytest --cov=scaffoldscope --cov-report=term-missing
python -m build
python -m twine check --strict dist/*
npm --prefix site run check
npm --prefix site run build
```

Document exact commands and any intentional omissions in the pull request.

## Commits and pull requests

Use a short Conventional Commit subject such as `fix: preserve pending-run cost
accounting`. Keep refactors separate from protocol changes. Pull requests should
explain:

1. the concrete problem;
2. the experiment or evidence contract affected;
3. the implementation and compatibility impact;
4. the validation performed; and
5. the release-note or migration requirement.

Maintainers may ask for a smaller change when a patch combines unrelated policy,
runtime, and presentation work.

## License

By submitting a contribution, you agree that it may be distributed under the
project's [Apache License 2.0](LICENSE). Do not submit material that you cannot
license on those terms. Retain attribution and notices required by any compatible
third-party material.
