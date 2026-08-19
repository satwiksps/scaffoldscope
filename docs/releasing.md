# Releasing ScaffoldScope

Releases are research-instrument snapshots. A release includes source, wheel, source distribution, checksums, a GitHub Release, and the same distributions on PyPI.

## One-time repository setup

1. Enable GitHub private vulnerability reporting, dependency alerts, secret scanning, push protection, and immutable releases.
2. Create the protected `pypi` environment in GitHub.
3. In PyPI, create a pending trusted publisher for owner `satwiksps`, repository `scaffoldscope`, workflow `release.yml`, and environment `pypi`.
4. Enable the repository variable `PYPI_PUBLISH` only after the trusted publisher exists.
5. Import the repository into Read the Docs with the official GitHub App, keep `main` as the
   default branch, and use the repository's `.readthedocs.yaml` without dashboard overrides.

## Release checklist

1. Start from a clean `main` after required CI has passed.
2. Select a Semantic Version and update `src/scaffoldscope/__init__.py`, `CHANGELOG.md`,
   `CITATION.cff`, `site/package.json`, `site/package-lock.json`, versioned README links,
   and the version-pinned changelog project URL together.
3. Confirm the changelog contains migration and evidence-schema notes when semantics changed.
4. Run the complete local gate:

   ```bash
   python -m ruff format --check src tests
   python -m ruff check src tests
   python -m mypy --platform linux
   python -m mypy --platform win32
   python -m mypy --platform darwin
   python -m pytest --cov=scaffoldscope --cov-report=term-missing
   python -m build
   python -m twine check --strict dist/*
   python -m pip install -r docs/requirements.txt
   python -m sphinx -W --keep-going -b html docs docs/_build/html

   cd site
   npm ci --ignore-scripts
   npm run check
   npm run build
   ```

5. Review the complete release diff on a clean `main`, normally through a release pull
   request, and require all protected-branch checks to pass. Do not tag an unreviewed
   working tree.
6. Create a signed annotated tag on the exact current `main` commit, then push it:

   ```bash
   git tag -s vX.Y.Z -m "release: ScaffoldScope X.Y.Z"
   git push origin vX.Y.Z
   ```

7. The release workflow verifies the tag signature and current-main target, fixes archive
   timestamps from the tagged commit via `SOURCE_DATE_EPOCH`, rebuilds and verifies the package,
   extracts curated notes from the matching changelog section, creates or safely resumes the draft
   GitHub Release, attaches checksummed distributions, and publishes to PyPI only when
   `PYPI_PUBLISH=true`. A rerun skips files already present on PyPI and then requires their
   public SHA-256 digests to match the rebuilt distributions.
8. Verify each GitHub artifact against `SHA256SUMS` and run the installed-wheel smoke test
   from the public artifact. When PyPI publishing is enabled, also verify that its files have
   the same SHA-256 digests.
9. In Read the Docs, activate the new `vX.Y.Z` tag after its build passes. Point the documented
   stable version at that release, keep `latest` on `main`, and verify the version switcher,
   PDF, and HTML ZIP downloads.

Never move, delete, or reuse a published tag. If a release is wrong, document it and publish a new patch version.
