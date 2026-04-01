# Developer Guide: Extending the Benchmark Framework

This guide explains how to extend the llm-d-benchmark framework with new steps,
analysis modules, harnesses, scenarios, and experiments. All code references are
accurate to the current codebase.

## Table of Contents

- [1. Architecture Overview](#1-architecture-overview)
  - [Step-Based Execution Model](#step-based-execution-model)
  - [Four-Phase Lifecycle](#four-phase-lifecycle)
  - [Experiment Orchestrator](#experiment-orchestrator)
  - [How StepExecutor Partitions Steps](#how-stepexecutor-partitions-steps)
  - [ExecutionContext](#executioncontext)
- [2. How to Add a New Step](#2-how-to-add-a-new-step)
  - [Step-by-Step Guide](#step-by-step-guide)
  - [Complete Examples](#complete-examples)
- [3. Step Execution Flow](#3-step-execution-flow)
  - [Discovery and Ordering](#discovery-and-ordering)
  - [Pre-Global / Per-Stack / Post-Global Partition](#pre-global--per-stack--post-global-partition)
  - [Parallel Execution Across Stacks](#parallel-execution-across-stacks)
  - [Error Handling](#error-handling)
  - [should_skip() Behavior](#shouldskip-behavior)
  - [Dry Run Mode](#dry-run-mode)
  - [Step Filtering](#step-filtering)
- [4. ExecutionContext Deep Dive](#4-executioncontext-deep-dive)
  - [All Fields](#all-fields)
  - [Accessing Cluster Commands](#accessing-cluster-commands)
  - [Accessing Plan Config](#accessing-plan-config)
  - [Logging](#logging)
  - [How Steps Share State](#how-steps-share-state)
- [5. How to Add a New Analysis Module](#5-how-to-add-a-new-analysis-module)
  - [Where Analysis Code Lives](#where-analysis-code-lives)
  - [How to Add a New Plot Type](#how-to-add-a-new-plot-type)
  - [How to Add a New Metric to Cross-Treatment Comparison](#how-to-add-a-new-metric-to-cross-treatment-comparison)
  - [Analysis Pipeline Chain](#analysis-pipeline-chain)
- [6. How to Add a New Harness](#6-how-to-add-a-new-harness)
  - [Required Files](#required-files)
  - [Harness Pod Template](#harness-pod-template)
- [7. How to Add a New Scenario (Well-Lit Path)](#7-how-to-add-a-new-scenario-well-lit-path)
  - [File Structure](#file-structure)
  - [Step 1: Create the Specification Template](#step-1-create-the-specification-template)
  - [Step 2: Create the Scenario File](#step-2-create-the-scenario-file)
  - [How Templates Are Rendered](#how-templates-are-rendered)
  - [Custom Jinja2 Templates](#custom-jinja2-templates)
- [8. How to Add a Smoketest Validator](#8-how-to-add-a-smoketest-validator)
- [9. How to Add a New Experiment](#9-how-to-add-a-new-experiment)
  - [Experiment YAML Format](#experiment-yaml-format)
  - [Setup Treatments vs Run Treatments](#setup-treatments-vs-run-treatments)
  - [How to Reference Scenario Overrides](#how-to-reference-scenario-overrides)
  - [Running an Experiment](#running-an-experiment)

---

## 1. Architecture Overview

### Step-Based Execution Model

The framework is built around a **Step** abstraction (defined in
`llmdbenchmark/executor/step.py`). Every unit of work -- deploying a namespace,
running a smoketest, collecting results -- is a `Step` subclass with a number,
name, phase, and an `execute()` method.

### Four-Phase Lifecycle

A benchmark run proceeds through four phases, each with its own ordered list of
steps:

1. **Standup** (`Phase.STANDUP`) -- Provisions infrastructure, deploys models.
   Steps 00-09 in `llmdbenchmark/standup/steps/`. Note: smoketest steps
   (formerly steps 10-11) have been moved to `llmdbenchmark/smoketests/` and
   run as a separate phase after standup.
2. **Smoketest** (`Phase.SMOKETEST`) -- Post-deployment validation: health
   checks, inference tests, per-scenario config validation. Steps 00-02 in
   `llmdbenchmark/smoketests/steps/`.
3. **Run** (`Phase.RUN`) -- Detects endpoints, renders workload profiles,
   deploys harness pods, waits for completion, collects and analyzes results.
   Steps 00-11 in `llmdbenchmark/run/steps/`.
4. **Teardown** (`Phase.TEARDOWN`) -- Uninstalls Helm releases, cleans harness
   resources, deletes cluster-scoped objects. Steps 00-04 in
   `llmdbenchmark/teardown/steps/`.

### Experiment Orchestrator

The `experiment` subcommand (implemented in `_execute_experiment` in
`llmdbenchmark/cli.py`) drives a Design of Experiments (DoE) matrix. For each
**setup treatment** it:

1. Renders plans with config overrides from the experiment YAML
2. Runs the full standup phase
3. Runs all **run treatments** (each is a separate run phase invocation)
4. Tears down the stack

Results are collected into `experiment-summary.yaml`.

### How StepExecutor Partitions Steps

`StepExecutor` (in `llmdbenchmark/executor/step_executor.py`) sorts all steps
by number, then partitions them into three groups:

- **Pre-global**: Global steps (`per_stack=False`) whose number is _lower_ than
  the lowest per-stack step. These run sequentially before any per-stack work.
- **Per-stack**: Steps with `per_stack=True`. These run sequentially within each
  stack but in parallel _across_ stacks (up to `max_parallel_stacks`, default 4)
  using `ThreadPoolExecutor`.
- **Post-global**: Global steps whose number is _higher_ than the lowest
  per-stack step. These run sequentially after all per-stack work completes.

```
pre-global steps (sequential)
    |
    v
per-stack steps (parallel across stacks, sequential within each stack)
    |
    v
post-global steps (sequential)
```

### ExecutionContext

`ExecutionContext` (in `llmdbenchmark/executor/context.py`) is a `@dataclass`
that carries all shared state through every step and phase. Steps receive it as
the first argument to `execute()`. Key contents:

- **Paths**: `plan_dir`, `workspace`, `base_dir`, `rendered_stacks`
- **Execution flags**: `dry_run`, `verbose`, `non_admin`, `current_phase`
- **Cluster info**: `cluster_url`, `kubeconfig`, `is_openshift`, `is_kind`,
  `cluster_name`, `context_name`
- **Namespace info**: `namespace`, `harness_namespace`
- **Deployed state**: `deployed_endpoints`, `deployed_methods`,
  `deployed_pod_names`, `experiment_ids`
- **Run configuration**: `harness_name`, `harness_profile`,
  `harness_output`, `harness_parallelism`
- **Shared logger and command executor**: `logger` (implements
  `LoggerProtocol`), `cmd` (`CommandExecutor`)

---

## 2. How to Add a New Step

### Step-by-Step Guide

#### 1. Create the step file

Place it in the appropriate phase directory. Follow the naming convention
`step_NN_descriptive_name.py`, where `NN` is a two-digit step number:

```
llmdbenchmark/standup/steps/step_11_my_custom_step.py
llmdbenchmark/run/steps/step_12_my_custom_step.py
llmdbenchmark/teardown/steps/step_05_my_custom_step.py
```

#### 2. Extend the Step base class

Every step must:
- Call `super().__init__()` with `number`, `name`, `description`, `phase`, and
  `per_stack`
- Implement `execute(self, context, stack_path=None) -> StepResult`
- Optionally override `should_skip(self, context) -> bool`

Here is the base class signature:

```python
class Step(ABC):
    def __init__(
        self,
        number: int,
        name: str,
        description: str,
        phase: Phase,
        per_stack: bool = False,
    ):
        ...

    @abstractmethod
    def execute(self, context: ExecutionContext, stack_path: Path | None = None) -> StepResult:
        ...

    def should_skip(self, context: ExecutionContext) -> bool:
        return False
```

#### 3. Implement execute()

The method must return a `StepResult`. Collect errors into a list, then return
success or failure:

```python
def execute(self, context: ExecutionContext, stack_path: Path | None = None) -> StepResult:
    errors: list[str] = []
    cmd = context.require_cmd()

    # Do work...
    result = cmd.kube("get", "pods", "--namespace", context.require_namespace(), check=False)
    if not result.success:
        errors.append(f"Failed to list pods: {result.stderr}")

    if errors:
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=False,
            message="Step failed",
            errors=errors,
        )

    return StepResult(
        step_number=self.number,
        step_name=self.name,
        success=True,
        message="Step completed successfully",
    )
```

#### 4. Implement should_skip()

Return `True` to skip the step based on context. For example, the run preflight
step skips when only collecting results:

```python
def should_skip(self, context: ExecutionContext) -> bool:
    return context.harness_skip_run
```

#### 5. Choose per_stack=True vs per_stack=False

| `per_stack` | Behavior | When to use |
|-------------|----------|-------------|
| `False` (default) | Runs once, globally. `stack_path` is `None`. | Cluster-wide operations: namespace creation, Helm uninstalls, preflight checks |
| `True` | Runs once per rendered stack. `stack_path` is the path to that stack's rendered config directory. | Operations that differ per deployment topology: smoketests, per-stack deploys |

When `per_stack=True`, your step must handle a non-None `stack_path` and can
load per-stack config via `self._load_stack_config(stack_path)`.

#### 6. Register the step in __init__.py

Add the import and instantiation to the phase's `__init__.py`:

```python
# In llmdbenchmark/standup/steps/__init__.py
from llmdbenchmark.standup.steps.step_11_my_custom_step import MyCustomStep

def get_standup_steps() -> list[Step]:
    return [
        EnsureInfraStep(),
        # ... existing steps ...
        SmoketestStep(),
        MyCustomStep(),  # Add here in order
    ]
```

#### 7. Choose the right step number

Steps execute in number order. Rules:
- Pick a number that places your step in the correct logical position.
- Global steps with numbers lower than the first per-stack step run before
  parallel execution. Global steps with higher numbers run after.
- Leave gaps between step numbers when possible (e.g., 10, 20, 30) to allow
  future insertions without renumbering.
- Within a phase, numbers must be unique.

Note: standup skips step 01 (reserved). Choose numbers that don't conflict with
existing steps.

### Complete Examples

#### Standup Example: Deploy a Custom CRD

```python
"""Step 11 -- Deploy a custom CRD before model serving starts."""

from pathlib import Path
from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class DeployCustomCrdStep(Step):
    """Deploy a custom Kubernetes CRD required by the workload."""

    def __init__(self):
        super().__init__(
            number=11,
            name="deploy_custom_crd",
            description="Deploy custom CRD for workload integration",
            phase=Phase.STANDUP,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        # Skip if running in non-admin mode (CRDs require cluster-admin)
        return context.non_admin

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors: list[str] = []
        cmd = context.require_cmd()

        crd_yaml = context.base_dir / "config" / "crds" / "my-crd.yaml"
        if not crd_yaml.exists():
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="CRD file not found",
                errors=[f"Expected CRD at {crd_yaml}"],
            )

        if context.dry_run:
            context.logger.log_info(f"[DRY RUN] Would apply {crd_yaml}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="[DRY RUN] CRD apply logged",
            )

        result = cmd.kube("apply", "-f", str(crd_yaml), check=False)
        if not result.success:
            errors.append(f"CRD apply failed: {result.stderr}")

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="CRD deployment failed",
                errors=errors,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message="Custom CRD deployed",
        )
```

Register in `llmdbenchmark/standup/steps/__init__.py`:

```python
from llmdbenchmark.standup.steps.step_11_deploy_custom_crd import DeployCustomCrdStep

def get_standup_steps() -> list[Step]:
    return [
        # ... existing steps 00-09 ...
        DeployCustomCrdStep(),
    ]
```

#### Run Example: Warm Up the Model Before Benchmarking

```python
"""Step 03b -- Send warmup requests to the model before benchmarking."""

from pathlib import Path
from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class WarmupModelStep(Step):
    """Send warmup requests to prime KV caches before benchmark."""

    def __init__(self):
        super().__init__(
            number=4,  # After verify_model (03), before render_profiles (04)
            name="warmup_model",
            description="Send warmup requests to prime model caches",
            phase=Phase.RUN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return context.dry_run or context.harness_skip_run

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        cmd = context.require_cmd()
        namespace = context.require_namespace()

        # Use the first deployed endpoint
        if not context.deployed_endpoints:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No endpoints deployed, skipping warmup",
            )

        endpoint = list(context.deployed_endpoints.values())[0]
        context.logger.log_info(f"Sending 5 warmup requests to {endpoint}...")

        # Run a curl pod to send warmup requests
        result = cmd.kube(
            "run", "warmup-probe", "--rm", "--attach", "--quiet",
            "--restart=Never", "--namespace", namespace,
            "--image=curlimages/curl",
            "--command", "--", "sh", "-c",
            f"for i in $(seq 1 5); do "
            f"curl -s -X POST {endpoint}/v1/completions "
            f"-H 'Content-Type: application/json' "
            f"-d '{{\"model\":\"{context.model_name}\",\"prompt\":\"warmup\",\"max_tokens\":1}}'; "
            f"done",
            check=False,
        )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message="Warmup requests sent",
        )
```

Note: this example is illustrative. The code uses `number=4` but the comment
says "Step 03b". In practice, inserting at number 4 would conflict with the
existing `RenderProfilesStep` (number=4). You would need to renumber existing
steps or choose a non-conflicting number such as 3 (between existing steps 03
and 04), using a numbering scheme that avoids collisions.

#### Teardown Example: Archive Results to S3

```python
"""Step 05 -- Archive benchmark results to S3 after teardown."""

from pathlib import Path
from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class ArchiveResultsStep(Step):
    """Upload workspace results to S3 for long-term storage."""

    def __init__(self):
        super().__init__(
            number=5,
            name="archive_results",
            description="Archive results to S3",
            phase=Phase.TEARDOWN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return context.dry_run

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        cmd = context.require_cmd()
        results_dir = context.workspace / "results"

        if not results_dir.exists():
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No results directory to archive",
            )

        s3_dest = f"s3://my-benchmark-bucket/{context.cluster_name}/{context.namespace}/"
        context.logger.log_info(f"Archiving {results_dir} to {s3_dest}")

        result = cmd.execute(
            f"aws s3 sync {results_dir} {s3_dest}",
            check=False,
        )

        if not result.success:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="S3 upload failed",
                errors=[result.stderr],
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Results archived to {s3_dest}",
        )
```

---

## 3. Step Execution Flow

### Discovery and Ordering

Each phase has a `get_*_steps()` function in its `__init__.py` that returns a
list of step instances. `StepExecutor.__init__` sorts them by `step.number`:

```python
self.steps = sorted(steps, key=lambda s: s.number)
```

### Pre-Global / Per-Stack / Post-Global Partition

`StepExecutor._partition_steps()` splits the sorted steps:

1. Separate steps into `global_steps` (per_stack=False) and `per_stack_steps`
   (per_stack=True).
2. Find the lowest per-stack step number (`min_per_stack`).
3. Global steps with `number < min_per_stack` become **pre-global**.
4. Global steps with `number >= min_per_stack` become **post-global**.

### Parallel Execution Across Stacks

For per-stack steps with multiple rendered stacks, `_execute_stacks_parallel()`
uses `ThreadPoolExecutor` with `max_workers = min(max_parallel_stacks, len(stacks))`.
Each stack runs its per-stack steps sequentially. Stacks execute in parallel.

### Error Handling

- **Global steps**: If a global step fails, execution aborts immediately
  (`_execute_global_steps` returns `True`). No further steps run.
- **Per-stack steps**: If a per-stack step fails within a stack, that stack's
  remaining steps are skipped (`break` in the step loop), but other stacks
  continue.
- **Exceptions**: `_safe_execute_step()` catches all exceptions and wraps them
  in a failed `StepResult`, preventing one step from crashing the entire
  pipeline.

### should_skip() Behavior

Before executing any step, the executor calls `step.should_skip(context)`. If it
returns `True`, the step is logged as skipped and a successful `StepResult` with
`message="Skipped"` is recorded. The step's `execute()` method is never called.

### Dry Run Mode

Steps must check `context.dry_run` themselves inside `execute()`. The executor
does not skip steps in dry-run mode automatically. The convention is to log what
_would_ happen and return a successful result:

```python
if context.dry_run:
    return StepResult(
        step_number=self.number,
        step_name=self.name,
        success=True,
        message="[DRY RUN] Would have done X",
    )
```

### Step Filtering

Users can run specific steps with `--steps "0,3-5,9"`. `StepExecutor.execute()`
passes the spec to `parse_step_list()` which returns a set of allowed step
numbers. Steps not in the set are excluded from partitioning.

---

## 4. ExecutionContext Deep Dive

### All Fields

| Field | Type | Set By | Description |
|-------|------|--------|-------------|
| `plan_dir` | `Path` | CLI init | Directory containing rendered plan files |
| `workspace` | `Path` | CLI init | Working directory for this run |
| `base_dir` | `Path \| None` | CLI init | Project root (for templates, scenarios) |
| `specification_file` | `str \| None` | CLI init | Resolved `--spec` path |
| `rendered_stacks` | `list[Path]` | Plan rendering | Paths to rendered stack directories |
| `dry_run` | `bool` | CLI flag | If True, commands are logged but not executed |
| `verbose` | `bool` | CLI flag | Enable verbose output |
| `non_admin` | `bool` | CLI flag | Skip steps requiring cluster-admin |
| `current_phase` | `Phase` | Phase runner | Currently executing phase |
| `deep_clean` | `bool` | CLI flag | Teardown: wipe all resources |
| `release` | `str` | Default `"llmdbench"` | Helm release name prefix |
| `cluster_url` | `str \| None` | Step 00 | Kubernetes API server URL |
| `cluster_token` | `str \| None` | Step 00 | Bearer token for API server auth |
| `kubeconfig` | `str \| None` | CLI / env | Path to kubeconfig |
| `is_openshift` | `bool` | Step 00 | True if cluster is OpenShift |
| `is_kind` | `bool` | Step 00 | True if cluster is Kind |
| `is_minikube` | `bool` | Step 00 | True if cluster is Minikube |
| `cluster_name` | `str \| None` | Step 00 | Hostname from API server URL |
| `cluster_server` | `str \| None` | Step 00 | Full API server URL |
| `context_name` | `str \| None` | Step 00 | Kube context name |
| `username` | `str \| None` | Step 00 | Current user for labeling |
| `namespace` | `str \| None` | Plan config / CLI | Model deployment namespace |
| `harness_namespace` | `str \| None` | Plan config / CLI | Benchmark harness namespace |
| `wva_namespace` | `str \| None` | Plan config / CLI | WVA namespace |
| `proxy_uid` | `int \| None` | Step 00 | OpenShift UID range (first_uid + 1) |
| `model_name` | `str \| None` | Plan config | Model identifier (e.g. `meta-llama/Llama-3.1-8B`) |
| `accelerator_resource` | `str \| None` | Step 03 | Node accelerator resource (e.g. `nvidia.com/gpu`) |
| `network_resource` | `str \| None` | Step 03 | Network resource (e.g. `rdma/rdma_shared_device_a`) |
| `deployed_endpoints` | `dict[str, str]` | Standup | Endpoint URLs keyed by stack name |
| `deployed_methods` | `list[str]` | Standup | Deploy methods used (standalone, modelservice) |
| `deployed_pod_names` | `list[str]` | Run step 06 | Harness pod names deployed |
| `experiment_ids` | `list[str]` | Run step 06 | Experiment IDs for result collection |
| `experiment_treatments` | `list[dict] \| None` | Run phase | Treatment overrides from experiment YAML |
| `results_dir` | `Path \| None` | Run phase | Where results are written |
| `harness_name` | `str \| None` | Run phase | Active harness (inference-perf, guidellm, etc.) |
| `harness_profile` | `str \| None` | Run phase | Workload profile filename |
| `experiment_treatments_file` | `str \| None` | Run phase | Path to experiment treatments file |
| `profile_overrides` | `str \| None` | Run phase | Profile override string |
| `harness_output` | `str` | Default `"local"` | Output destination (local, gs://, s3://) |
| `harness_parallelism` | `int` | Default `1` | Parallel harness pods per treatment |
| `harness_wait_timeout` | `int` | Default `3600` | Seconds to wait for harness completion |
| `harness_debug` | `bool` | Default `False` | Enable debug mode for harness pods |
| `harness_skip_run` | `bool` | Default `False` | Skip harness run (collect-only mode) |
| `harness_service_account` | `str \| None` | Run phase | Service account for harness pods |
| `harness_envvars_to_pod` | `str \| None` | Run phase | Extra env vars forwarded to harness pods |
| `analyze_locally` | `bool` | Default `False` | Run analysis locally instead of in-cluster |
| `endpoint_url` | `str \| None` | CLI flag | Existing endpoint URL (run-only mode) |
| `run_config_file` | `str \| None` | CLI flag | Pre-built run config file (run-only mode) |
| `generate_config_only` | `bool` | Default `False` | Generate config without executing run |
| `dataset_url` | `str \| None` | CLI flag | Custom dataset URL for harness |
| `kubectl_cmd` | `str` | Default `"kubectl"` | Path to kubectl binary |
| `helm_cmd` | `str` | Default `"helm"` | Path to helm binary |
| `helmfile_cmd` | `str` | Default `"helmfile"` | Path to helmfile binary |
| `python_cmd` | `str` | Default `"python3"` | Path to python binary |
| `logger` | `LoggerProtocol` | CLI init | Logger instance |
| `cmd` | `CommandExecutor` | `rebuild_cmd()` | Shared command executor for kubectl/helm/shell |

### Accessing Cluster Commands

Use `context.require_cmd()` to get the `CommandExecutor`. It raises
`RuntimeError` if not yet initialized:

```python
cmd = context.require_cmd()

# Run kubectl commands
result = cmd.kube("get", "pods", "--namespace", ns, check=False)

# Run arbitrary shell commands
result = cmd.execute("aws s3 ls s3://bucket", check=False, silent=True)
```

### Accessing Plan Config

Steps can load the merged YAML config from rendered stacks using helper methods
inherited from `Step`:

```python
# Load config from the first rendered stack
plan_config = self._load_plan_config(context)

# Load config from a specific stack (for per_stack steps)
stack_config = self._load_stack_config(stack_path)

# Require a nested config key (raises KeyError if missing)
model_name = self._require_config(plan_config, "model", "name")

# Resolve with three-tier fallback: context value > plan config > default
port = self._resolve(plan_config, "vllmCommon.inferencePort",
                     context_value=None, default=8000)
```

### Logging

Use the logger attached to context:

```python
context.logger.log_info("Starting deployment...")
context.logger.log_warning("PVC size mismatch, continuing")
context.logger.log_error("Deployment failed: timeout")
context.logger.set_indent(1)  # Indent subsequent messages
```

The `LoggerProtocol` (in `llmdbenchmark/executor/protocols.py`) defines the
interface: `log_info`, `log_warning`, `log_error`, `set_indent`.

### How Steps Share State

Steps communicate through `ExecutionContext` fields. For example:
- Standup step 06 (deploy) writes to `context.deployed_methods` and
  `context.deployed_endpoints`
- Smoketest phase reads `context.deployed_methods` to determine the pod
  selector
- Run step 06 (deploy harness) writes to `context.experiment_ids` and
  `context.deployed_pod_names`
- Run step 08 (collect results) reads `context.experiment_ids` to know which
  results to fetch

---

## 5. How to Add a New Analysis Module

### Where Analysis Code Lives

All analysis code is in `llmdbenchmark/analysis/`:

```
llmdbenchmark/analysis/
    __init__.py              # Main run_analysis() entry point
    cross_treatment.py       # Cross-treatment comparison (CSV + plots)
    per_request_plots.py     # Per-request distribution plots
    visualize_metrics.py     # Prometheus metrics visualization
    benchmark_report/        # Benchmark report format converters
        native_to_br0_1.py   # Convert harness output to BR v0.1
        native_to_br0_2.py   # Convert harness output to BR v0.2
        ...
```

### How to Add a New Plot Type

The analysis pipeline in `__init__.py` calls plotting functions in sequence.
To add a new plot:

1. Create a new module, e.g., `llmdbenchmark/analysis/my_custom_plots.py`:

```python
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llmdbenchmark.executor.context import ExecutionContext


def generate_my_plots(
    results_dir: Path,
    output_dir: Path | None = None,
    context: "ExecutionContext | None" = None,
) -> int:
    """Generate custom plots. Returns the number of plots generated."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return 0

    if output_dir is None:
        output_dir = results_dir / "analysis" / "custom"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load data, generate plots, save PNGs...
    # Return the count of generated plots
    return 0
```

2. Call it from `run_analysis()` in `llmdbenchmark/analysis/__init__.py`. Add a
   new section after the existing plot generation steps:

```python
# --- 6. Generate custom plots ---
from llmdbenchmark.analysis.my_custom_plots import generate_my_plots
try:
    count = generate_my_plots(results_dir, context=context)
    if count:
        _log(context, f"Generated {count} custom plot(s)")
except Exception as exc:
    _log(context, f"Custom plot generation failed: {exc}", warning=True)
```

### How to Add a New Metric to Cross-Treatment Comparison

Edit `METRICS_OF_INTEREST` in `llmdbenchmark/analysis/cross_treatment.py`. Each
entry is a tuple of `(dotted_path_in_br_v0.2, csv_column_name)`:

```python
METRICS_OF_INTEREST = [
    # ... existing metrics ...
    ("results.request_performance.aggregate.latency.time_to_first_token.mean", "ttft_mean_s"),
    # Add your new metric:
    ("results.custom_section.my_metric.value", "my_metric_value"),
]
```

To also generate a comparison bar chart for it, add an entry to the `plot_specs`
list in `_generate_comparison_plots()`:

```python
plot_specs = [
    # ... existing specs ...
    ("my_metric_value", "My Custom Metric", "unit", True),  # True = higher is better
]
```

### Analysis Pipeline Chain

The analysis pipeline runs in two stages:

1. **Per-treatment analysis** (`run_analysis()` in `__init__.py`):
   - Converts raw harness output to benchmark report v0.1 and v0.2 YAML
   - Extracts summary from stdout.log
   - Runs harness-specific post-processing (e.g., `inference-perf --analyze`)
   - Generates metric time-series plots from Prometheus data
   - Generates per-request distribution plots (histograms, CDFs, scatter)

2. **Cross-treatment analysis** (`generate_cross_treatment_summary()` in
   `cross_treatment.py`):
   - Reads benchmark report v0.2 files from all treatment subdirectories
   - Produces a CSV summary table (one row per treatment)
   - Generates comparison bar charts and scatter/line plots
   - Generates overlaid CDF plots comparing distributions across treatments

---

## 6. How to Add a New Harness

A harness is a load generator that sends requests to the model endpoint. Adding
one requires files in three areas.

### Required Files

1. **Harness script** in `workload/harnesses/`:
   Create `my-harness-llm-d-benchmark.sh` (or `.py`). This script is the
   entrypoint that the harness pod runs. It receives configuration through
   environment variables and the profile ConfigMap.

2. **Profile templates** in `workload/profiles/my-harness/`:
   Create `.yaml.in` files that define workload configurations. Use
   `REPLACE_ENV_*` placeholders for values injected at runtime:

   ```yaml
   # workload/profiles/my-harness/random_test.yaml.in
   executable: my-harness-binary
   model: REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL
   base-url: REPLACE_ENV_LLMDBENCH_HARNESS_STACK_ENDPOINT_URL
   num-requests: 100
   concurrency: 8
   ```

3. **Analysis integration** in `llmdbenchmark/analysis/__init__.py`:
   Register the harness in the module-level dictionaries:

   ```python
   _RESULT_PATTERNS["my-harness"] = "*.json"          # Glob for result files
   _WRITER_NAMES["my-harness"] = "my-harness"          # benchmark_report writer name
   _SUMMARY_MARKERS["my-harness"] = "Results Summary"  # Log marker for summary extraction
   ```

4. **Benchmark report converter** (optional but recommended):
   Add conversion functions to `llmdbenchmark/analysis/benchmark_report/`
   in both `native_to_br0_1.py` and `native_to_br0_2.py` so raw output
   can be normalized to the standard benchmark report format.

### Harness Pod Template

The harness pod template is at `config/templates/jinja/20_harness_pod.yaml.j2`.
It uses the `harness.name` config value to select the correct entrypoint script.
If your harness needs a custom container image, you may need to update the
Dockerfile as well.

---

## 7. How to Add a New Scenario (Well-Lit Path)

Scenarios define a deployment configuration (model, GPU type, vLLM settings,
etc.). They are the primary way users customize what gets deployed.

### File Structure

Each scenario requires two files:

1. **Specification template** (`.yaml.j2`) in `config/specification/`:
   Points to the defaults file, template directory, and scenario file.

2. **Scenario defaults** (`.yaml`) in `config/scenarios/`:
   Contains the actual configuration overrides.

### Step 1: Create the Specification Template

Create `config/specification/examples/my-scenario.yaml.j2`:

```yaml
# My Custom Scenario Specification
{% set base_dir = base_dir | default('../') -%}
base_dir: {{ base_dir }}

values_file:
  path: {{ base_dir }}/config/templates/values/defaults.yaml

template_dir:
  path: {{ base_dir }}/config/templates/jinja

scenario_file:
  path: {{ base_dir }}/config/scenarios/examples/my-scenario.yaml
```

The specification template is a Jinja2 file rendered by `RenderSpecification`
(in `llmdbenchmark/parser/render_specification.py`). The `base_dir` variable is
injected automatically from the project root or `--base-dir` CLI argument.

### Step 2: Create the Scenario File

Create `config/scenarios/examples/my-scenario.yaml`:

```yaml
scenario:
  - name: "my-custom-deployment"

    model:
      name: meta-llama/Llama-3.1-8B-Instruct

    vllmCommon:
      tensorParallelism: 2
      flags:
        disableLogRequests: true
        noPrefixCaching: true

    standalone:
      enabled: true
      replicas: 1

    modelservice:
      enabled: false

    harness:
      name: inference-perf
      experimentProfile: sanity_random.yaml
```

The `scenario` key is a list. Each entry becomes a "stack" that gets deployed.
Most scenarios define a single stack. Parameters not specified here inherit from
`config/templates/values/defaults.yaml`.

The deployment method must be exactly one of `standalone` or `modelservice`.
Both `standalone.enabled` and `modelservice.enabled` must be explicitly set so
that the rendered templates know which path to take.

### How Templates Are Rendered

1. `RenderSpecification` renders the `.yaml.j2` spec to resolve `base_dir`
   paths and writes the result as YAML.
2. `RenderPlans` (in `llmdbenchmark/parser/render_plans.py`) loads the defaults
   YAML and the scenario YAML, deep-merges them (scenario overrides defaults),
   then renders each Jinja2 template in `config/templates/jinja/` with the
   merged config values.
3. Output goes to one directory per stack under the plan directory (e.g.,
   `plan/my-custom-deployment/`).

### Custom Jinja2 Templates

To add infrastructure YAML that gets rendered per-stack, create a new `.yaml.j2`
file in `config/templates/jinja/`. Use a numeric prefix to control ordering:

```
config/templates/jinja/24_my-custom-resource.yaml.j2
```

Files prefixed with `_` are treated as partials/macros and are not rendered
directly. All other `.yaml.j2` files are rendered for each stack.

Templates receive the full merged config dictionary. Access values with Jinja2
syntax:

```yaml
# config/templates/jinja/24_my-custom-resource.yaml.j2
{% if myFeature is defined and myFeature.enabled %}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ release }}-my-config
  namespace: {{ namespace.name }}
data:
  setting: "{{ myFeature.setting | default('default-value') }}"
{% endif %}
```

### Usage

Run the scenario with:

```bash
llmdbenchmark --spec my-scenario standup
llmdbenchmark --spec my-scenario run
llmdbenchmark --spec my-scenario teardown
```

The `--spec` flag resolves to the specification file by searching
`config/specification/` directories for a matching name.

---

## 8. How to Add a Smoketest Validator

Smoketest validators run during the `SMOKETEST` phase to verify that a
scenario's rendered config matches expectations before the benchmark run begins.

### Create the Validator File

Add a new file in `llmdbenchmark/smoketests/validators/`, e.g.,
`my_scenario.py`. The file name must match the stack directory name -- for
example, a stack named `my-scenario` maps to `my_scenario.py`.

### Subclass ScenarioValidator

Extend `ScenarioValidator` from `llmdbenchmark/smoketests/validators/base.py`
and implement the `run_config_validation` method:

```python
from llmdbenchmark.smoketests.validators.base import ScenarioValidator


class MyScenarioValidator(ScenarioValidator):
    """Validate the my-scenario deployment configuration."""

    def run_config_validation(self, config, cmd, namespace, logger):
        # Validate that expected pods are running with the correct config.
        # Checks come from the rendered config.yaml, not hardcoded values.
        pods = self.get_pod_specs(cmd, namespace, label="app=my-scenario")

        self.validate_role_pods(pods, expected_count=1, role="server")
        self.assert_env_equals(pods[0], "MODEL_NAME", config["model"]["name"])
        self.assert_arg_contains(pods[0], "--tensor-parallel-size",
                                 str(config["vllmCommon"]["tensorParallelism"]))
```

### Available Helper Methods

- `validate_role_pods(pods, expected_count, role)` -- Assert the expected
  number of pods exist for a given role.
- `assert_env_equals(pod_spec, env_var, expected_value)` -- Check that a
  container environment variable has the expected value.
- `assert_arg_contains(pod_spec, arg_name, expected_value)` -- Check that a
  container argument contains the expected value.
- `get_pod_specs(cmd, namespace, label)` -- Retrieve pod specs filtered by
  label selector.

### Register the Validator

Add an entry to `VALIDATOR_REGISTRY` in
`llmdbenchmark/smoketests/validators/__init__.py`:

```python
from llmdbenchmark.smoketests.validators.my_scenario import MyScenarioValidator

VALIDATOR_REGISTRY = {
    # ... existing validators ...
    "my-scenario": MyScenarioValidator,
}
```

The registry key must match the stack directory name.

### Key Principles

- All checks should derive from the rendered `config.yaml`, not hardcoded
  values. This ensures the validator stays in sync with scenario changes.
- See `llmdbenchmark/smoketests/README.md` for the full check system
  documentation, including the complete list of built-in assertions and
  advanced usage patterns.

---

## 9. How to Add a New Experiment

Experiments define a Design of Experiments (DoE) matrix with setup treatments
(infrastructure variations) and run treatments (workload parameter sweeps).

### Experiment YAML Format

Create a file in `workload/experiments/`, e.g.,
`workload/experiments/my-experiment.yaml`:

```yaml
experiment:
  name: my-experiment
  description: >
    Measure throughput under increasing concurrency
    with different tensor parallelism settings.
  harness: vllm-benchmark
  profile: random_concurrent.yaml

design:
  type: factorial

  setup:
    factors:
      - name: tensor_parallelism
        key: vllmCommon.tensorParallelism
        levels: [1, 2, 4]

  run:
    factors:
      - name: max-concurrency
        key: max-concurrency
        levels: [1, 8, 32]

    constants:
      - key: num-prompts
        value: 100
      - key: dataset-name
        value: random

# Runtime: setup treatments consumed by the experiment orchestrator.
# Each treatment specifies config overrides applied during plan rendering.
setup:
  treatments:
    - name: tp1
      vllmCommon.tensorParallelism: 1
    - name: tp2
      vllmCommon.tensorParallelism: 2
    - name: tp4
      vllmCommon.tensorParallelism: 4

# Runtime: run treatments consumed by step_04 render_profiles.
# Each treatment specifies harness profile overrides.
treatments:
  - name: conc1
    max-concurrency: 1
    num-prompts: 100
  - name: conc8
    max-concurrency: 8
    num-prompts: 100
  - name: conc32
    max-concurrency: 32
    num-prompts: 100
```

### Setup Treatments vs Run Treatments

**Setup treatments** (`setup.treatments`) control the infrastructure deployed.
Each treatment triggers a full standup/run/teardown cycle. Override keys use
dotted paths into the scenario config (e.g., `vllmCommon.tensorParallelism`,
`decode.replicas`, `standalone.enabled`). These overrides are passed to
`RenderPlans` as `setup_overrides` and deep-merged into the scenario config
during template rendering.

**Run treatments** (`treatments`) control the workload parameters for each
benchmark run. Each treatment overrides profile values (e.g., `max-concurrency`,
`num-prompts`). Within a single setup treatment, all run treatments execute
sequentially against the same deployed stack.

### How to Reference Scenario Overrides

Setup treatment keys map directly to the scenario YAML structure. For example:

```yaml
setup:
  treatments:
    - name: pd-2decode
      decode.replicas: 2              # Sets scenario[].decode.replicas
      prefill.replicas: 4             # Sets scenario[].prefill.replicas
      standalone.enabled: false       # Disables standalone mode
```

### Running an Experiment

```bash
llmdbenchmark --spec my-scenario experiment \
  --experiments workload/experiments/my-experiment.yaml
```

The experiment orchestrator (`_execute_experiment` in `llmdbenchmark/cli.py`):

1. Parses the experiment YAML via `parse_experiment()`
2. Falls back experiment-level `harness` and `profile` to CLI args if not
   provided
3. For each setup treatment:
   - Renders plans with the treatment's config overrides
   - Runs standup
   - Runs all run treatments (each as a separate run phase)
   - Runs teardown (unless `--skip-teardown`)
4. Records success/failure per treatment in `ExperimentSummary`
5. Writes `experiment-summary.yaml` to the workspace

Options:
- `--stop-on-error`: Abort on first failure (default: continue to next treatment)
- `--skip-teardown`: Leave stacks running for debugging
