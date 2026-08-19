# Getting started

This section takes you from installation to a controlled experiment. No API key or Docker installation is needed for the first run.

1. [Install ScaffoldScope](installation.md).
2. [Run the offline starter](quickstart.md).
3. [Design a first real experiment](first-experiment.md).
4. Continue with the [operator guide](../operator-guide.md) for the full evidence lifecycle.

```{toctree}
:maxdepth: 1
:hidden:

installation
quickstart
first-experiment
```

## What you need

- Python 3.10 through 3.14.
- A terminal with permission to create virtual environments and local files.
- Git only when a task workspace is a Git repository or you install from source.
- Docker only when you select the Docker evaluator backend.
- A provider API key only when your selected provider requires one.

## Vocabulary

**Source configuration**
: The JSON file you edit. It points to a task manifest and declares the fixed protocol.

**Task**
: A problem statement, starting workspace, evaluator command, and optional standing constraints.

**Variant**
: One treatment. It chooses a context policy and may change the exposed tool set or append instructions.

**Paired block**
: One task and one replicate. Every variant runs once from the same task source inside that block.

**Experiment directory**
: The generated, identity-addressed evidence directory. It is not the source project directory.

**Trial**
: One task, one replicate, and one variant.
