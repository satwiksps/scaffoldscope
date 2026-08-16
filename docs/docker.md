# Docker evaluator backend

The Docker backend runs the task manifest's fixed evaluator command in a short-lived Linux
container. It is the recommended backend for repository code that is not fully trusted.
ScaffoldScope uses the Docker CLI directly and adds no Docker SDK or other runtime dependency.

## Threat model

Treat the model-generated patch and all repository test code as untrusted. Treat the ScaffoldScope
process, experiment configuration, task manifest, selected image, Docker CLI, Docker daemon, and
host kernel as trusted infrastructure.

The model still uses ScaffoldScope's structured file tools against a generated per-trial workspace
on the host. It cannot choose an arbitrary command. When the model invokes `run_tests`, and again
during final evaluation, the trusted `test_command` argument vector from the task manifest runs in
Docker with `shell=false`.

The backend fixes these controls for every evaluator container:

| Control | Runtime behavior |
|---|---|
| Network | `--network=none` |
| Identity | Numeric non-root `UID:GID` |
| Linux privilege | `--cap-drop=ALL` and `no-new-privileges` |
| Root filesystem | Read-only, with a bounded writable `/tmp` tmpfs |
| Workspace | Only the generated trial workspace is bind-mounted at `/workspace` |
| Evaluator assets | `.git` and existing `protected_paths` are over-mounted read-only |
| Resources | CPU, memory, swap, PID, open-file, output, and wall-time limits |
| Process lifecycle | Init process, unique name, `--rm`, plus explicit kill/remove fallback |
| Container logging | Docker daemon log storage disabled; ScaffoldScope retains a bounded prefix |
| Reproducibility | Fixed hostname, locale, timezone, Python hash seed, and platform |

These controls materially reduce exposure; they do not make Docker an absolute security boundary.
A daemon running as root is a privileged service, containers share the host kernel, and runtime
vulnerabilities can escape a container. Use a maintained rootless Docker installation where
possible. For hostile inputs or public benchmark workers, place Docker inside a disposable VM and
destroy the VM after the run.

Never mount the Docker socket, credential directories, SSH agents, cloud credentials, or unrelated
host paths into the evaluator image. ScaffoldScope does not add those mounts.

## Prerequisites

- Python 3.10 or newer and a working Docker Engine or Docker Desktop CLI.
- Linux-container mode. The default evaluator platform is `linux/amd64`.
- A locally available image containing the task's interpreter, test runner, and dependencies.
- Enough Docker CPU, memory, disk, and concurrent-container capacity for `experiment.max_workers`.
- A numeric non-root user that can read the bind-mounted workspace and perform any writes the test
  suite legitimately needs.

On native Linux, bind mounts preserve host ownership. The safe default user, `65532:65532`, can
usually read normal source files but cannot write directories owned by another UID. If a suite must
build or write inside `/workspace`, configure a non-root UID:GID with appropriate permissions. For
example, the dedicated benchmark worker's UID:GID. Do not solve permission errors by switching to
`0:0`; root users are rejected.

Docker Desktop normally translates bind-mount permissions. Paths containing a comma are rejected
because the Docker `--mount` grammar cannot represent them unambiguously. When using a remote Docker
daemon, the workspace path must exist on the daemon host; a local path is not uploaded automatically.

## Build once, then pin the image

Install dependencies while building the image, not during evaluation: evaluator networking is
always disabled and runs use `--pull=never`.

```bash
docker build --platform linux/amd64 -t scaffoldscope-eval:2026-08-15 .
docker image inspect --format '{{json .RepoDigests}}' scaffoldscope-eval:2026-08-15
```

Images pushed to a registry normally report a repository digest such as:

```text
ghcr.io/example/scaffoldscope-eval@sha256:<64 hexadecimal characters>
```

Use that complete reference in the experiment config. A local-only image may not have a repository
digest; push it to a controlled registry, or address it by its immutable `sha256:<image-id>` from:

```bash
docker image inspect --format '{{.Id}}' scaffoldscope-eval:2026-08-15
```

Repository digests and local image IDs are different identifiers. ScaffoldScope records both the
declared reference and the resolved local image ID.

Mutable tags are rejected by default. `require_image_digest: false` exists only for local
exploration. Even in that mode, preflight resolves the tag once and every trial launches the
resolved image ID, preventing the tag from changing underneath an active run. Do not publish
results produced with digest enforcement disabled.

## Configuration

```json
{
  "sandbox": {
    "backend": "docker",
    "max_file_bytes": 1000000,
    "max_observation_chars": 20000,
    "max_process_output_chars": 20000,
    "test_timeout_seconds": 120,
    "docker": {
      "image": "ghcr.io/example/scaffoldscope-eval@sha256:<64-hex-digest>",
      "binary": "docker",
      "platform": "linux/amd64",
      "user": "65532:65532",
      "cpus": 2.0,
      "memory_bytes": 2147483648,
      "pids_limit": 256,
      "tmpfs_bytes": 536870912,
      "nofile_limit": 1024,
      "cleanup_timeout_seconds": 10,
      "python_executable": "python",
      "require_image_digest": true
    }
  }
}
```

`image` is required. Every other Docker field has the value shown above by default. Resource sizes
are integer bytes, not strings such as `2g`. `memory_bytes` is also passed as `--memory-swap`, which
prevents swap from silently extending the declared memory budget. `{python}` entries in a task's
`test_command` become `python_executable` inside the container.

The Docker settings are part of the resolved experiment identity. Changing a resource limit,
platform, user, image, or executable produces a different config hash and trial matrix.

## Validate, plan, and preflight

```bash
scaffoldscope validate experiment.json
scaffoldscope plan experiment.json
scaffoldscope doctor --config experiment.json
scaffoldscope run experiment.json
```

`validate` and `plan` are offline: they validate the configuration and materialize the matrix but do
not contact Docker. `doctor --config` performs the same read-only image inspection for an operator
without creating trials. `run` performs preflight before creating a trial workspace or making a model
request:

1. `docker image inspect` must find the configured image locally.
2. The image must expose an immutable SHA-256 image ID.
3. Its reported OS, architecture, and variant must match `sandbox.docker.platform`.
4. The observed record is hashed and compared with any existing experiment manifest.

ScaffoldScope never pulls an image during a run. Pulling can change latency, consume network, and
resolve a mutable tag differently across workers. These are experimental confounds. Pre-pull or build the
image on every worker before starting the matrix.

A plan-only manifest has `docker_runtime: null`. The first real run may fill that field only while no
trial results exist. Every resume repeats preflight and refuses to proceed if the runtime identity
differs. Completed trials are therefore never silently attributed to a different local image.

## Provenance

`manifest.json` separates declared settings from observed runtime identity:

- `docker`: the complete normalized Docker configuration.
- `docker_runtime.declared_image`: the configured OCI reference.
- `docker_runtime.image_id`: the immutable local image ID actually passed to `docker run`.
- `docker_runtime.configured_platform` and `image_platform`: requested and observed platforms.
- `docker_runtime.hash`: canonical hash of the observed preflight record.

Each `result.json` and `episodes.jsonl` row repeats `sandbox_backend`, `docker_image`,
`docker_image_id`, and `docker_image_platform` so an episode remains interpretable when inspected
outside the aggregate. These are additive v1 provenance fields; they do not change solve or
infrastructure-validity semantics.

## Operational limitations

- The workspace bind mount is writable because many test suites create build products. Repository
  code can modify that generated copy. Patch capture and evaluator-integrity checks run afterward;
  the original task checkout is not mounted.
- Existing protected evaluator files are read-only mounts. A protected path that is initially absent
  cannot be mounted; creating it causes the final integrity check to fail.
- Only `/tmp` and the workspace are writable. Software that insists on writing elsewhere in the
  image will fail under the read-only root filesystem.
- Network-dependent tests fail by design. Bake dependencies and fixtures into the image or choose a
  hermetic test command.
- The backend does not provision databases, sibling service containers, GPUs, or Docker-in-Docker.
- CPU limits are quotas, not dedicated cores. Concurrent trials still share host I/O and cache state.
- Cross-architecture emulation can be dramatically slower and should not be mixed with native runs.
- Docker output retention is bounded, but test-created files are limited only by Docker/host storage.
  Use disposable workers with disk quotas for adversarial repositories.
- The Docker CLI environment forwards only Docker connection/configuration variables and essential
  operating-system paths. Provider API keys are not forwarded to the CLI or container.

## Troubleshooting

### "image is unavailable locally; ScaffoldScope never pulls"

Pull the exact digest and platform before running:

```bash
docker pull --platform linux/amd64 ghcr.io/example/eval@sha256:<digest>
docker image inspect ghcr.io/example/eval@sha256:<digest>
```

Check that the same Docker context is active in the shell that launches ScaffoldScope.

### "image platform is ... but sandbox.docker.platform is ..."

Build or pull the requested platform, or change the config before planning. Do not combine results
from native and emulated platforms under one experiment identity.

### Permission denied under `/workspace`

On native Linux, set `sandbox.docker.user` to the dedicated non-root worker UID:GID and ensure that
worker owns or can write the generated experiment directory. Keep the value explicit so another
machine cannot silently choose a different identity.

### Exit 126 or 127 / evaluator could not start

The image lacks the configured executable, its entrypoint assumptions are incompatible, or the task
command names a missing program. ScaffoldScope clears the image entrypoint and executes the manifest
argv directly. Verify it manually with the same image and non-root user.

### Read-only filesystem errors

Redirect caches and temporary output to `/tmp` or `/workspace`, or rebuild the image so runtime
initialization is complete before evaluation. The container root is intentionally not writable.

### Tests attempt network access

Make the evaluator hermetic. Network mode is deliberately fixed to `none` and cannot be relaxed by
experiment configuration.

### Timeouts or abrupt Docker failures

Inspect the bounded evaluator output first. Then check `test_timeout_seconds`, CPU/memory/PID limits,
Docker daemon health, and architecture emulation. Containers carry the label
`org.scaffoldscope.role=evaluator`; `docker ps -a --filter label=org.scaffoldscope.role=evaluator`
can identify a container left behind after a daemon or host crash.

### Remote daemon mount errors

The bind source is resolved locally but interpreted by the daemon. Use a worker-local daemon or make
the identical absolute experiment path available on the remote daemon host. ScaffoldScope does not
copy workspaces to remote daemons.
