# llmdbenchmark.executor

Execution framework for running standup, run, and teardown phases. Provides step orchestration, command execution, shared context, and dependency checking.

## Architecture

The executor follows a three-tier execution model:

1. **Pre-global steps** -- Global steps (not per-stack) whose step number is lower than the lowest per-stack step. Run sequentially before any per-stack work.
2. **Per-stack steps** -- Steps that execute once per rendered stack. Run in parallel across stacks (up to `max_parallel_stacks`), but sequentially within each stack.
3. **Post-global steps** -- Global steps whose step number is higher than the lowest per-stack step. Run after all per-stack work completes.

Execution aborts on the first failed global step. Per-stack failures are isolated to the failing stack -- other stacks continue.

## Files

```
executor/
├── __init__.py          -- Package docstring
├── context.py           -- ExecutionContext dataclass
├── step.py              -- Step ABC, Phase enum, result types
├── step_executor.py     -- StepExecutor orchestrator
├── command.py           -- CommandExecutor (shell commands)
├── deps.py              -- System dependency checker
└── protocols.py         -- LoggerProtocol interface
```

## Step (`step.py`)

`Step` is the abstract base class for all pipeline steps across standup, run, and teardown.

### Phase Enum

```python
class Phase(Enum):
    STANDUP = "standup"
    SMOKETEST = "smoketest"
    RUN = "run"
    TEARDOWN = "teardown"
```

### Step ABC

```python
class Step(ABC):
    number: int          # Determines execution order
    name: str            # Machine-readable identifier
    description: str     # Human-readable description (logged during execution)
    phase: Phase         # Which lifecycle phase this step belongs to
    per_stack: bool      # False = global, True = runs once per rendered stack
```

Key methods:

- `execute(context, stack_path=None) -> StepResult` -- Abstract. Run the step logic and return a result.
- `should_skip(context) -> bool` -- Override for conditional skip logic (e.g., skip standalone steps in modelservice mode).
- `_resolve(plan_config, *config_paths, context_value=None, default=None)` -- Three-tier value resolution: (1) runtime override from CLI/context, (2) nested lookup in plan config via dotted paths, (3) default value. Supports fallback chains with multiple config paths.
- `_require_config(config, *keys)` -- Traverse a nested key path, raising `KeyError` if any key is missing.
- `_load_plan_config(context)` -- Load `config.yaml` from the first rendered stack.
- `_load_stack_config(stack_path)` -- Load `config.yaml` from a specific stack directory.
- `_all_target_namespaces(context)` -- Collect deduplicated namespaces from all rendered stacks with context-level fallback. Raises `RuntimeError` if no namespace is configured.
- `_find_rendered_yaml(context, prefix)` -- Find a rendered YAML file by filename prefix across all stacks.
- `_find_yaml(stack_path, prefix)` -- Find a YAML file by prefix in a single stack directory.
- `_parse_size_gi(size_str)` -- Parse Kubernetes quantity strings (`300Gi`, `1Ti`, `500Mi`) to GiB.
- `_check_existing_pvc(cmd, context, pvc_name, requested_size, namespace, errors)` -- Check if a PVC exists and validate its size against the requested size. Returns `True` if the PVC exists (caller should skip creation).
- `_has_yaml_content(yaml_path)` -- Check if a YAML file has non-empty content (skips conditionally-empty templates).

### Result Types

- `StepResult` -- Result of a single step: `step_number`, `step_name`, `success`, `message`, `errors`, `stack_name`, `context`.
- `StackExecutionResult` -- Aggregated results for one stack: `stack_name`, `stack_path`, `step_results`. Exposes `has_errors` and `failed_steps`.
- `ExecutionResult` -- Full phase result: `phase`, `global_results`, `stack_results`, `errors`. Provides `summary()` for human-readable output.

## StepExecutor (`step_executor.py`)

Phase-agnostic orchestrator. Takes a list of `Step` objects and an `ExecutionContext`, partitions them into pre-global, per-stack, and post-global groups, and executes them.

```python
class StepExecutor:
    def __init__(self, steps, context, logger, max_parallel_stacks=4): ...
    def execute(self, step_spec=None) -> ExecutionResult: ...
    def parse_step_list(self, step_spec: str) -> list[int]: ...
```

Key behaviors:

- **Step filtering** -- Accepts spec strings like `"0,3-5,9"` to run only specific steps.
- **Cluster resolution** -- Calls `context.resolve_cluster()` before execution if not already resolved.
- **Partitioning** -- `_partition_steps()` splits steps by the boundary of the lowest per-stack step number. Global steps below that boundary run first; global steps at or above run after per-stack work.
- **Parallel per-stack execution** -- Uses `ThreadPoolExecutor` with `max_parallel_stacks` workers. Single-stack scenarios skip the thread pool.
- **Error handling** -- Global step failure aborts the entire phase. Per-stack step failure aborts that stack but does not affect others. Uncaught exceptions are wrapped in failed `StepResult` objects.

## CommandExecutor (`command.py`)

Shell command executor with dry-run, retry, logging, and output capture. Uses `oc` instead of `kubectl` when `openshift=True`.

```python
class CommandExecutor:
    def __init__(self, work_dir, dry_run, verbose, logger=None,
                 kubeconfig=None, kube_context=None, openshift=False): ...
```

### Core Methods

- `execute(cmd, attempts=1, *, fatal=False, silent=True, delay=10, check=True, force=False) -> CommandResult` -- Run a shell command with optional retry. When `force=True`, the command runs even in dry-run mode (used for local-only reads like `kubectl config view`). Raises `ExecutionError` when `fatal=True` and the command fails.
- `kube(*args, namespace=None, check=True, force=False) -> CommandResult` -- Execute kubectl/oc with auto-injected `--kubeconfig` and `--context` flags.
- `helm(*args, check=True) -> CommandResult` -- Execute helm with auto-injected kubeconfig flags.
- `helmfile(*args) -> CommandResult` -- Execute helmfile with auto-injected kubeconfig flags.

### Wait Helpers

All wait helpers show live terminal progress with progress bars and pod status. In dry-run mode, they log the would-be command and return success immediately.

- `wait_for_pods(label, namespace, timeout=300, poll_interval=10, description="") -> CommandResult` -- Poll pods by label until all are Ready. Detects and aborts on terminal states (`CrashLoopBackOff`, `OOMKilled`, `ImagePullBackOff`, etc.).
- `wait_for_job(job_name, namespace, timeout=3600, poll_interval=15, description="") -> CommandResult` -- Poll a Job until it completes or fails. Tracks active/succeeded/failed counts.
- `wait_for_pvc(pvc_name, namespace, timeout=300, poll_interval=10, description="") -> CommandResult` -- Poll a PVC until it reaches `Bound` phase.

### CommandResult

```python
@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    dry_run: bool = False
    attempts: int = 1

    @property
    def success(self) -> bool: ...  # exit_code == 0
```

### Dry-Run Behavior

In dry-run mode, `execute()` logs what would have been run and returns a `CommandResult` with `exit_code=0` and `dry_run=True`. The `force=True` parameter overrides this for local-only reads that subsequent commands depend on.

All commands are logged to timestamped files under `workspace/setup/commands/`.

## ExecutionContext (`context.py`)

Mutable dataclass populated incrementally across phases. Shared by all steps and the `CommandExecutor`.

### Core Fields

| Field | Type | Description |
|-------|------|-------------|
| `plan_dir` | `Path` | Directory for rendered plans |
| `workspace` | `Path` | Working directory for this session |
| `base_dir` | `Path or None` | Project root (for templates, scenarios) |
| `specification_file` | `str or None` | Resolved `--spec` path |
| `rendered_stacks` | `list[Path]` | Paths to rendered stack directories |
| `dry_run` | `bool` | Dry-run mode (no cluster modifications) |
| `verbose` | `bool` | Enable debug-level logging |
| `non_admin` | `bool` | Skip cluster-admin operations |
| `current_phase` | `Phase` | Active lifecycle phase |
| `deep_clean` | `bool` | Teardown: wipe all resources in namespaces |
| `release` | `str` | Helm release name prefix (default: `"llmdbench"`) |

### Kubernetes Connection

| Field | Type | Description |
|-------|------|-------------|
| `cluster_url` | `str or None` | API server URL |
| `cluster_token` | `str or None` | Bearer token |
| `kubeconfig` | `str or None` | Path to kubeconfig file |
| `is_openshift` | `bool` | True if OpenShift API groups detected |
| `is_kind` | `bool` | True if Kind cluster detected |
| `is_minikube` | `bool` | True if Minikube detected |
| `cluster_name` | `str or None` | Hostname from API server URL |
| `cluster_server` | `str or None` | Full API server URL |
| `context_name` | `str or None` | Kube context name |
| `username` | `str or None` | OS username for labeling |

### Namespace and Model

| Field | Type | Description |
|-------|------|-------------|
| `namespace` | `str or None` | Primary deployment namespace |
| `harness_namespace` | `str or None` | Harness pod namespace |
| `wva_namespace` | `str or None` | WVA namespace |
| `model_name` | `str or None` | Model HuggingFace ID |
| `proxy_uid` | `int or None` | OpenShift UID range for proxy pods |

### Deployed State

| Field | Type | Description |
|-------|------|-------------|
| `deployed_endpoints` | `dict[str, str]` | Stack name to endpoint URL |
| `deployed_methods` | `list[str]` | Active deploy methods |
| `accelerator_resource` | `str or None` | GPU resource key (e.g. `nvidia.com/gpu`) |
| `network_resource` | `str or None` | Network resource key (e.g. `rdma/rdma_shared_device_a`) |
| `deployed_pod_names` | `list[str]` | Harness pod names from step 06 |
| `experiment_ids` | `list[str]` | Experiment IDs from step 06 |

### Run Configuration

| Field | Type | Description |
|-------|------|-------------|
| `harness_name` | `str or None` | Active harness (e.g. `inference-perf`) |
| `harness_profile` | `str or None` | Workload profile name |
| `harness_output` | `str` | Output destination (default: `"local"`) |
| `harness_parallelism` | `int` | Parallel harness pods (default: 1) |
| `harness_wait_timeout` | `int` | Seconds to wait for completion (default: 3600) |
| `harness_debug` | `bool` | Start pods with `sleep infinity` |
| `harness_service_account` | `str or None` | Service account for harness pods |
| `harness_envvars_to_pod` | `str or None` | Env vars to propagate to pods |
| `endpoint_url` | `str or None` | Explicit endpoint (run-only mode) |
| `run_config_file` | `str or None` | Run config YAML path |
| `analyze_locally` | `bool` | Run local analysis after collection |

### Key Methods

- `rebuild_cmd() -> CommandExecutor` -- Create or recreate the shared `CommandExecutor` from current context fields.
- `resolve_cluster()` -- Resolve cluster connectivity and metadata (idempotent).
- `require_cmd() -> CommandExecutor` -- Return the `CommandExecutor`, raising if not initialized.
- `require_namespace() -> str` -- Return the namespace, raising if not configured.
- `platform_type -> str` -- Human-readable platform label (`"OpenShift"`, `"Kind"`, `"Minikube"`, `"Kubernetes"`).
- `is_run_only_mode -> bool` -- True when running against an existing stack.
- Directory helpers: `setup_commands_dir()`, `setup_yamls_dir()`, `setup_logs_dir()`, `setup_helm_dir()`, `environment_dir()`, `run_dir()`, `run_results_dir()`, `run_analysis_dir()`, `workload_profiles_dir()`, `preprocess_dir()`.

## LoggerProtocol (`protocols.py`)

Structural typing interface for loggers used throughout the pipeline. Any object implementing these methods satisfies the protocol -- no explicit inheritance required.

```python
@runtime_checkable
class LoggerProtocol(Protocol):
    def log_info(self, msg: str, *, emoji: str = "") -> None: ...
    def log_warning(self, msg: str) -> None: ...
    def log_error(self, msg: str) -> None: ...
    def set_indent(self, level: int) -> None: ...
```

Both `_MinimalLogger` in `command.py` and `LLMDBenchmarkLogger` in `logging/logger.py` conform to this interface.

## Dependency Checker (`deps.py`)

Checks for required and optional CLI tools on `$PATH`.

```python
REQUIRED_TOOLS = ["kubectl", "helm", "helmfile", "jq", "yq"]
OPTIONAL_TOOLS = ["oc", "kustomize", "skopeo", "rsync", "make"]

def check_system_dependencies(required_only=False, extra_required=None) -> DependencyCheckResult: ...
def check_python_version() -> tuple[bool, str]: ...  # Requires Python >= 3.11
```

`DependencyCheckResult` provides `has_missing_required` and a `summary()` method.

## Writing a New Step

1. Create a new file under the appropriate phase directory (e.g. `standup/steps/step_NN_name.py`).
2. Subclass `Step` and call `super().__init__()` with the step number, name, description, phase, and `per_stack` flag.
3. Implement `execute(self, context, stack_path=None) -> StepResult`.
4. Optionally override `should_skip(self, context) -> bool` for conditional execution.
5. Register the step in the phase's `steps/__init__.py` `get_*_steps()` function.

Example:

```python
from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext

class MyStep(Step):
    def __init__(self):
        super().__init__(
            number=12,
            name="my_step",
            description="Do something useful",
            phase=Phase.STANDUP,
            per_stack=True,  # runs once per stack
        )

    def execute(self, context, stack_path=None):
        cmd = context.require_cmd()
        config = self._load_stack_config(stack_path)
        # ... step logic ...
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message="Done",
        )
```

Use `_resolve()` for three-tier config lookups and `_require_config()` for mandatory keys. Use `cmd.kube()`, `cmd.helm()`, or `cmd.execute()` for shell commands -- they handle kubeconfig injection, dry-run, retry, and logging automatically.
