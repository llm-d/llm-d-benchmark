# llmdbenchmark.executor

Execution framework for running standup, run, and teardown phases. Provides step orchestration, command execution, shared context, and dependency checking.

## Architecture

The executor follows a three-tier execution model:

1. **Pre-global steps** -- Global steps (not per-stack) whose step number is lower than the lowest per-stack step. Run sequentially before any per-stack work.
2. **Per-stack steps** -- Steps that execute once per rendered stack. Run in parallel across stacks (up to `max_parallel_stacks`), but sequentially within each stack.
3. **Post-global steps** -- Global steps whose step number is higher than the lowest per-stack step. Run after all per-stack work completes.

Execution aborts on the first failed global step. Per-stack failures are isolated to the failing stack.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Package docstring |
| `context.py` | `ExecutionContext` dataclass -- shared state carried through all pipeline phases (paths, flags, cluster info, deployment state, run config) |
| `step.py` | `Step` abstract base class, `Phase` enum (`STANDUP`/`RUN`/`TEARDOWN`), result types (`StepResult`, `StackExecutionResult`, `ExecutionResult`) |
| `step_executor.py` | `StepExecutor` -- phase-agnostic orchestrator with step partitioning, sequential global execution, and parallel per-stack execution via `ThreadPoolExecutor` |
| `command.py` | `CommandExecutor` -- shell command execution with dry-run, retry, logging, and output capture. Provides `kube()`, `helm()`, `helmfile()` convenience methods and `wait_for_pods()`/`wait_for_job()`/`wait_for_pvc()` polling helpers with live progress display |
| `deps.py` | System dependency checker for required CLI tools (kubectl, helm, helmfile, jq, yq) and optional tools (oc, kustomize, skopeo) |
| `protocols.py` | `LoggerProtocol` -- structural typing interface for loggers used throughout the pipeline |

## Key Classes

- **`ExecutionContext`** -- Mutable dataclass populated incrementally across phases. Holds core paths, execution flags, Kubernetes connection info, namespace config, deployed state, harness configuration, and the shared `CommandExecutor` instance.
- **`Step`** -- Abstract base with `execute()` and `should_skip()`. Provides config resolution helpers (`_resolve()`, `_require_config()`, `_load_plan_config()`), YAML discovery, and PVC size validation.
- **`StepExecutor`** -- Takes a list of steps and an `ExecutionContext`, partitions them, and runs the three-tier execution model. Supports step filtering via spec strings like `"0,3-5,9"`.
- **`CommandExecutor`** -- Wraps `subprocess.run` with kubeconfig injection, retry loops, dry-run support, and structured `CommandResult` objects. Uses `oc` instead of `kubectl` when `openshift=True`.
