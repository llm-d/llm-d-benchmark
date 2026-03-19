# llmdbenchmark.experiment

Design of Experiments (DoE) orchestrator. Manages the lifecycle of multi-treatment experiments where each setup treatment triggers a full standup, run, and teardown cycle.

## Experiment Structure

Experiment YAML files define two sections:

- **`setup`** -- Infrastructure treatments. Each treatment provides config overrides that are deep-merged into the base scenario before plan rendering. Each setup treatment triggers a complete standup/run/teardown cycle.
- **`treatments`** (or `run`) -- Workload treatments consumed by the run phase's profile renderer (step 04). Multiple run treatments execute against a single stood-up stack.

The total experiment matrix is `setup_treatments x run_treatments`.

The `setup` section is optional. When absent, the experiment file behaves identically to the existing `--experiments` run-only flow.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Package docstring |
| `parser.py` | Parse experiment YAML into `ExperimentPlan`. Handles `SetupTreatment` extraction, constant merging, dotted-key-to-nested-dict conversion, and run treatment counting. |
| `summary.py` | `ExperimentSummary` and `TreatmentResult` -- track per-treatment outcomes (success/failure, duration, run counts) and write `experiment-summary.yaml`. Includes a formatted table printer. |

## Key Data Types

- **`ExperimentPlan`** -- Parsed experiment definition: name, harness, profile, setup treatments list, run treatment count, and total matrix size.
- **`SetupTreatment`** -- A single infrastructure config override set (name + nested overrides dict).
- **`ExperimentSummary`** -- Aggregate experiment results with `record_success()` and `record_failure()` methods, YAML serialization, and summary table output.
