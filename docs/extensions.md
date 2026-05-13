# Third-party extensions

ScaffoldScope exposes a dependency-free, lazy plugin API for context policies and model providers.
Installed extensions can be selected directly in experiment configuration, with plugin-owned
settings carried in `plugin_options`.

## Entry-point contract

A plugin is a normal Python distribution with one or both of these entry-point groups:

```toml
[project.entry-points."scaffoldscope.context_policies"]
"acme.pinned-tail" = "acme_scaffoldscope:policy_registration"

[project.entry-points."scaffoldscope.model_providers"]
"acme.replay" = "acme_scaffoldscope:provider_registration"
```

Use an organization-prefixed name. Lookup is case-insensitive and treats `.`, `_`, and `-` as the
same separator. This intentionally makes ambiguous names a startup error. Plugins may not shadow
the built-ins (`none`, `reactive`, `periodic`, `selective`, `scripted`, or
`openai_compatible`). Discovery ordering is deterministic and does not import plugin modules.

The referenced object must be a `PluginRegistration`, not the factory itself:

```python
from scaffoldscope.plugins import ContextPolicyRequest, context_policy_plugin


def create_policy(request: ContextPolicyRequest):
    return MyPolicy(request.config, request.counter)


policy_registration = context_policy_plugin(
    create_policy,
    plugin_version="1.2.0",
    description="Keeps pinned bundles and a deterministic recency tail.",
    minimum_core_version="0.1.0",
    maximum_core_version_exclusive="1.0.0",
)
```

Model factories receive a `ModelProviderRequest` containing the resolved `ModelConfig`, task,
token counter, retry-event callback, and plugin-owned options. They must return an object satisfying
the `ChatModel.complete` protocol. Context factories receive a `ContextPolicyRequest` and must
return a `ContextPolicy`.

`plugin_version` is mechanism provenance chosen by the plugin publisher. It is recorded separately
from the installed distribution name and version. `api_version` describes the callable contract;
the current value is `PLUGIN_API_VERSION == 1`. Core compatibility bounds use numeric dotted
releases, with an inclusive minimum and exclusive maximum.

## Discovery and explicit loading

```python
from scaffoldscope.plugins import PluginKind, PluginRegistry

registry = PluginRegistry.discover()

# Metadata only: no plugin module has been imported.
for item in registry.plugins(PluginKind.CONTEXT_POLICY):
    print(item.name, item.distribution, item.distribution_version)

# Import, validate API/core compatibility, and cache exactly once.
loaded = registry.load_context_policy("acme.pinned_tail")
print(loaded.provenance())
```

Import failures, kind mismatches, invalid metadata, incompatible API versions, unavailable names,
and name collisions raise a `PluginError` subclass with a remediation hint. A failed import is
cached so a broken plugin cannot repeatedly execute import-time side effects in one process.

Use `scaffoldscope plugins` for metadata-only discovery or `scaffoldscope plugins --check` to import
and validate every installed extension. Add `--json` for a machine-readable inventory.

Installing a plugin authorizes arbitrary Python code to run when that plugin is loaded. Treat plugin
packages as trusted code, pin wheel hashes in reproducible environments, and record the environment
lock alongside result bundles. On load, ScaffoldScope records an `implementation_sha256` over the
distribution's sorted installed `.py` and `.pyi` paths and bytes. When safe installed-file metadata
is unavailable, it hashes the bounded source file containing the factory and labels the fallback in
`implementation_hash_source`. This detects local Python-code drift; it is not a signature and does
not cover native libraries, data files, transitive dependencies, or ambient services.

See the runnable package skeleton in
[`examples/plugins/pinned-tail`](../examples/plugins/pinned-tail/README.md).

## Runtime selection and identity

Select a third-party policy by its entry-point name. `plugin_options` must be a JSON object and is
passed unchanged as the request's mapping; plugin code must treat it and nested values as read-only:

```json
{
  "variants": [
    {
      "id": "pinned-tail",
      "policy": "acme.pinned-tail",
      "trigger_ratio": 0.85,
      "keep_recent_bundles": 3,
      "plugin_options": {"preserve_errors": true}
    }
  ]
}
```

For a provider plugin, set `model.provider` to its entry-point name and place provider-specific
settings in `model.plugin_options`. Built-ins reject non-empty `plugin_options`, which catches
misspelled or accidentally ineffectual settings.

Configuration loading discovers and validates referenced plugins before planning or paid API calls.
The resolved configuration records `LoadedPlugin.provenance()`, including the entry-point target,
distribution and plugin versions, compatibility interval, implementation hash, and hash source.
That mapping participates in the canonical configuration hash, so implementation drift changes trial
identity instead of silently resuming stale evidence. Factories are checked to return the required
`ContextPolicy` or `ChatModel` interface before an episode starts.

Context plugins receive `ContextPolicyRequest(config, counter, options)`. Model plugins receive
`ModelProviderRequest(config, task, counter, event_callback, options)`. Provider implementations
must report every failed or retried request through `event_callback`; the core preserves those events
and counts them even when a provider uses its own payload shape. A successful `ModelResponse` after
one or more callback events is marked usage-incomplete and its configured-price cost becomes `null`
unless the provider supplied complete billing for every attempt; ScaffoldScope never invents it.
Providers should also set integer `raw_metadata.attempt_count` when that count is known so traces are
self-describing outside the core ledger.

## Measurement requirements for policy plugins

The public interface does not relax the repository invariants. Context-policy plugins must:

- derive views from the append-only canonical trajectory;
- select assistant/tool bundles atomically;
- preserve pinned messages and fail explicitly when mandatory content cannot fit;
- be deterministic for fixed inputs unless stochasticity is declared, seeded, and recorded;
- identify retained, dropped, and summarized source messages in `ContextDecision`;
- avoid evaluator outcomes, gold patches, ambient credentials, and undeclared network access; and
- account for every model-backed summarization call, retry, token, latency, and cost.

Model-provider plugins must return `ModelResponse`, preserve actual provider model and fingerprint
metadata, label locally estimated usage, and never invent billing for failed calls whose usage was not
reported.
