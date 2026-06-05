# Pinned-tail example plugin

This standalone example distribution demonstrates ScaffoldScope's lazy context-policy entry point.
The policy keeps the system/task bundles and a configurable recent tail, dropping only whole bundles
when context pressure reaches `trigger_ratio`.

```bash
python -m pip install -e examples/plugins/pinned-tail
scaffoldscope plugins --check
```

Select it in an experiment variant. This example has no custom options, so `plugin_options` is empty:

```json
{
  "id": "pinned-tail",
  "policy": "example.pinned-tail",
  "trigger_ratio": 0.85,
  "keep_recent_bundles": 3,
  "plugin_options": {}
}
```

ScaffoldScope validates the registration before planning and records its distribution version and
implementation SHA-256 in experiment identity. See the complete contract in
[`docs/extensions.md`](../../../docs/extensions.md).

This implementation is an API example, not a benchmark result or a recommended compaction strategy.
