# llmdbenchmark.experiment

Design of Experiments (DoE) orchestrator. Manages the lifecycle of multi-treatment experiments where each setup treatment triggers a full standup, run, and teardown cycle.

## Experiment YAML Format

Experiment files define two sections:

```yaml
experiment:
  name: my-experiment      # Optional; defaults to filename
  harness: inference-perf  # Optional; overrides scenario harness
  profile: my_profile.yaml # Optional; overrides scenario profile

setup:
  constants:               # Merged into every setup treatment
    model.maxModelLen: 4096
  treatments:
    - name: tp2
      decode.parallelism.tensor: 2
    - name: tp4
      decode.parallelism.tensor: 4

treatments:                # Or "run:" -- workload treatments
  - name: low-load
    rate: 10
  - name: high-load
    rate: 100
```

### Setup Treatments

Each setup treatment provides config overrides that are deep-merged into the base scenario before plan rendering. Overrides use dotted keys (e.g. `decode.parallelism.tensor: 4`) which are converted to nested dicts via `dotted_to_nested()`. Constants from `setup.constants` are merged first, then treatment-specific values override them.

Each setup treatment triggers a complete standup to run to teardown cycle.

### Run Treatments (Workload)

Run treatments (under `treatments` or `run`) are consumed by the run phase's profile renderer (step 04). Multiple run treatments execute against a single stood-up stack.

### Matrix

The total experiment matrix is `setup_treatments x run_treatments`. For example, 3 setup treatments and 4 run treatments produce 12 total runs.

### Optional Setup Section

The `setup` section is optional. When absent, the experiment file behaves identically to the existing `--experiments` run-only flow -- a single standup runs all workload treatments.

## Files

```
experiment/
├── __init__.py    -- Package docstring
├── parser.py      -- ExperimentPlan parser
└── summary.py     -- ExperimentSummary tracker
```

## ExperimentParser (`parser.py`)

### `parse_experiment(path: Path) -> ExperimentPlan`

Parse an experiment YAML file into a structured `ExperimentPlan`. Raises `FileNotFoundError` if the file does not exist and `ValueError` if the content is not a YAML mapping.

### `dotted_to_nested(flat: dict) -> dict`

Convert a flat dict with dotted keys to a nested dict. Raises `ValueError` on key conflicts (e.g. `a.b: 1` alongside `a.b.c: 2`).

```python
>>> dotted_to_nested({"a.b.c": 1, "a.b.d": 2, "x": 3})
{"a": {"b": {"c": 1, "d": 2}}, "x": 3}
```

### Key Data Types

```python
@dataclass
class SetupTreatment:
    name: str                              # Treatment identifier
    overrides: dict[str, Any]              # Nested config overrides (post-conversion)

@dataclass
class ExperimentPlan:
    name: str                              # Experiment name
    harness: str | None                    # Harness override
    profile: str | None                    # Profile override
    setup_treatments: list[SetupTreatment] # Infrastructure treatments
    run_treatments_count: int              # Number of workload treatments
    experiment_file: Path                  # Source file path
    has_setup_phase: bool                  # True if setup section was present

    @property
    def total_matrix(self) -> int:         # setup_count x run_count
```

## ExperimentSummary (`summary.py`)

Tracks per-treatment outcomes across the experiment lifecycle.

### `ExperimentSummary`

```python
@dataclass
class ExperimentSummary:
    experiment_name: str
    total_setup_treatments: int
    total_run_treatments: int
    results: list[TreatmentResult]
    start_time: float

    def record_success(self, setup_treatment, run_completed, run_total, workspace_dir=None, duration=0.0): ...
    def record_failure(self, setup_treatment, phase, error, run_completed=0, run_total=0, ...): ...
    def write(self, path: Path): ...       # Write experiment-summary.yaml
    def print_table(self, logger): ...     # Print formatted summary table
    def to_dict(self) -> dict: ...         # Serialize for YAML output
```

### `TreatmentResult`

```python
@dataclass
class TreatmentResult:
    setup_treatment: str
    status: str                # "pending", "success", "failed_standup", "failed_run", "failed_teardown"
    run_treatments_completed: int
    run_treatments_total: int
    error_message: str | None
    workspace_dir: str | None
    duration_seconds: float
```

## Orchestration Flow

The `experiment` CLI command (defined in `interface/experiment.py`) orchestrates the lifecycle:

1. Parse the experiment YAML via `parse_experiment()`.
2. Create an `ExperimentSummary` tracker.
3. For each setup treatment:
   a. Render plans with the treatment's config overrides deep-merged into the base scenario.
   b. Execute standup (all steps).
   c. Execute run (all steps, with the experiment's run treatments).
   d. Execute teardown (all steps, unless `--skip-teardown` is set).
   e. Record success or failure in the summary.
4. Write `experiment-summary.yaml` and print the summary table.

If `--stop-on-error` is set, the experiment aborts on the first failed setup treatment. Default behavior continues to the next treatment.
