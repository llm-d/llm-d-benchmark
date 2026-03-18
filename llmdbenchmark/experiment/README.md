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

The `experiment` command:

1. Parses the experiment YAML
2. For each setup treatment:
   a. Deep-merges treatment overrides into the plan configuration
   b. Runs standup (deploys infrastructure with treatment config)
   c. Runs all run treatments against the stood-up stack
   d. Runs teardown
   e. Records success or failure
3. Prints a summary table and writes `experiment-summary.yaml`

### Run-only sweeps

```bash
llmdbenchmark --spec gpu run \
  --experiments workload/experiments/inference-scheduling.yaml
```

### Experiment command flags

| Flag | Description |
|------|-------------|
| `--experiments` / `-e` | Experiment YAML file (required) |
| `--stop-on-error` | Abort on first setup treatment failure |
| `--skip-teardown` | Leave stacks running after each treatment (for debugging) |

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
