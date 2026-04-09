# llmdbenchmark.phases

Lifecycle phase implementations for the `llmdbenchmark` CLI. Each subcommand
(`standup`, `smoketest`, `run`, `teardown`, `experiment`) has its own module
here; `cli.py` only parses arguments, sets up the workspace, renders plans,
and dispatches to the `execute_*` entry points exposed from this package.

Extracting these phases out of `cli.py` was done to:

- Keep `cli.py` focused on argument parsing and workspace setup (~460 lines
  now, down from ~1500).
- Give the experiment orchestrator a clean set of re-entrant cores
  (`do_standup`, `do_smoketest`, `do_run`, `do_teardown`) that it can
  compose per treatment without going back through argparse.
- Colocate all the phase-specific banners, CLI parsing helpers, and stack
  info loaders that used to be scattered across `cli.py`.

## Files

```
phases/
├── __init__.py       -- Package docstring and import ordering notes
├── common.py         -- PhaseError, stack loaders, CLI parsing helpers
├── banners.py        -- All CLI summary/kickoff banners
├── standup.py        -- execute_standup / do_standup / check_model_access
├── smoketest.py      -- execute_smoketest / do_smoketest
├── run.py            -- execute_run / do_run / store_run_parameters_configmap
├── teardown.py       -- execute_teardown / do_teardown
└── experiment.py     -- execute_experiment (composes the do_* cores)
```

## Public API

Each phase module exposes two functions:

| Function | Audience | Returns |
|----------|----------|---------|
| `do_<phase>(args, logger, render_plan_errors)` | Re-entrant core used by `phases.experiment` to drive the DoE matrix. | `(context, result)`; raises `PhaseError` on failure. |
| `execute_<phase>(args, logger, render_plan_errors)` | Top-level CLI entry point. Calls `do_<phase>`, prints banners, handles `sys.exit(1)` on failure. | `None` |

`execute_experiment` has no `do_*` sibling because the experiment IS the
composition of the other `do_*` cores.

## Module responsibilities

### `phases/common.py`

Utilities shared across all phase modules. Every phase imports from here;
this module must never import from any other `phases/*` module (that would
create a cycle).

- `PhaseError` -- raised by any phase on lifecycle failure. `execute_*`
  catches it and exits; `execute_experiment` catches it and records the
  treatment as failed before continuing.
- `load_stack_info_from_config(config_file, stack_name)` -- parse a single
  rendered `config.yaml` into a plan-info dict with namespace, harness
  namespace, model name, HuggingFace token, release name, and
  standalone/modelservice enabled flags.
- `load_all_stacks_info(rendered_paths)` -- call the above for every
  rendered stack directory, returning a list of per-stack info dicts.
- `load_plan_info(rendered_paths)` -- convenience wrapper that returns the
  first stack's info (used by teardown, which only needs global info).
- `parse_namespaces(ns_str, plan_info)` -- parse the CLI `--namespace` value
  into `(namespace, harness_namespace)`. Supports `"ns"` (both use the same
  value) and `"ns,harness_ns"` (comma-separated). Falls back to
  `plan_info` when the CLI value is not provided.
- `resolve_deploy_methods(args, plan_info, logger, phase)` -- determine
  deployment methods from the CLI `--methods` flag, with fallback to
  auto-detection from the scenario's `standalone.enabled` /
  `modelservice.enabled` flags. Phase-specific behavior:
  - `standup` / `smoketest`: mutually exclusive, picks one method.
  - `run`: returns a list of every enabled method (endpoint detection
    needs all of them).
  - `teardown`: raises `PhaseError` if no plan info and no `--methods`
    was passed (can't guess what to remove).
- `render_plans_for_experiment(args, logger, setup_overrides=None)` --
  render plans with optional setup treatment overrides. Used exclusively
  by `phases.experiment` to regenerate plans between treatments.

### `phases/banners.py`

All decorative "horizontal rule + labeled fields" output the CLI prints
lives here, so phase modules can stay focused on orchestration logic.

- `print_standup_banner(context, result, logger)` -- "STANDUP COMPLETE"
  with namespace, methods, stacks, step pass/fail counts, and deployed
  endpoints.
- `print_run_banner(context, logger)` -- "BENCHMARK RUN SUMMARY" with
  harness, workload, model, mode (full / run-only / generate-config),
  parallelism, treatments, local results path, and a reproducible
  `kubectl exec` command for inspecting PVC results.
- `print_teardown_banner(context, logger)` -- single-line teardown
  completion with namespaces, mode, methods, and release.
- `print_experiment_start_banner(experiment_plan, stop_on_error, skip_teardown, logger)` --
  kickoff banner shown once at the start of `execute_experiment`.
- `print_phase_banner` -- re-exported from
  `llmdbenchmark.utilities.cluster` (it lives there because it runs at
  the beginning of a phase, before the `ensure_infra` step, and needs
  cluster info that is only resolved during `ensure_infra`).

### `phases/standup.py`

Deploys infrastructure, gateway, and model pods (9 steps, 00-09 in
`llmdbenchmark/standup/steps/`).

- `do_standup(args, logger, render_plan_errors)` -- build the
  `ExecutionContext`, check HuggingFace access for every unique model
  across all stacks, and run `StepExecutor` with `get_standup_steps()`.
  Returns `(context, result)`.
- `execute_standup(args, logger, render_plan_errors)` -- top-level entry
  point. Calls `do_standup`, prints the standup banner, and auto-chains
  `do_smoketest` unless the user passed `--skip-smoketest`. The chain
  goes one-way -- `smoketest.py` must not import from `standup.py`.
- `check_model_access(context, all_stacks_info, logger)` -- verifies
  HuggingFace access for every unique model. Exits immediately if any
  gated model is inaccessible. Skipped in dry-run.

### `phases/smoketest.py`

Post-deployment validation (3 steps, 00-02 in
`llmdbenchmark/smoketests/steps/`).

- `do_smoketest(args, logger, render_plan_errors)` -- called both by
  `execute_smoketest` (standalone CLI invocation) and by
  `execute_standup` (the auto-chain after standup completes).
- `execute_smoketest(args, logger, render_plan_errors)` -- top-level
  entry point for `llmdbenchmark smoketest`.

### `phases/run.py`

Executes the benchmark harness against deployed model endpoints (12
steps, 00-11 in `llmdbenchmark/run/steps/`).

- `do_run(args, logger, render_plan_errors, experiment_file_override=None)` --
  re-entrant core. The optional `experiment_file_override` lets
  `phases.experiment` inject the per-run-treatment experiments file.
- `execute_run(args, logger, render_plan_errors)` -- top-level entry
  point. Calls `do_run`, prints the BENCHMARK RUN SUMMARY banner, and
  stores run parameters as a ConfigMap in the namespace for auditability.
- `store_run_parameters_configmap(context, harness, workload, experiment_ids, logger)` --
  append a timestamped entry to the `llm-d-benchmark-run-parameters`
  ConfigMap (keyed by timestamp, with `latest` always pointing at the
  newest entry). Multiple sequential or parallel runs accumulate history
  in a single ConfigMap rather than overwriting each other.

### `phases/teardown.py`

Removes deployed resources (5 steps, 00-04 in
`llmdbenchmark/teardown/steps/`).

- `do_teardown(args, logger, render_plan_errors)` -- re-entrant core
  used by the experiment orchestrator for per-treatment cleanup.
- `execute_teardown(args, logger, render_plan_errors)` -- top-level
  entry point. Prints the teardown completion banner.

### `phases/experiment.py`

Orchestrates a full DoE setup × run treatment matrix. This is the only
`phases/*` module that imports from its siblings -- it composes
`do_standup`, `do_smoketest`, `do_run`, and `do_teardown` to implement
the matrix.

- `execute_experiment(args, logger)` -- parses the experiment YAML via
  `parse_experiment()`, synthesizes a default setup treatment if the
  YAML has none, then for each setup treatment:
  1. Calls `render_plans_for_experiment` with the treatment's config
     overrides.
  2. Runs `do_standup` → `do_smoketest` → `do_run` (once per run
     treatment) → `do_teardown`.
  3. Records success or failure in an `ExperimentSummary`.
  4. Writes `experiment-summary.yaml` to the workspace.

  Supports `--stop-on-error` (abort the matrix on first failure) and
  `--skip-teardown` (leave stacks running for debugging).

**Global state note**: the experiment orchestrator temporarily mutates
`config.workspace` and `config.plan_dir` per treatment so each run lands
in an isolated subdirectory. The mutation is wrapped in `try/finally`
so an uncaught exception mid-matrix cannot leak a stale workspace into
subsequent code.

## Import rules

To keep the package free of import cycles:

1. **`cli.py` may import `phases/*`**, never the other way around.
2. **`phases/common.py`** may not import from any other `phases/*`
   module or from `llmdbenchmark.cli`.
3. **`phases/banners.py`** may not import from sibling phase modules.
4. **`phases/standup.py` → `phases/smoketest.py`** is the only
   allowed one-way dependency between phase implementations (for the
   standup → smoketest auto-chain).
5. **`phases/experiment.py`** may import from all other `phases/*`
   modules. It is the only phase that composes its siblings.

Violations of these rules almost always manifest as `ImportError` at
CLI import time, which is why `phases/__init__.py` and every module
docstring spells them out explicitly.

## Adding a new phase

In practice this is rare -- the four existing phases cover the entire
benchmark lifecycle. If you do need one (e.g., a dedicated
`build-image` phase):

1. Add a new file `phases/<name>.py` with `do_<name>` and
   `execute_<name>` functions following the patterns above.
2. Add a corresponding subcommand to `llmdbenchmark.interface.commands`
   and wire up arguments in `llmdbenchmark/interface/<name>.py`.
3. Register the new command in `cli.py`'s `dispatch_cli` dispatch block.
4. If your phase has its own steps, create an `llmdbenchmark/<name>/`
   package with `steps/__init__.py::get_<name>_steps()`.
5. If your phase needs a completion banner, add a
   `print_<name>_banner(...)` function to `phases/banners.py`.
6. If the experiment orchestrator should drive the new phase, compose
   its `do_<name>` from `phases/experiment.py`.
