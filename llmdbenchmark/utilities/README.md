# Utilities Module

Shared utilities used across all phases of `llmdbenchmark`. Provides Kubernetes integration, capacity validation, HuggingFace access checks, and OS helpers.

## Module Structure

```text
utilities/
    __init__.py
    cluster.py              Kubernetes connection, platform detection
    capacity_validator.py   GPU capacity validation
    huggingface.py          HuggingFace model access checks
    endpoint.py             Endpoint discovery and model verification
    profile_renderer.py     Workload profile template rendering
    kube_helpers.py         Shared kubectl patterns (wait, collect, cleanup)
    cloud_upload.py         Unified cloud storage upload (GCS, S3)
    os/
        __init__.py
        filesystem.py       Workspace and directory management
        platform.py         Host OS detection
```

## Components

### cluster.py -- Kubernetes Integration

One-stop cluster setup and platform detection:

| Function | Description |
|----------|-------------|
| `resolve_cluster(context)` | Main entry point: resolves kubeconfig, connects, detects platform, populates context |
| `kube_connect(kubeconfig, context)` | Establishes Python Kubernetes client connection |
| `is_openshift(v1)` | Detects OpenShift by checking for `openshift` API group |
| `detect_kind(cmd)` | Detects Kind cluster from kubeconfig context |
| `detect_minikube(cmd)` | Detects Minikube cluster |
| `get_service_endpoint(name, ns, client)` | Looks up a Service's ClusterIP endpoint |
| `get_gateway_address(name, ns)` | Looks up a Gateway CR's address |
| `print_phase_banner(phase, context, logger)` | Prints formatted phase start banner |

**Platform detection priority:** OpenShift > Kind > Minikube > vanilla Kubernetes

**Context population:** `resolve_cluster()` sets these fields on `ExecutionContext`:
- `is_openshift`, `is_kind`, `is_minikube`
- `cluster_name`, `cluster_server`, `context_name`, `username`
- `kubeconfig` (resolved to absolute path)
- Builds the shared `CommandExecutor`

### capacity_validator.py -- GPU Capacity

Validates that the cluster has sufficient GPU resources for the requested deployment:
- Checks node labels for GPU types
- Compares requested GPU count against available capacity
- Validates node affinity constraints
- Reports capacity shortfalls before deployment starts

### huggingface.py -- Model Access

Verifies access to HuggingFace models before standup:

| Function/Class | Description |
|-----------------|-------------|
| `check_model_access(model_id, hf_token)` | Verify access to a HuggingFace model |
| `GatedStatus` | Enum: `NOT_GATED`, `GATED`, `GATED_NO_ACCESS` |
| `AccessResult` | Result with `ok`, `gated`, `detail` fields |

**Fail-fast behavior:** Called before any standup steps run. If a model is gated and the token doesn't have access, the tool exits immediately with a clear error message.

### os/filesystem.py -- Workspace Management

Directory creation and path resolution utilities:

| Function | Description |
|----------|-------------|
| `create_workspace(path)` | Create the main workspace directory (or a temp dir if `None`) |
| `create_sub_dir_workload(parent, sub_dir)` | Create a timestamped subdirectory for the current run |
| `create_directory(path, exist_ok)` | Create directory with parents |
| `create_tmp_directory(prefix, suffix, base_dir)` | Create a temporary directory |
| `get_absolute_path(path)` | Resolve relative/tilde paths to absolute |
| `directory_exists_and_nonempty(path)` | Check if directory exists and has contents |
| `file_exists_and_nonzero(path)` | Check if file exists and has non-zero size |
| `copy_directory(source, destination, overwrite)` | Copy entire directory tree |
| `remove_directory(path)` | Recursively remove a directory and all contents |

### os/platform.py -- Host Detection

| Function/Class | Description |
|-----------------|-------------|
| `PlatformInfo` | Immutable dataclass with `system`, `machine`, `is_mac`, `is_linux` |
| `get_platform_info()` | Returns `PlatformInfo` for the current host |
| `get_platform_dict()` | Returns platform info as a dictionary |
| `get_user_id()` | Returns the current system username |

### endpoint.py -- Endpoint Discovery

Endpoint detection and model verification functions used by run-phase steps 02 and 03:

| Function | Description |
|----------|-------------|
| `find_standalone_endpoint(cmd, namespace, inference_port)` | Find standalone service by `stood-up-from=llm-d-benchmark` label |
| `find_gateway_endpoint(cmd, namespace, release)` | Discover gateway IP with automatic HTTPS detection |
| `find_custom_endpoint(cmd, namespace, method_pattern)` | Multi-level fallback: service match → pod match |
| `discover_hf_token_secret(cmd, namespace)` | Auto-discover HuggingFace token secret from cluster |
| `extract_hf_token_from_secret(cmd, namespace, secret, key)` | Extract and decode HF token from Kubernetes secret |
| `validate_model_response(stdout, expected_model, host, port)` | Validate `/v1/models` response contains expected model |
| `test_model_serving(cmd, namespace, host, port, model, ...)` | Test endpoint via ephemeral curl pod with retries |

**Retry behavior:** `test_model_serving()` retries on transient failures (503, 502, "not ready", "still loading") up to `max_retries` times.

### profile_renderer.py -- Profile Template Rendering

Regex-based substitution engine for `.yaml.in` workload profile templates:

| Function | Description |
|----------|-------------|
| `build_env_map(plan_config, runtime_values)` | Build substitution map from token registry and config |
| `render_profile(template_content, env_map)` | Replace `REPLACE_ENV_{KEY}` tokens in template string |
| `render_profile_file(source, dest, env_map)` | Render template file and write result |
| `apply_overrides(profile_content, overrides)` | Apply dotted key=value overrides to rendered YAML |

**Token registry:** `PROFILE_TOKENS` maps token suffixes to config paths:

| Token Suffix | Config Path | Description |
|-------------|-------------|-------------|
| `LLMDBENCH_DEPLOY_CURRENT_MODEL` | `model.name` | Model identifier |
| `LLMDBENCH_DEPLOY_CURRENT_TOKENIZER` | `model.name` | Tokenizer (defaults to model) |
| `LLMDBENCH_HARNESS_STACK_ENDPOINT_URL` | Runtime only | Endpoint URL |
| `LLMDBENCH_RUN_DATASET_DIR` | `experiment.datasetDir` | Dataset directory |

### kube_helpers.py -- Shared Kubernetes Patterns

Common kubectl operations extracted from run-phase steps to eliminate duplication:

| Function | Description |
|----------|-------------|
| `find_data_access_pod(cmd, namespace)` | Find data-access pod by label |
| `wait_for_pods_by_label(cmd, label, namespace, timeout, context)` | Two-phase wait: Ready → Completed, with crash detection |
| `wait_for_pod(cmd, pod_name, namespace, timeout, context, poll_interval)` | Per-pod polling fallback |
| `collect_pod_results(cmd, data_pod, namespace, prefix, id, idx, dir, context)` | Copy results from PVC for a single pod |
| `sync_analysis_dir(local_path, analysis_dir, suffix)` | Move analysis subdirectory out of results |
| `delete_pods_by_names(cmd, pod_names, namespace, context)` | Delete pods by individual name |
| `delete_pods_by_label(cmd, label, namespace, context)` | Delete all pods matching a label selector |
| `capture_pod_logs(cmd, pod_names, namespace, log_dir, context)` | Capture logs from harness pods |
| `capture_label_logs(cmd, namespace, label, dest, label_name, context)` | Capture aggregated logs by label |
| `capture_infrastructure_logs(cmd, namespace, log_dir, model_label, context)` | Capture pod status and infrastructure logs |

**Constants:**

| Constant | Value | Description |
|----------|-------|-------------|
| `CRASH_STATES` | `{CrashLoopBackOff, Error, OOMKilled, ...}` | Terminal error pod states |
| `DATA_ACCESS_LABEL` | `role=llm-d-benchmark-data-access` | Label for data-access pods |

### cloud_upload.py -- Cloud Storage Upload

Unified upload implementation for GCS and S3, used by step 06 (per-pod) and step 09 (bulk):

| Function | Description |
|----------|-------------|
| `upload_results_dir(cmd, local_path, output, context, relative_path)` | Upload single directory to cloud storage |
| `upload_all_results(cmd, results_dir, output, context)` | Bulk upload entire results directory |

**Supported destinations:** `gs://` (Google Cloud Storage via `gcloud storage cp`), `s3://` (Amazon S3 via `aws s3 cp`), `local` (no-op).

## Workspace Architecture

The workspace system provides isolated, timestamped directories for each run of `llmdbenchmark`. This ensures multiple runs never interfere with each other and all artifacts are traceable.

### Directory Hierarchy

```text
workspace_llmdbench_xyz/              Overall workspace (--workspace or temp dir)
└── {user}-{YYYYMMDD-HHMMSS-mmm}/    Run workspace (auto-generated per invocation)
    ├── plan/                         Rendered templates and config.yaml per stack
    │   ├── stack-1/
    │   │   ├── config.yaml           Merged configuration (single source of truth)
    │   │   ├── 01_namespace.yaml     Rendered Kubernetes manifest
    │   │   ├── 06_standalone.yaml    ...
    │   │   └── ...
    │   └── stack-2/
    │       └── ...
    └── logs/                         All log files for this run
        ├── {user}-{ts}-{uuid}-stdout.log   Per-instance info/debug
        ├── {user}-{ts}-{uuid}-stderr.log   Per-instance warnings/errors
        ├── llmdbenchmark-stdout.log        Combined info/debug
        └── llmdbenchmark-stderr.log        Combined warnings/errors
```

### Workspace Creation Flow

The workspace is set up in `cli.py` before any command executes:

1. **Overall workspace** -- `create_workspace()` creates the top-level directory. If `--workspace` is provided, that path is used (with `workspace_` prefixed if not already present). Otherwise, a temp directory is created via `tempfile.mkdtemp()`.

2. **Run workspace** -- `create_sub_dir_workload()` creates a unique subdirectory for this invocation using the pattern `{username}-{YYYYMMDD-HHMMSS-mmm}`. This means each run gets its own isolated directory.

3. **Subdirectories** -- `plan/` and `logs/` are created inside the run workspace via `create_sub_dir_workload(parent, "plan")` and `create_sub_dir_workload(parent, "logs")`.

4. **Config singleton** -- The paths are stored in the `WorkspaceConfig` singleton (see below) so all modules can access them.

### WorkspaceConfig Singleton

A package-wide dataclass instance in `llmdbenchmark/config.py`:

```python
@dataclass
class WorkspaceConfig:
    workspace: Optional[Path] = None    # Run workspace path
    plan_dir: Optional[Path] = None     # plan/ subdirectory
    log_dir: Optional[Path] = None      # logs/ subdirectory
    verbose: bool = False               # --verbose flag
    dry_run: bool = False               # --dry-run flag

config = WorkspaceConfig()  # Single package-wide instance
```

Configured once during CLI startup via `setup_workspace()`, then imported by any module that needs workspace paths:

```python
from llmdbenchmark.config import config

plan_path = config.plan_dir / "stack-1" / "config.yaml"
```

### Workspace Naming

| Component | Format | Example |
|-----------|--------|---------|
| Overall workspace | `workspace_{name}` or temp dir | `workspace_llmdbench_abc123/` |
| Run subdirectory | `{user}-{YYYYMMDD-HHMMSS-mmm}` | `vezio-20260309-141523-042/` |
| Plan subdirectory | `plan/` | `plan/` |
| Logs subdirectory | `logs/` | `logs/` |

The `workspace_` prefix is enforced to prevent accidentally storing artifacts in the git repository (`.gitignore` includes `workspace*/`).

### Specifying a Workspace

```bash
# Auto-generated temp directory (default)
llmdbenchmark --spec spec.yaml.j2 standup

# Explicit workspace (reusable across runs)
llmdbenchmark --spec spec.yaml.j2 --workspace ./my-workspace standup

# Each run creates a new timestamped subdirectory inside the workspace
```

Using `--workspace` allows consolidating multiple runs into a single parent directory for easier comparison:

```text
my-workspace/
├── vezio-20260309-141523-042/    # First run
│   ├── plan/
│   └── logs/
├── vezio-20260309-150012-891/    # Second run
│   ├── plan/
│   └── logs/
└── vezio-20260310-091045-123/    # Third run
    ├── plan/
    └── logs/
```

### Design Principles

- **No global state in utilities** -- Functions take explicit path parameters; global state lives only in `config.py`
- **Path normalization** -- All functions normalize inputs to `Path` objects internally
- **Return Path objects** -- Consistent return type across all path operations
- **No silent failures** -- Filesystem errors surface as standard Python exceptions (`OSError`, `ValueError`)
