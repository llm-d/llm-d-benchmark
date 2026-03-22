# llmdbenchmark

Benchmarking framework for LLM inference stacks on Kubernetes. Provides an end-to-end pipeline for deploying model-serving infrastructure, executing benchmark workloads, collecting results, and generating standardized analysis reports.

## Package Structure

```
llmdbenchmark/
├── __init__.py              -- Package metadata (name, version, homepage)
├── cli.py                   -- CLI entry point: argument parsing, workspace setup, phase dispatch
├── config.py                -- Package-wide WorkspaceConfig singleton (paths, flags)
├── analysis/                -- Post-benchmark result processing and visualization
├── executor/                -- Execution engine: step orchestration, command execution
├── experiment/              -- DoE experiment orchestrator (setup + run treatment lifecycle)
├── interface/               -- CLI subcommand definitions and environment variable helpers
├── logging/                 -- Logger with emoji formatting, file output, and stream separation
├── parser/                  -- Config parsing, Jinja2 rendering, version/resource resolution
├── run/                     -- Run phase steps (deploy harness, collect results, analyze)
├── smoketests/              -- Post-deployment validation (health, inference, config checks)
├── standup/                 -- Standup phase steps (provision infrastructure, deploy models)
├── teardown/                -- Teardown phase steps (uninstall, clean up resources)
├── utilities/               -- Shared helpers (Kubernetes, endpoint detection, cloud upload)
└── exceptions/              -- Custom exception hierarchy
```

## CLI Commands

The package exposes six subcommands via `cli.py`:

| Command | Description |
|---------|-------------|
| `plan` | Generate deployment plans (YAML/Helm manifests) without executing |
| `standup` | Provision infrastructure and deploy model-serving stacks |
| `smoketest` | Validate deployment health, run inference test, check pod config against scenario |
| `run` | Execute benchmark workloads against deployed stacks |
| `teardown` | Remove deployed resources and clean up |
| `experiment` | Orchestrate full DoE experiments (standup + run + teardown per treatment) |

## Lifecycle

A typical benchmark session follows this pipeline:

1. **Plan** -- Render Jinja2 templates into per-stack YAML plans from a specification file, merging defaults with scenario overrides.
2. **Standup** -- Execute standup steps: validate infrastructure, create namespaces, deploy model-serving pods (9 steps, 00-09).
3. **Smoketest** -- Validate deployment: health checks, sample inference, per-scenario config validation. Runs automatically after standup; also available as a standalone command.
4. **Run** -- Execute run steps: detect endpoints, render workload profiles, deploy harness pods, wait for completion, collect and analyze results (12 steps, 00-11).
5. **Teardown** -- Execute teardown steps: uninstall Helm releases, delete pods/secrets/ConfigMaps, clean cluster-scoped resources (5 steps, 00-04).

The `experiment` command automates this lifecycle across multiple setup treatments (Design of Experiments).

## How Submodules Relate

- **interface** defines CLI arguments for each subcommand; **cli.py** dispatches to the appropriate phase.
- **parser** renders specification files and stack plans; the rendered output is consumed by **executor**.
- **executor** provides the step framework (`Step`, `StepExecutor`, `ExecutionContext`) used by **standup**, **run**, and **teardown**.
- **standup/run/teardown** each register ordered steps that the executor runs sequentially (global) or in parallel (per-stack).
- **smoketests** provides post-deployment validation with per-scenario validators that check deployed pods against rendered config. Runs after standup or independently.
- **experiment** wraps the standup/run/teardown cycle, iterating over setup treatments with config overrides.
- **analysis** is invoked at the end of the run phase to convert raw harness output into standardized benchmark reports and plots.
- **utilities** provides shared Kubernetes, endpoint, and filesystem helpers used across all phases.
- **logging** and **exceptions** are cross-cutting infrastructure used throughout.
