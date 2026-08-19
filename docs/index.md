# ScaffoldScope documentation

ScaffoldScope runs controlled, paired experiments on coding-agent harnesses. It changes one declared scaffold treatment while holding the model, task, evaluator, budget, and replicate fixed.

Use it to measure context policies, tool surfaces, or treatment instructions. Every run produces an inspectable plan, per-trial traces, patches, reports, and a deterministic evidence bundle.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Install
:link: getting-started/installation
:link-type: doc

Install the PyPI package on Python 3.10 through 3.14.
:::

:::{grid-item-card} Run the starter
:link: getting-started/quickstart
:link-type: doc

Complete a zero-cost paired experiment in a few minutes.
:::

:::{grid-item-card} Design a study
:link: getting-started/first-experiment
:link-type: doc

Move from the scripted starter to a fixed model, task panel, and primary comparison.
:::

:::{grid-item-card} Operate and publish
:link: operator-guide
:link-type: doc

Plan, resume, inspect, verify, bundle, and share experiment evidence.
:::
::::

## Choose the right path

| If you want to... | Read... |
|---|---|
| Understand the measurement claim | [Core concepts](concepts/index.md) |
| Define tasks, variants, budgets, and providers | [Configuration reference](configuration.md) |
| Run untrusted evaluator code | [Docker backend](docker.md) |
| Grade generated patches with the official harness | [SWE-bench workflow](swebench.md) |
| Interpret solve, resource, and governance results | [Experiment design](experiment-design.md) |
| Inspect persisted evidence fields | [Result bundle and schema](results-schema.md) |
| Implement a policy or provider plugin | [Extension guide](extensions.md) |
| Diagnose a failed command or trial | [Troubleshooting](troubleshooting.md) |

```{note}
The bundled scripted provider verifies the workflow. It is not evidence of model capability. A publishable study needs a real model, a frozen task panel, complete paired cells, and an explicit interpretation policy.
```

```{toctree}
:caption: Getting started
:maxdepth: 2
:hidden:

getting-started/index
```

```{toctree}
:caption: Concepts
:maxdepth: 2
:hidden:

concepts/index
```

```{toctree}
:caption: Guides
:maxdepth: 2
:hidden:

guides/index
```

```{toctree}
:caption: Reference
:maxdepth: 2
:hidden:

reference/index
```

```{toctree}
:caption: Development
:maxdepth: 2
:hidden:

development/index
```
