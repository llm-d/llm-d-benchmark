---
name: llm-d-benchmark-experiment
description: |
  Deep reference for the llm-d-benchmark experiment command — Design of Experiments (DoE)
  with full factorial sweeps across infrastructure (setup) and workload (run) treatments.
  TRIGGER when: user asks about experiment YAML files, setup treatments, run treatments,
  factorial sweeps, treatment matrices, experiment orchestration, or the experiment command.
  Also trigger when user wants to create or modify experiment definitions.
  DO NOT TRIGGER for simple single-run benchmarks (use llm-d-benchmark-run skill instead).
---

# llm-d-benchmark Experiment Phase

The experiment command orchestrates full Design of Experiments (DoE) — sweeping across
infrastructure configurations (setup treatments) and workload parameters (run treatments).
Each setup treatment gets its own standup → smoketest → run → teardown cycle.

---

## CLI Usage

```bash
# Basic experiment
llmdbenchmark --spec guides/tiered-prefix-cache experiment \
  -e workload/experiments/tiered-prefix-cache.yaml \
  -p my-ns

# With explicit harness and workload (overrides experiment defaults)
llmdbenchmark --spec guides/pd-disaggregation experiment \
  -e workload/experiments/pd-disaggregation.yaml \
  -p my-ns -l inference-perf -w shared_prefix_synthetic.yaml

# Keep stacks up for debugging
llmdbenchmark --spec guides/inference-scheduling experiment \
  -e workload/experiments/inference-scheduling.yaml \
  -p my-ns --skip-teardown

# Stop on first failure
llmdbenchmark --spec guides/tiered-prefix-cache experiment \
  -e workload/experiments/tiered-prefix-cache.yaml \
  -p my-ns --stop-on-error

# With monitoring enabled
llmdbenchmark --spec guides/pd-disaggregation experiment \
  -e workload/experiments/pd-disaggregation.yaml \
  -p my-ns -f
```

---

## All Flags

| Flag | Env Var | Default | Description |
|------|---------|---------|-------------|
| `-e/--experiments` | `LLMDBENCH_EXPERIMENTS` | — | Experiment YAML file (**required**) |
| `-p/--namespace` | `LLMDBENCH_NAMESPACE` | from plan | Namespace(s) |
| `-t/--methods` | `LLMDBENCH_METHODS` | from plan | Deployment method |
| `-m/--models` | `LLMDBENCH_MODELS` | from plan | Model(s) |
| `-l/--harness` | `LLMDBENCH_HARNESS` | from experiment | Harness name |
| `-w/--workload` | `LLMDBENCH_WORKLOAD` | from experiment | Workload profile |
| `-o/--overrides` | `LLMDBENCH_OVERRIDES` | — | Profile overrides |
| `-r/--output` | `LLMDBENCH_OUTPUT` | `local` | Results destination |
| `-j/--parallelism` | `LLMDBENCH_PARALLELISM` | 1 | Parallel harness pods |
| `--wait-timeout` | `LLMDBENCH_WAIT_TIMEOUT` | 3600 | Wait timeout |
| `-x/--dataset` | `LLMDBENCH_DATASET` | — | Dataset URL |
| `-f/--monitoring` | — | false | Enable metrics |
| `-d/--debug` | — | false | Debug mode |
| `-k/--kubeconfig` | `LLMDBENCH_KUBECONFIG` | — | Kubeconfig path |
| `--parallel` | — | 4 | Max parallel stacks |
| `--stop-on-error` | — | false | Abort on first failure |
| `--skip-teardown` | — | false | Leave stacks running |

---

## Experiment YAML Structure

### Full Example

```yaml
experiment:
  name: tiered-prefix-cache
  description: "Sweep CPU block counts with varying prompt parameters"
  harness: inference-perf
  profile: shared_prefix_synthetic.yaml

design:
  type: full_factorial

  setup:
    factors:
      - name: numCpuBlocks
        key: vllmCommon.flags.numCpuBlocks
        levels: [500, 1000, 2000, 5000]

  run:
    factors:
      - name: question_len
        key: data.shared_prefix.question_len
        levels: [100, 300, 1000]
        unit: tokens
      - name: output_len
        key: data.shared_prefix.output_len
        levels: [100, 300, 1000]
        unit: tokens
    constants:
      - key: data.shared_prefix.system_prompt_len
        value: 2048
      - key: api.streaming
        value: true

  total_setup_treatments: 4
  total_run_treatments: 9
  total_matrix: 36

# Setup treatments: infrastructure changes requiring re-standup
setup:
  constants:
    model.maxModelLen: 16000
    model.blockSize: 64
  treatments:
    - name: cpu-blocks-500
      vllmCommon.flags.numCpuBlocks: 500
    - name: cpu-blocks-1000
      vllmCommon.flags.numCpuBlocks: 1000
    - name: cpu-blocks-2000
      vllmCommon.flags.numCpuBlocks: 2000
    - name: cpu-blocks-5000
      vllmCommon.flags.numCpuBlocks: 5000

# Run treatments: workload parameter changes within a single standup
treatments:
  - name: qlen100-olen100
    data.shared_prefix.question_len: 100
    data.shared_prefix.output_len: 100
  - name: qlen100-olen300
    data.shared_prefix.question_len: 100
    data.shared_prefix.output_len: 300
  # ... (9 total from 3×3 factorial)
```

### Minimal Example (Run-Only, No Setup)

```yaml
experiment:
  name: concurrency-sweep
  harness: inference-perf
  profile: chatbot_synthetic.yaml

treatments:
  - name: conc1
    data.max_concurrency: 1
    data.num_prompts: 10
  - name: conc8
    data.max_concurrency: 8
    data.num_prompts: 80
  - name: conc32
    data.max_concurrency: 32
    data.num_prompts: 320
```

No `setup` section = all treatments run against the current stack without re-standup.

### With Deployment Method Overrides

```yaml
experiment:
  name: pd-disaggregation
  harness: inference-perf
  profile: shared_prefix_synthetic.yaml

setup:
  treatments:
    # Modelservice treatments
    - name: ms-pd-nixl
      modelservice.enabled: true
      standalone.enabled: false
      vllmCommon.kvTransfer.connector: NixlConnector
    - name: ms-pd-offloading
      modelservice.enabled: true
      standalone.enabled: false
      vllmCommon.kvTransfer.connector: OffloadingConnector
    # Standalone treatments
    - name: sa-baseline
      standalone.enabled: true
      modelservice.enabled: false

treatments:
  - name: qlen100
    data.shared_prefix.question_len: 100
  - name: qlen1000
    data.shared_prefix.question_len: 1000
```

---

## Two Types of Treatments

### Setup Treatments (Infrastructure)

Changes that require a **full re-standup** of the inference stack:

- Tensor parallelism (`decode.parallelism.tensor`)
- CPU block count (`vllmCommon.flags.numCpuBlocks`)
- Routing/scheduling plugins (`inferenceExtension.plugins`)
- Deployment method (`standalone.enabled`, `modelservice.enabled`)
- KV transfer connector (`vllmCommon.kvTransfer.connector`)
- Model name/version (`model.name`)

Each setup treatment triggers: **standup → smoketest → [all run treatments] → teardown**

### Run Treatments (Workload)

Changes that only affect the benchmark workload parameters:

- Concurrency (`data.max_concurrency`)
- Prompt/output length (`data.shared_prefix.question_len`)
- Number of prompts (`data.num_prompts`)
- Dataset selection (`data.type`)
- Streaming mode (`api.streaming`)

All run treatments execute against a **single stood-up stack** without teardown between them.

---

## Treatment Matrix

```
Total runs = setup_treatments × run_treatments
```

| Example | Setup | Run | Total |
|---------|-------|-----|-------|
| tiered-prefix-cache | 4 (CPU blocks) | 6 (prompt variations) | 24 |
| precise-prefix-cache-aware | 3 (routing modes) | 6 (prompt variations) | 18 |
| pd-disaggregation | 9 (6 MS + 3 SA) | 6 (prompt variations) | 54 |
| inference-scheduling | 4 (scheduling plugins) | 9 (3×3 len combinations) | 36 |
| No setup section | 1 (default) | N treatments | N |

---

## Orchestration Flow

```
Parse experiment YAML → ExperimentPlan
    ↓
Display matrix: "4 setup × 6 run = 24 total"
    ↓
For each setup treatment:
    ├── Create isolated workspace: setup-treatment-{name}/
    ├── Deep merge overrides into plan config
    ├── Render plans with overrides
    ├── STANDUP (deploy stack with treatment config)
    ├── SMOKETEST (validate deployment)
    ├── RUN (execute ALL run treatments sequentially)
    │   ├── Treatment 1: deploy pods → wait → collect
    │   ├── Treatment 2: deploy pods → wait → collect
    │   └── Treatment N: ...
    ├── TEARDOWN (unless --skip-teardown)
    └── Record: success/failure + duration
    ↓
Write experiment-summary.yaml
Print results table
```

### Setup Constants

The `setup.constants` section applies to **all** setup treatments:

```yaml
setup:
  constants:
    model.maxModelLen: 16000    # Applied to every treatment
    model.blockSize: 64         # Applied to every treatment
  treatments:
    - name: cpu-500
      vllmCommon.flags.numCpuBlocks: 500   # Treatment-specific
    - name: cpu-1000
      vllmCommon.flags.numCpuBlocks: 1000  # Treatment-specific
```

Treatment-specific values override constants if they conflict.

### Dotted Key Resolution

Treatment keys use dotted notation, converted to nested dicts:

```python
dotted_to_nested({"a.b.c": 1, "a.b.d": 2})
# → {"a": {"b": {"c": 1, "d": 2}}}
```

These nested dicts are deep-merged into the plan config before rendering.

---

## Control Flags

### `--stop-on-error`

Default: **continue on failure**. Each setup treatment runs independently.

With `--stop-on-error`: abort the entire experiment on the first setup treatment that fails at any phase (standup, smoketest, or run).

### `--skip-teardown`

Leaves stacks running after each setup treatment. Useful for:
- Post-hoc debugging
- Manual inspection of deployed configs
- Avoiding costly re-standup when iterating on run treatments

**Warning:** With N setup treatments, you may have N stacks consuming cluster resources.

---

## Experiment Summary Output

Written to `experiment-summary.yaml` in the workspace:

```yaml
experiment: tiered-prefix-cache
total_setup_treatments: 4
total_run_treatments: 6
total_matrix: 24
succeeded: 3
failed: 1
total_duration_seconds: 7245.3

treatments:
  - setup_treatment: cpu-blocks-500
    status: success
    run_treatments: "6/6"
    duration_seconds: 1800.5
    workspace_dir: /workspace/setup-treatment-cpu-blocks-500

  - setup_treatment: cpu-blocks-1000
    status: success
    run_treatments: "6/6"
    duration_seconds: 1850.2
    workspace_dir: /workspace/setup-treatment-cpu-blocks-1000

  - setup_treatment: cpu-blocks-2000
    status: failed_standup
    run_treatments: "0/6"
    error: "Decode pods not ready: Timed out after 1500s"
    duration_seconds: 1520.0
    workspace_dir: /workspace/setup-treatment-cpu-blocks-2000

  - setup_treatment: cpu-blocks-5000
    status: success
    run_treatments: "6/6"
    duration_seconds: 2074.6
    workspace_dir: /workspace/setup-treatment-cpu-blocks-5000
```

### Status Values

| Status | Meaning |
|--------|---------|
| `success` | All phases completed |
| `failed_render` | Plan rendering failed (bad overrides) |
| `failed_standup` | Stack deployment failed |
| `failed_smoketest` | Health check or inference test failed |
| `failed_run` | Benchmark execution failed |
| `failed_teardown` | Teardown failed (stack may still be running) |

---

## Parser Classes

### ExperimentPlan (`llmdbenchmark/experiment/parser.py`)

```python
@dataclass
class ExperimentPlan:
    name: str                              # Experiment name
    harness: str | None                    # Default harness (fallback for CLI)
    profile: str | None                    # Default profile (fallback for CLI)
    setup_treatments: list[SetupTreatment] # Infrastructure treatments
    run_treatments_count: int              # Number of workload treatments
    experiment_file: Path                  # Source YAML path
    has_setup_phase: bool                  # True if setup.treatments exists

    @property
    def total_matrix(self) -> int:
        return max(len(self.setup_treatments), 1) * max(self.run_treatments_count, 1)
```

### SetupTreatment (`llmdbenchmark/experiment/parser.py`)

```python
@dataclass
class SetupTreatment:
    name: str                    # Treatment identifier
    overrides: dict[str, Any]    # Nested dict (converted from dotted keys)
```

### ExperimentSummary (`llmdbenchmark/experiment/summary.py`)

```python
@dataclass
class ExperimentSummary:
    experiment_name: str
    total_setup_treatments: int
    total_run_treatments: int
    start_time: float
    results: list[TreatmentResult]

    def record_success(name, run_completed, run_total, workspace, duration)
    def record_failure(name, phase, error_msg, run_completed, run_total)

    @property
    def succeeded(self) -> int
    @property
    def failed(self) -> int
    @property
    def total_matrix(self) -> int

    def write(path: Path)          # Write YAML summary
    def print_table(logger)        # Print results table to log
    def to_dict() -> dict          # Serialize to dict
```

### Key Functions

```python
parse_experiment(path: Path) -> ExperimentPlan
    # Parses experiment YAML, resolves setup constants + treatments

dotted_to_nested(flat: dict[str, Any]) -> dict[str, Any]
    # {"a.b.c": 1} → {"a": {"b": {"c": 1}}}
    # Raises ValueError on key conflicts
```

---

## Existing Experiment Files

Located in `workload/experiments/`:

| File | Setup | Run | Matrix | Description |
|------|-------|-----|--------|-------------|
| `tiered-prefix-cache.yaml` | 4 | 6 | 24 | CPU block count sweep |
| `precise-prefix-cache-aware.yaml` | 3 | 6 | 18 | Routing mode comparison |
| `pd-disaggregation.yaml` | 9 | 6 | 54 | MS vs SA with connectors |
| `inference-scheduling.yaml` | 4 | 9 | 36 | Scheduling plugin comparison |

---

## Creating a New Experiment

### Step 1: Define the experiment YAML

```yaml
experiment:
  name: my-experiment
  harness: inference-perf
  profile: chatbot_synthetic.yaml

setup:
  constants:
    # Shared across all setup treatments
    model.maxModelLen: 8192
  treatments:
    - name: tp2
      decode.parallelism.tensor: 2
    - name: tp4
      decode.parallelism.tensor: 4

treatments:
  - name: low-load
    data.max_concurrency: 4
    data.num_prompts: 50
  - name: high-load
    data.max_concurrency: 64
    data.num_prompts: 500
```

### Step 2: Run the experiment

```bash
llmdbenchmark --spec examples/gpu experiment \
  -e workload/experiments/my-experiment.yaml \
  -p my-ns
```

### Step 3: Check results

```bash
# Summary
cat workspace/experiment-summary.yaml

# Per-treatment results
ls workspace/setup-treatment-*/results/

# Cross-treatment comparison (if --analyze was used)
ls workspace/setup-treatment-*/results/cross-treatment-comparison/
```

---

## Experiment vs Run with `--experiments`

| Aspect | `experiment` command | `run --experiments` |
|--------|---------------------|---------------------|
| Setup treatments | Yes — re-standup per treatment | No — uses current stack |
| Run treatments | Yes — swept per setup | Yes — swept against current stack |
| Standup/Teardown | Automatic per setup treatment | Not included |
| Summary output | `experiment-summary.yaml` | Per-treatment results only |
| Use case | Full factorial DoE | Workload parameter sweep |

Use `experiment` when you need to change infrastructure between treatment groups.
Use `run --experiments` when the infrastructure stays the same and you only vary workload params.

---

## Workspace Layout

```
workspace/
├── experiment-summary.yaml              # Overall experiment results
├── setup-treatment-cpu-blocks-500/
│   ├── plan/                            # Rendered plans for this treatment
│   ├── setup/                           # Command logs
│   └── results/
│       ├── inference-perf-qlen100-..._1/
│       ├── inference-perf-qlen300-..._1/
│       └── cross-treatment-comparison/
├── setup-treatment-cpu-blocks-1000/
│   └── ...
└── setup-treatment-cpu-blocks-2000/
    └── ...
```

---

## Key Files

| File | Purpose |
|------|---------|
| `llmdbenchmark/interface/experiment.py` | Experiment subcommand flag definitions |
| `llmdbenchmark/cli.py` (`_execute_experiment`) | Experiment orchestration loop |
| `llmdbenchmark/experiment/parser.py` | ExperimentPlan, SetupTreatment, parse_experiment() |
| `llmdbenchmark/experiment/summary.py` | ExperimentSummary, TreatmentResult |
| `workload/experiments/` | Experiment YAML definitions |
| `workload/profiles/` | Workload profile templates |
