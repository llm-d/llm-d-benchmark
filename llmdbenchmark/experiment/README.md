# Experiment Module (Design of Experiments)

The experiment module provides Design of Experiments (DoE) orchestration for `llmdbenchmark`. It enables systematic evaluation of how infrastructure configurations and workload parameters affect inference performance.

## Module Structure

```text
experiment/
    __init__.py
    parser.py           Parse experiment YAML into ExperimentPlan
    summary.py          Per-treatment result tracking and summary output
```

## Core Concepts

### Two-Phase Experiment Model

Experiments operate across two independent dimensions:

- **Setup treatments** -- Infrastructure variants (e.g., different CPU block allocations, routing plugins, replica counts). Each setup treatment triggers a full standup → run → teardown cycle.
- **Run treatments** -- Workload variants (e.g., different prompt lengths, concurrency levels, prefix configurations). All run treatments execute against each stood-up infrastructure.

The total number of benchmark runs is `setup_treatments × run_treatments`.

### Experiment vs Run

| Command | Infrastructure | Workload | Use Case |
|---------|---------------|----------|----------|
| `run --experiments` | Fixed (already deployed) | Swept via treatments | Test workload variations against one stack |
| `experiment` | Swept via setup treatments | Swept via run treatments | Full factorial: vary both infrastructure and workload |

## Data Model

### ExperimentPlan

Parsed from the experiment YAML file:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Experiment name (from YAML or file stem) |
| `harness` | `str \| None` | Harness type (e.g., `inference-perf`) |
| `profile` | `str \| None` | Workload profile name |
| `setup_treatments` | `list[SetupTreatment]` | Infrastructure configuration variants |
| `run_treatments_count` | `int` | Number of workload treatments |
| `experiment_file` | `Path` | Path to source YAML |
| `has_setup_phase` | `bool` | Whether the experiment defines setup treatments |
| `total_matrix` | `int` (property) | `max(setup, 1) × max(run, 1)` |

### SetupTreatment

A single infrastructure configuration variant:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Treatment identifier (e.g., `cpu-blocks-500`) |
| `overrides` | `dict` | Nested configuration overrides for deep-merge into the plan |

### TreatmentResult

Tracks the outcome of a single setup treatment cycle:

| Field | Type | Description |
|-------|------|-------------|
| `setup_treatment` | `str` | Treatment name |
| `status` | `str` | `success`, `failed_standup`, `failed_run`, or `failed_teardown` |
| `run_treatments_completed` | `int` | How many run treatments finished |
| `run_treatments_total` | `int` | Total run treatments for this cycle |
| `error_message` | `str \| None` | Error description if failed |
| `workspace_dir` | `str \| None` | Workspace path for this treatment |
| `duration_seconds` | `float` | Time for the full standup+run+teardown cycle |

### ExperimentSummary

Aggregates results across all treatment cycles. Provides `record_success()` and `record_failure()` methods for tracking, and outputs via `print_table()` (formatted terminal table) and `write()` (YAML file).

## Experiment YAML Format

### Structure

```yaml
experiment:
  name: my-experiment               # Optional; defaults to filename
  harness: inference-perf            # Optional; overridable via CLI
  profile: shared_prefix_synthetic.yaml  # Optional

setup:                               # Optional; omit for run-only experiments
  constants:                         # Applied to ALL setup treatments
    model.maxModelLen: 16000
    model.blockSize: 64
  treatments:
    - name: cpu-blocks-500
      vllmCommon.flags.numCpuBlocks: 500
    - name: cpu-blocks-1000
      vllmCommon.flags.numCpuBlocks: 1000

treatments:                          # Run treatments (workload variations)
  - name: grp40-splen8k
    data.shared_prefix.num_groups: 40
    data.shared_prefix.system_prompt_len: 8000
  - name: grp40-splen5k
    data.shared_prefix.num_groups: 40
    data.shared_prefix.system_prompt_len: 5000
```

### Dotted Key Notation

Configuration overrides use dotted keys that are automatically expanded into nested dictionaries:

```yaml
# This:
vllmCommon.flags.numCpuBlocks: 500

# Becomes:
vllmCommon:
  flags:
    numCpuBlocks: 500
```

### Setup Constants

Values in `setup.constants` are merged into every setup treatment as a base layer. Treatment-specific values override constants:

```yaml
setup:
  constants:
    model.maxModelLen: 16000          # Applied to all treatments
  treatments:
    - name: custom
      model.maxModelLen: 32000        # Overrides the constant for this treatment
      vllmCommon.flags.numCpuBlocks: 500  # Additional override
```

### Run-Only Experiments

Omitting the `setup` section creates a run-only experiment (no infrastructure changes):

```yaml
experiment:
  name: workload-sweep
  harness: inference-perf

treatments:
  - name: low-concurrency
    concurrency: 10
  - name: high-concurrency
    concurrency: 100
```

Use with `run --experiments` to sweep workload parameters against an existing stack.

## CLI Usage

### Full DoE (setup + run treatments)

```bash
llmdbenchmark --spec tiered-prefix-cache experiment \
  --experiments workload/experiments/tiered-prefix-cache.yaml
```

### Run-only sweeps (no infrastructure changes)

```bash
# Sweep workload parameters against an already-deployed stack
llmdbenchmark --spec gpu run \
  --experiments workload/experiments/inference-scheduling.yaml
```

When there is no `setup:` section in the experiment YAML (or when using `run --experiments` instead of `experiment`), the tool only varies workload parameters. No standup or teardown happens — the run treatments execute against whatever endpoint is already available.

### Experiment command flags

| Flag | Description |
|------|-------------|
| `--experiments` / `-e` | Experiment YAML file (required) |
| `--stop-on-error` | Abort on first setup treatment failure |
| `--skip-teardown` | Leave stacks running after each treatment (for debugging) |

## Execution Flow

The `experiment` command orchestrates a **sequential** pipeline. Only one infrastructure configuration is alive at any time.

### Step-by-step for `experiment`

```text
┌─────────────────────────────────────────────────────────────┐
│  llmdbenchmark --spec <spec> experiment -e <experiment.yaml>│
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
               Parse experiment YAML
               (setup treatments + run treatments)
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
   Setup Treatment 1  Setup Treatment 2  ...  (sequential)
         │
         ├─ 1. Render plans (deep-merge setup overrides into config)
         ├─ 2. Standup (deploy infrastructure with overridden config)
         ├─ 3. Run ALL run treatments against this stack:
         │      ├─ Run treatment A (render profile, deploy harness, collect)
         │      ├─ Run treatment B
         │      └─ Run treatment C
         ├─ 4. Teardown (destroy the stack)
         └─ 5. Record result (success/failure + duration)
                         │
                         ▼
              Next setup treatment (repeat 1-5)
                         │
                         ▼
              Write experiment-summary.yaml
              Print summary table
```

### What each phase does internally

**Render** (`_render_plans_for_experiment`): Takes the setup treatment's overrides (e.g., `{"decode": {"replicas": 4}}`), deep-merges them on top of `defaults.yaml + scenario`, then renders all Jinja2 templates. The result is a complete set of Kubernetes manifests customised for this treatment.

**Standup** (`_do_standup`): Runs the full standup step pipeline (steps 00-10) using the rendered manifests. This deploys the llm-d stack, GAIE, model service, and runs the smoketest.

**Run** (`_do_run`): Runs the full run step pipeline (steps 00-11). The experiment YAML file is passed through as `experiment_file_override`, so step 04 reads the `treatments:` key and renders one workload profile per run treatment. Step 06 then deploys harness pods for each run treatment **sequentially** — deploy, wait, collect results, clean up, then move to the next run treatment.

**Teardown** (`_do_teardown`): Runs the full teardown step pipeline (steps 00-04). Uninstalls Helm releases, deletes resources, and cleans up the namespace. This ensures a clean slate for the next setup treatment.

### Endpoint discovery

The endpoint is **auto-discovered** after standup. Run step 02 (`DetectEndpointStep`) finds the gateway or standalone service IP in the namespace. There is no way to pass a per-treatment `--endpoint-url` — the experiment always deploys and discovers.

### Error handling

By default, a failed setup treatment is recorded and the experiment **continues** to the next setup treatment. Use `--stop-on-error` to abort the entire experiment on the first failure.

If standup fails, a cleanup teardown is still attempted (unless `--skip-teardown` is set) to avoid leaking cluster resources.

### Workspace structure

Each setup treatment gets its own subdirectory:

```text
workspace/
    setup-treatment-2-replicas/
        plan/                    # Rendered manifests for this treatment
        run/                     # Benchmark results
    setup-treatment-4-replicas/
        plan/
        run/
    experiment-summary.yaml      # Aggregate results
```

### Comparison: `experiment` vs `run --experiments`

| | `experiment` | `run --experiments` |
|---|---|---|
| **Command** | `llmdbenchmark experiment -e file.yaml` | `llmdbenchmark run -e file.yaml` |
| **Reads `setup:` section** | Yes — one standup/teardown per setup treatment | No — ignored entirely |
| **Reads `treatments:` section** | Yes — workload variations per stack | Yes — workload variations |
| **Deploys infrastructure** | Yes — per setup treatment | No — uses existing endpoint |
| **Tears down** | Yes — after each setup treatment | No |
| **Use case** | Full factorial sweep across infrastructure + workload | Sweep workload parameters against a fixed stack |

## Summary Output

### Terminal Table

```text
==============================================================
  DoE EXPERIMENT SUMMARY
==============================================================
  Experiment:       tiered-prefix-cache
  Setup treatments: 4
  Run treatments:   6
  Total matrix:     24
  Duration:         1234s
--------------------------------------------------------------
  ✅ cpu-blocks-500     success          runs: 6/6
  ✅ cpu-blocks-1000    success          runs: 6/6
  ❌ cpu-blocks-2000    failed_standup   runs: 0/6
     Error: Pod scheduling timeout
  ✅ cpu-blocks-5000    success          runs: 6/6
==============================================================
  Result: 3/4 succeeded, 1 failed
==============================================================
```

### YAML Summary File

Written to `{workspace}/experiment-summary.yaml`:

```yaml
experiment: tiered-prefix-cache
total_setup_treatments: 4
total_run_treatments: 6
total_matrix: 24
succeeded: 3
failed: 1
total_duration_seconds: 1234.5
treatments:
  - setup_treatment: cpu-blocks-500
    status: success
    run_treatments: 6/6
    duration_seconds: 300.2
    workspace_dir: /workspace/cpu-blocks-500
  - setup_treatment: cpu-blocks-2000
    status: failed_standup
    run_treatments: 0/6
    duration_seconds: 45.3
    error: Pod scheduling timeout
```

## Pre-Built Experiments

Example experiment files are in `workload/experiments/`:

| File | Setup | Run | Total | Purpose |
|------|-------|-----|-------|---------|
| `tiered-prefix-cache.yaml` | 4 | 6 | 24 | CPU block allocation vs prefix cache performance |
| `inference-scheduling.yaml` | 4 | 9 | 36 | Scheduling plugin comparison across workload shapes |
| `pd-disaggregation.yaml` | 9 | 6 | 54 | Prefill-decode disaggregation configurations |
| `precise-prefix-cache-aware.yaml` | 3 | 6 | 18 | Routing strategy comparison for prefix cache |
