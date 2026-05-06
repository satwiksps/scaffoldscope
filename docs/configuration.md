# Configuration reference

ScaffoldScope uses strict JSON so an accepted configuration has one unambiguous meaning. Unknown fields are errors; put third-party settings inside `plugin_options`. `schema_version` is required and currently equals `1`.

Export the packaged Draft 2020-12 schema for editor or CI integration:

```bash
scaffoldscope schema --out experiment.schema.json
```

## `experiment`

| Field | Type | Meaning |
|---|---|---|
| `name` | ID string | Human-readable name used in the output directory |
| `replicates` | nonnegative 64-bit integer list | Paired repeat identifiers; the legacy alias `seeds` is accepted |
| `max_workers` | positive integer | Concurrent paired-block workers |
| `output_dir` | path | Root for generated experiment directories |
| `baseline` | variant ID | Control for paired comparisons |
| `primary_comparison` | variant ID or `null` | One preregistered contrast eligible for confirmatory labeling |
| `randomize_variant_order` | boolean | Deterministically shuffle variants inside paired blocks |
| `bootstrap_samples` | integer >= 100 | Task-cluster bootstrap draws |
| `analysis_seed` | integer | Deterministic report seed |
| `sesoi` | number in `(0, 1)` | Smallest solve-rate effect of practical interest |

The final directory is `<output_dir>/<name>-<first-8-config-hash>`.

## `tasks`

`manifest` points to JSONL. Optional `ids` selects an explicit panel and `limit` takes the first rows after selection. Config paths resolve relative to the config; manifest workspace paths resolve relative to the manifest.

Each task row supports:

| Field | Required | Meaning |
|---|---:|---|
| `id` | yes | Stable instance ID; `instance_id` is an accepted alias |
| `workspace` | yes | Trusted local directory copied or cloned per trial |
| `problem` | yes | Issue statement; `problem_statement` is an accepted alias |
| `constraints` | no | Standing text plus optional deterministic check |
| `test_command` | no | Trusted argument vector; `{python}` expands to the evaluator interpreter |
| `protected_paths` | no | Relative evaluator paths readable but immutable to the model |
| `script` | scripted provider | Offline protocol responses |
| `base_commit` | no | Detached commit checked out after cloning a Git workspace |
| `repository` | no | Provenance label; `repo` is an accepted alias |
| `metadata` | no | Dataset-specific provenance |

Constraint check types are `file_unchanged`, `file_exists`, `text_present`, and `text_absent`. All require a relative `path`; text checks also require `text`.

Task source bytes or the resolved Git commit participate in experiment identity. Changing a fixture cannot resume an old result.

## `model`

Built-in providers are `scripted` and `openai_compatible`; an installed model-provider plugin name is also valid.

Common fields:

| Field | Meaning |
|---|---|
| `name` | Exact model or scripted-engine revision |
| `context_window_tokens` | Declared context window |
| `max_output_tokens` | Maximum response reservation per call |
| `temperature` | Sampling temperature |
| `supports_seed` | Include the replicate seed only when the provider supports it |
| `input_price_per_million`, `output_price_per_million` | Immutable configured-price snapshot or `null` |
| `cache_read_price_per_million`, `cache_write_price_per_million` | Optional cache-specific prices |
| `plugin_options` | Provider-plugin settings; built-ins reject nonempty values |

The OpenAI-compatible provider also accepts:

- `base_url` (required), `timeout_seconds`, `retries`, and `json_mode`;
- `requires_api_key` (default `true`); and
- `api_key_env` (default `OPENAI_API_KEY`).

Set `requires_api_key` to `false` only for a deliberately unauthenticated local endpoint. A remote plain-HTTP URL is rejected when a key is enabled. `supports_seed` defaults to `false`; set it only when the provider documents and accepts that field.

Prices are user-supplied estimates, not invoices. Use `null` instead of guessing. A monetary cap requires input and output prices. Cache prices fall back to the input price when omitted.

## `agent`

- `max_turns`
- `max_total_tokens`
- `max_cost_usd` (`null` disables)
- either inline `system_prompt` or relative `prompt_file`

The explicit `char4-v1` counter is a provider-independent estimate. Before every call the runner reserves estimated input plus maximum output and refuses a call whose worst case exceeds the remaining configured token or cost budget. Provider usage and invoices remain authoritative.

## `sandbox`

Common fields are `backend` (`local` or `docker`), `max_file_bytes`, `max_observation_chars`, `max_process_output_chars`, and `test_timeout_seconds`.

With `backend: "docker"`, a `docker` object is required:

| Field | Default | Meaning |
|---|---:|---|
| `image` | required | Local image reference; digest pin required by default |
| `binary` | `docker` | Docker CLI executable |
| `user` | `65532:65532` | Numeric non-root UID:GID |
| `platform` | `linux/amd64` | Pinned platform, or `null` |
| `cpus` | `2.0` | CPU limit |
| `memory_bytes` | 2 GiB | Memory and swap limit |
| `pids_limit` | `256` | Process limit |
| `tmpfs_bytes` | 512 MiB | Writable temporary filesystem limit |
| `nofile_limit` | `1024` | File-descriptor limit |
| `cleanup_timeout_seconds` | `10` | Kill/removal bound |
| `python_executable` | `python` | Interpreter inside the image |
| `require_image_digest` | `true` | Reject mutable image tags |

See [docker.md](docker.md) before running untrusted code.

## `variants`

Every variant needs `id` and `policy`. A policy is one of `none`, `reactive`, `periodic`, `selective`, or an installed context-policy plugin.

Context fields:

- `trigger_ratio`, `target_ratio`, `every_turns`, and `keep_recent_bundles`;
- `weights` with any of `recency`, `referenced`, `subgoal`, `constraint`, `task`, and `error`; and
- `plugin_options` for a third-party policy. Built-ins reject nonempty plugin options.

Other treatment axes:

- `tools`: an exact unique subset of `list_files`, `read_file`, `search`, `search_symbols`, `replace`, `write_file`, and `run_tests`;
- `instructions`: nonempty treatment-specific guidance appended to the invariant prompt.

Omitting `tools` selects the full task-compatible built-in surface. `run_tests` is unavailable when a task has no local evaluator command.

## Validation and identity

```bash
scaffoldscope validate experiment.json
scaffoldscope budget experiment.json
scaffoldscope plan experiment.json
```

Validation checks strict fields and types, IDs, ratios, duplicates, task selection and workspaces, baseline membership, budget observability, Docker settings, plugin compatibility, and source fingerprints. The config hash includes raw inputs, prompt contents, harness implementation, task sources, and loaded plugin provenance.
