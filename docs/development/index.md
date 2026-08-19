# Development

ScaffoldScope is an Apache-2.0 open source project. Changes to its protocol, evidence, or analysis can alter scientific conclusions, so contribution review treats those contracts as carefully as runtime correctness.

## Set up a development environment

```bash
git clone https://github.com/satwiksps/scaffoldscope.git
cd scaffoldscope
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pip install -r docs/requirements.txt
pre-commit install
```

Read [CONTRIBUTING.md](https://github.com/satwiksps/scaffoldscope/blob/main/CONTRIBUTING.md) before submitting a change. Protocol and persisted-evidence changes require an RFC issue before implementation.

## Repository map

| Path | Purpose |
|---|---|
| `src/scaffoldscope/agent.py` | Agent loop and usage ledger |
| `src/scaffoldscope/context.py` | Canonical trajectory and built-in context policies |
| `src/scaffoldscope/runner.py` | Planning, execution, resume, locking, and atomic result assembly |
| `src/scaffoldscope/sandbox.py` | Structured tools, workspace preparation, local evaluation, and patch capture |
| `src/scaffoldscope/docker_sandbox.py` | Docker evaluator boundary and preflight |
| `src/scaffoldscope/report.py` | Evidence checking and paired analysis |
| `src/scaffoldscope/bundle.py` | Deterministic evidence archives and verification |
| `src/scaffoldscope/plugins.py` | Public plugin discovery, loading, compatibility, and provenance |
| `src/scaffoldscope/schema.py` | Strict runtime configuration and experiment identity |
| `src/scaffoldscope/schemas/` | Exported JSON Schema |
| `tests/` | Unit, integration, adversarial integrity, and platform tests |
| `docs/` | Sphinx/MyST documentation source |
| `site/` | Independent Next.js landing site |

Read [Architecture](../architecture.md) before changing runtime boundaries.

## Run the project gates

```bash
python -m ruff format --check src tests
python -m ruff check src tests
python -m mypy --platform linux
python -m mypy --platform win32
python -m mypy --platform darwin
python -m pytest --cov=scaffoldscope --cov-report=term-missing
python -m build
python -m twine check --strict dist/*
python -m sphinx -W --keep-going -b html docs docs/_build/html
npm --prefix site run check
npm --prefix site run build
```

Use `python -m sphinx -W --keep-going -b linkcheck docs docs/_build/linkcheck` when changing external links. Network failures can be transient; inspect each result rather than disabling broad URL classes.

## Documentation style

- Lead with the behavior or decision an operator needs.
- Use exact command names, paths, statuses, and field names.
- Distinguish guarantees from recommendations and limitations.
- Do not copy marketing claims into technical reference pages.
- Avoid future promises in current-version documentation.
- Keep examples runnable or label placeholders explicitly.
- Add a cross-link instead of duplicating a contract in several chapters.
- Build with warnings treated as errors.

## Change the documentation

The source uses MyST Markdown plus a small reStructuredText API page. Build locally:

```bash
python -m pip install -r docs/requirements.txt
python -m pip install -e .
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

Open `docs/_build/html/index.html`. Read the Docs uses the pinned requirements and `.readthedocs.yaml` at the repository root.

## Release process

See [Release process](../releasing.md) for version alignment, signed tags, GitHub artifacts, PyPI publication, and post-release verification. Existing release tags are immutable.

Read [Read the Docs hosting](read-the-docs.md) before changing the documentation build or version policy.

## Community standards

- [Code of Conduct](https://github.com/satwiksps/scaffoldscope/blob/main/CODE_OF_CONDUCT.md)
- [Security policy](https://github.com/satwiksps/scaffoldscope/blob/main/SECURITY.md)
- [Governance](https://github.com/satwiksps/scaffoldscope/blob/main/GOVERNANCE.md)
- [License](https://github.com/satwiksps/scaffoldscope/blob/main/LICENSE)
- [Citation metadata](https://github.com/satwiksps/scaffoldscope/blob/main/CITATION.cff)

```{toctree}
:maxdepth: 1
:hidden:

../releasing
read-the-docs
```
