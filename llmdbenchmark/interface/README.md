# llmdbenchmark.interface

CLI subcommand definitions and environment variable helpers. Each subcommand module registers its arguments with argparse, using environment variables as defaults where applicable.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Empty package marker |
| `commands.py` | `Command` enum defining valid CLI commands: `PLAN`, `STANDUP`, `RUN`, `TEARDOWN`, `EXPERIMENT` |
| `env.py` | Environment variable helpers: `env()` for string values, `env_bool()` for boolean flags, `env_int()` for integer values. Used as argparse `default=` values. |
| `plan.py` | CLI definition for `plan` subcommand (no additional arguments) |
| `standup.py` | CLI definition for `standup` subcommand (step selection, namespace, methods, models, affinity, annotations, monitoring, kubeconfig) |
| `run.py` | CLI definition for `run` subcommand (harness config, workload profiles, experiments, endpoint URL, run-only mode, analysis) |
| `teardown.py` | CLI definition for `teardown` subcommand (step selection, namespace, methods, release name, deep clean) |
| `experiment.py` | CLI definition for `experiment` subcommand (experiment YAML, harness, workload, overrides, stop-on-error, skip-teardown) |

## Environment Variable Convention

All subcommands accept environment variables prefixed with `LLMDBENCH_` as defaults for their arguments. Common variables include:

- `LLMDBENCH_NAMESPACE` -- Deployment namespace(s)
- `LLMDBENCH_METHODS` -- Deploy method (standalone, modelservice)
- `LLMDBENCH_MODELS` -- Model list
- `LLMDBENCH_KUBECONFIG` / `KUBECONFIG` -- Kubeconfig path
- `LLMDBENCH_HARNESS` -- Harness name
- `LLMDBENCH_WORKLOAD` -- Workload profile
- `LLMDBENCH_EXPERIMENTS` -- Experiment YAML path
- `LLMDBENCH_OUTPUT` -- Results destination (local, gs://, s3://)
