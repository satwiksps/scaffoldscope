# Supported scope and limitations

ScaffoldScope 0.3 is an alpha research instrument for controlled, paired
comparisons of coding-agent harness mechanisms.

## Supported

- Python 3.10–3.14 on Linux, macOS, and Windows.
- Local scripted studies and OpenAI-compatible chat-completions providers.
- Fixed task × replicate × treatment plans with explicit budgets and seeds.
- Built-in context-policy, tool-surface, and instruction treatments.
- Local execution and a network-disabled Docker backend that requires
  digest-pinned images by default.
- SWE-bench generation export and immutable ingestion of official evaluator
  outcomes.
- Auditable traces, patches, reports, usage provenance, and deterministic
  evidence bundles.
- Versioned context-policy and model-provider plugins.

## Not promised

- A security boundary for hostile model-generated code. Prefer an isolated host
  even when using Docker.
- Benchmark decontamination or an absolute measure of model capability.
- Statistical power from small panels, duplicate scripted trajectories, or
  incomplete treatment pairs.
- Semantic preservation of every natural-language constraint after compaction;
  lexical availability and behavioral adherence are reported separately.
- Compatibility of private plugin APIs or unversioned evidence formats.
- A hosted benchmark service, distributed scheduler, or managed model gateway.

The bundled scripted demo validates the core workflow only. It must not be cited
as model-performance evidence.
