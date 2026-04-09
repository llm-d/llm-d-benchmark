# llmdbenchmark

Benchmarking framework for LLM inference stacks on Kubernetes. Provides an end-to-end pipeline for deploying model-serving infrastructure, executing benchmark workloads, collecting results, and generating standardized analysis reports.

## Package Structure

```
llmdbenchmark/
├── __init__.py              -- Package metadata (name, version, homepage)
├── cli.py                   -- CLI entry point: argument parsing, workspace setup, plan rendering, phase dispatch
├── config.py                -- Package-wide WorkspaceConfig singleton (paths, flags)
├── analysis/                -- Post-benchmark result processing and visualization
├── executor/                -- Execution engine: step orchestration, command execution
├── experiment/              -- DoE experiment plan parsing and summary tracking
├── interface/               -- CLI subcommand definitions and environment variable helpers
├── logging/                 -- Logger with emoji formatting, file output, and stream separation
├── parser/                  -- Config parsing, Jinja2 rendering, version/resource resolution
├── phases/                  -- Lifecycle phase implementations (standup, smoketest, run, teardown, experiment) + banners
├── run/                     -- Run phase steps (deploy harness, collect results, analyze)
├── smoketests/              -- Post-deployment validation (health, inference, config checks)
├── standup/                 -- Standup phase steps (provision infrastructure, deploy models)
├── teardown/                -- Teardown phase steps (uninstall, clean up resources)
├── utilities/               -- Shared helpers (Kubernetes, endpoint detection, cloud upload)
└── exceptions/              -- Custom exception hierarchy
```

## CLI Commands

The package exposes six subcommands. `cli.py` parses arguments, sets up the
workspace, renders plans, and dispatches to the `execute_*` functions in
`llmdbenchmark/phases/`:

| Command | Dispatch target | Description |
|---------|-----------------|-------------|
| `plan` | (handled inline in `cli.py`) | Generate deployment plans (YAML/Helm manifests) without executing |
| `standup` | `phases.standup.execute_standup` | Provision infrastructure and deploy model-serving stacks. Auto-chains smoketest unless `--skip-smoketest`. |
| `smoketest` | `phases.smoketest.execute_smoketest` | Validate deployment health, run inference test, check pod config against scenario |
| `run` | `phases.run.execute_run` | Execute benchmark workloads against deployed stacks |
| `teardown` | `phases.teardown.execute_teardown` | Remove deployed resources and clean up |
| `experiment` | `phases.experiment.execute_experiment` | Orchestrate full DoE experiments (standup + run + teardown per setup treatment) |

## Lifecycle

A typical benchmark session follows this pipeline:

1. **Plan** -- Render Jinja2 templates into per-stack YAML plans from a specification file, merging defaults with scenario overrides.
2. **Standup** -- Execute standup steps: validate infrastructure, create namespaces, deploy model-serving pods (9 steps, 00-09).
3. **Smoketest** -- Validate deployment: health checks, sample inference, per-scenario config validation. Runs automatically after standup; also available as a standalone command.
4. **Run** -- Execute run steps: detect endpoints, render workload profiles, deploy harness pods, wait for completion, collect and analyze results (12 steps, 00-11).
5. **Teardown** -- Execute teardown steps: uninstall Helm releases, delete pods/secrets/ConfigMaps, clean cluster-scoped resources (5 steps, 00-04).

The `experiment` command automates this lifecycle across multiple setup treatments (Design of Experiments).

## How Submodules Relate

- **interface** defines CLI arguments for each subcommand; **cli.py** parses them and dispatches to the corresponding `execute_*` function in **phases**.
- **parser** renders specification files and stack plans; `cli.py` drives the rendering and hands the result (`render_plan_errors`) to each phase module.
- **phases** holds the lifecycle implementations -- one module per phase (`standup`, `smoketest`, `run`, `teardown`, `experiment`), plus `banners.py` (all CLI summary blocks) and `common.py` (shared helpers: stack info loaders, namespace parsing, deploy-method resolution, `PhaseError`). Each phase exposes a `do_<phase>` re-entrant core and an `execute_<phase>` top-level entry point. See [phases/README.md](phases/README.md).
- **executor** provides the step framework (`Step`, `StepExecutor`, `ExecutionContext`, `StepPrologue`, result types). The `Step` base class also offers prologue/epilogue helpers (`start`, `success_result`, `failure_result`) that consolidate boilerplate across every step implementation.
- **standup/run/teardown/smoketests** each register ordered steps that the executor runs sequentially (global) or in parallel (per-stack). The phase modules in **phases/** build the `ExecutionContext`, construct a `StepExecutor`, and kick off execution.
- **smoketests** provides post-deployment validation with per-scenario validators that check deployed pods against rendered config. `phases.standup.execute_standup` auto-chains `do_smoketest` after a successful standup unless `--skip-smoketest` is passed.
- **experiment** (the package, not the phase module) holds experiment plan parsing (`parser.py`) and summary tracking (`summary.py`). `phases.experiment.execute_experiment` composes `do_standup`, `do_smoketest`, `do_run`, and `do_teardown` per setup treatment.
- **analysis** is invoked at the end of the run phase to convert raw harness output into standardized benchmark reports and plots.
- **utilities** provides shared Kubernetes, endpoint, and filesystem helpers used across all phases.
- **logging** and **exceptions** are cross-cutting infrastructure used throughout.
