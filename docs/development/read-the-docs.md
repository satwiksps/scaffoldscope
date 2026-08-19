# Read the Docs hosting

The public documentation is built by Read the Docs Community from this repository. Build behavior lives in `.readthedocs.yaml`; local dashboard settings must not replace repository configuration.

## One-time project import

An administrator performs this step in the Read the Docs web interface:

1. Sign in to [Read the Docs](https://app.readthedocs.org/) with the GitHub account that administers `satwiksps/scaffoldscope`.
2. Install or authorize the Read the Docs GitHub App for this repository.
3. Select **Add project**, choose `satwiksps/scaffoldscope`, and continue.
4. Use project name and slug `scaffoldscope` if available.
5. Keep the default branch as `main` and the documentation language as English.
6. Confirm that `.readthedocs.yaml` exists at the repository root.
7. Start the first build.

Automatic GitHub App import configures repository access, build status reporting, and push-triggered builds. A manual import requires separate webhook configuration and should be used only when GitHub App installation is unavailable.

Official references:

- [Adding a documentation project](https://docs.readthedocs.com/platform/stable/intro/add-project.html)
- [Git integration](https://docs.readthedocs.com/platform/stable/reference/git-integration.html)
- [Configuration file v2](https://docs.readthedocs.com/platform/stable/config-file/v2.html)

## Repository build contract

`.readthedocs.yaml` pins:

- the v2 configuration schema;
- Ubuntu 24.04 and Python 3.13;
- `docs/conf.py` as the Sphinx configuration;
- warnings as build failures;
- the exact packages in `docs/requirements.txt`;
- an editable install of ScaffoldScope so CLI and API references match the built revision; and
- HTML, downloadable HTML ZIP, and PDF output.

Read the Docs creates a clean environment for every version. No generated documentation belongs in Git.

## Version policy

| Read the Docs version | Source | Purpose |
|---|---|---|
| `latest` | `main` | Current development documentation |
| `stable` | Latest supported release tag | Default release documentation when configured |
| `vX.Y.Z` | Immutable Git tag | Documentation matching one published package release |

Read the Docs discovers branches and tags but leaves new versions inactive by default. After a release tag is built successfully, activate it in the project version settings and update `stable`. Keep old release documentation available unless it contains a security or legal problem that requires removal.

Do not point `stable` at `main`. Development documentation can describe behavior that is not on PyPI yet.

## Pull request builds

Enable pull request builds after the GitHub App is connected. A documentation pull request should build its exact commit and report status to GitHub. The repository CI job remains required because it gives contributors the same warnings-as-errors Sphinx check without depending on Read the Docs availability.

## Local parity

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install -r docs/requirements.txt
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

On Windows PowerShell, activate with `.\.venv\Scripts\Activate.ps1`. The output is `docs/_build/html`.

Run the external link checker before changing navigation or canonical URLs:

```bash
python -m sphinx -W --keep-going -b linkcheck docs docs/_build/linkcheck
```

## Build failures

### Configuration rejected before installation

Validate that `.readthedocs.yaml` uses only fields supported by configuration version 2. Read the Docs rejects unknown keys. Do not work around an invalid file with dashboard overrides.

### Dependency installation fails

Reproduce in a clean Python 3.13 environment using `docs/requirements.txt`. Keep versions pinned and update them together after a successful local HTML, link, and LaTeX-source build.

### Sphinx warning fails the build

Warnings are errors by design. Fix missing references, duplicate labels, unknown documents, invalid API targets, or malformed markup. Add a narrow suppression only when the warning is understood and unavoidable.

### API documentation differs from the release

Check the selected Read the Docs version. The API page imports ScaffoldScope from the exact source revision being built. `latest` can differ from the installed PyPI release; `vX.Y.Z` must match that tag.

### A tag does not appear in the version switcher

Confirm the GitHub integration received the tag, refresh versions in the Read the Docs dashboard, activate the tag, and complete a successful build. Newly discovered tags are inactive by default.

## Canonical URLs

The root documentation URL is `https://scaffoldscope.readthedocs.io/`. Read the Docs supplies the canonical URL for each build through `READTHEDOCS_CANONICAL_URL`, and `docs/conf.py` uses it for Open Graph and canonical metadata. Repository, PyPI, README, and website links should point to the canonical documentation host rather than GitHub-rendered Markdown.
