## Summary

<!-- What changed, and why is this the smallest useful change? -->

## Validation

<!-- List exact commands, task panels, seeds, and relevant output. -->

- [ ] `python -m ruff format --check src tests`
- [ ] `python -m ruff check src tests`
- [ ] `python -m mypy --platform linux`
- [ ] `python -m mypy --platform win32`
- [ ] `python -m mypy --platform darwin`
- [ ] `python -m pytest --cov=scaffoldscope --cov-report=term-missing`
- [ ] `python -m build`
- [ ] `python -m twine check --strict dist/*`
- [ ] `npm --prefix site ci --ignore-scripts`
- [ ] `npm --prefix site run check`
- [ ] `npm --prefix site run build`
- [ ] Documentation and examples were updated when behavior changed.

## Experiment integrity

- [ ] This change does not silently alter pinned prompts, tools, budgets, retry policy, task selection, or model settings.
- [ ] Any intentional measurement change is declared in the config/schema and recorded in result artifacts.
- [ ] New or changed traces contain no credentials, proprietary code, or sensitive task data.
- [ ] The offline demo remains clearly labeled as an engine test, not model-performance evidence.

## Release note

<!-- User-visible impact, migration notes, or "Not user-visible". -->
