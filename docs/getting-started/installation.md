# Installation

## Install from PyPI

Create an isolated environment, then install the current release:

::::{tab-set}
:::{tab-item} Linux and macOS
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install scaffoldscope
scaffoldscope --version
```
:::

:::{tab-item} Windows PowerShell
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install scaffoldscope
scaffoldscope --version
```
:::
::::

ScaffoldScope has no required third-party runtime dependencies. The wheel includes the CLI, JSON Schema, offline demo, starter assets, Docker backend, and plugin API.

## Install an exact release

Pin a version in automated workers:

```bash
python -m pip install "scaffoldscope==0.3.1"
```

For a stronger supply-chain policy, download the wheel from the [GitHub release](https://github.com/satwiksps/scaffoldscope/releases), verify it against `SHA256SUMS`, and install the local file:

```bash
python -m pip install ./scaffoldscope-0.3.1-py3-none-any.whl
```

## Install from source

Use an editable install only when developing ScaffoldScope or a plugin against the current branch:

```bash
git clone https://github.com/satwiksps/scaffoldscope.git
cd scaffoldscope
python -m pip install -e .
```

The `main` branch can contain unreleased changes. Use a tagged release for a reproducible experiment environment.

## Verify the installation

Run the local readiness check:

```bash
scaffoldscope doctor
```

Then inspect the command surface and packaged schema:

```bash
scaffoldscope --help
scaffoldscope schema --out experiment.schema.json
```

`doctor` without a config checks the installed package and local capabilities. `doctor --config experiment.json` additionally checks the selected provider, credential status, plugin compatibility, sandbox backend, and Docker image when applicable.

## Upgrade or remove

```bash
python -m pip install --upgrade scaffoldscope
python -m pip uninstall scaffoldscope
```

An upgrade does not alter existing experiment directories. Resume checks compare persisted package, implementation, configuration, task, plugin, and runtime identity before reusing a result.

## Supported environments

| Component | Supported or expected |
|---|---|
| Python | CPython 3.10 through 3.14 |
| Host OS | Linux, macOS, Windows |
| Local evaluator | Trusted fixtures and controlled local repositories |
| Docker evaluator | Linux containers through Docker Engine or Docker Desktop |
| Provider | Bundled scripted provider, OpenAI-compatible chat completions endpoint, or a plugin |

If the command is not found after installation, confirm that the virtual environment is active and run `python -m scaffoldscope --version`. See [Troubleshooting](../troubleshooting.md) for platform-specific checks.
