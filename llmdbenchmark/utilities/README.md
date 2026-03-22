# llmdbenchmark.utilities

Shared helper functions used across multiple phases. Provides Kubernetes helpers, endpoint detection, cloud upload, capacity validation, HuggingFace access checks, workload profile rendering, and OS-level utilities.

## Files

```
utilities/
├── __init__.py            -- Empty package marker
├── cluster.py             -- Cluster connectivity and platform detection
├── capacity_validator.py  -- GPU memory / KV cache validation
├── endpoint.py            -- Endpoint discovery and model verification
├── kube_helpers.py        -- Pod lifecycle helpers
├── cloud_upload.py        -- GCS/S3 upload
├── huggingface.py         -- HuggingFace Hub access checks
├── profile_renderer.py    -- Workload profile template renderer
└── os/
    ├── __init__.py        -- Empty package marker
    ├── filesystem.py      -- Filesystem utilities
    └── platform.py        -- Platform detection
```

## cluster.py -- Cluster Connectivity and Platform Detection

Main entry point for cluster setup during step 00 of every phase.

### `resolve_cluster(context: ExecutionContext) -> None`

Connects to the cluster, detects platform type, stores a self-contained `context.ctx` kubeconfig in the workspace, and resolves cluster metadata (server URL, hostname, kube context, username). Runs fully even in dry-run mode so that subsequent dry-run commands use the canonical kubeconfig path.

Internally calls:
1. `_kube_api_connect()` -- Connect via Kubernetes Python client; set `context.is_openshift`.
2. `_detect_local_platform()` -- Detect Kind or Minikube by inspecting kube-system pods.
3. `_store_kubeconfig()` -- Create self-contained `context.ctx` from provided kubeconfig, named config, current context extraction, or OpenShift `oc login`.
4. `_resolve_cluster_metadata()` -- Populate `cluster_server`, `cluster_name`, `context_name`, `username`.

### `kube_connect(kubeconfig=None, kube_context=None, cluster_url=None, token=None) -> ApiClient`

Establish a Kubernetes API connection. Resolution order:
1. Explicit kubeconfig file
2. Cluster URL + token (direct API config, SSL verification disabled)
3. Default kubeconfig (`~/.kube/config`)
4. In-cluster config

### Other Functions

- `is_openshift(api_client) -> bool` -- Check for OpenShift API groups.
- `get_service_endpoint(api_client, namespace, service_name) -> str | None` -- Return `ip:port` for a Service.
- `get_gateway_address(api_client, namespace, gateway_name) -> str | None` -- Return first address from a Gateway CR's status.
- `load_stacks_info(context) -> list[dict]` -- Read per-stack config (name, namespace, model, method) from rendered stacks.
- `print_phase_banner(context, extra_fields=None)` -- Print a bordered phase summary banner with cluster, stack, and model info.

## capacity_validator.py -- GPU Memory and KV Cache Validation

Validates vLLM deployment parameters against model and GPU hardware constraints using the `config_explorer.capacity_planner` library.

### `run_capacity_planner(plan_config, logger, ignore_failures=False) -> list[str]`

Run capacity validation for all active deployment methods (standalone, decode, prefill). Returns diagnostic messages. When `ignore_failures=False`, errors indicate deployment will fail.

Checks performed:
- GPU count: `TP x PP x DP` must not exceed requested accelerators per pod.
- Valid tensor parallelism values for the model architecture.
- `maxModelLen` does not exceed the model's maximum context length.
- GPU memory sufficient to load model weights + activation memory.
- KV cache memory sufficient for at least one request at `maxModelLen`.
- Maximum concurrent request estimation.

### `ValidationParams`

```python
@dataclass
class ValidationParams:
    models: list[str]         # HuggingFace model IDs
    hf_token: str | None      # For gated model access
    replicas: int
    gpu_memory: int            # Per-GPU memory in GB (0 = skip GPU checks)
    tp: int                    # Tensor parallelism
    pp: int                    # Pipeline parallelism
    dp: int                    # Data parallelism
    accelerator_nr: int        # GPUs requested per pod
    gpu_memory_util: float     # 0.0 to 1.0
    max_model_len: int
    ignore_failures: bool
    label: str                 # e.g. "standalone", "decode", "prefill"
```

## endpoint.py -- Endpoint Discovery and Model Verification

### Endpoint Discovery

- `find_standalone_endpoint(cmd, namespace, inference_port=80) -> (ip, service_name, port)` -- Find standalone services labelled `stood-up-from=llm-d-benchmark`.
- `find_gateway_endpoint(cmd, namespace, release) -> (ip_or_hostname, gateway_name, port)` -- Find the gateway IP from Gateway resource status. Detects HTTPS (port 443) from managed fields. Falls back to service ClusterIP.
- `find_custom_endpoint(cmd, namespace, method_pattern) -> (ip, name, port)` -- Multi-level fallback for non-standard deployments: service name match (check port names `default`, `http`, `https`), then pod name match (probe ports, metrics port, pod IP).

### HuggingFace Token Discovery

- `discover_hf_token_secret(cmd, namespace) -> str | None` -- Find secrets matching `llm-d-hf.*token` pattern.
- `extract_hf_token_from_secret(cmd, namespace, secret_name, key="HF_TOKEN") -> str | None` -- Decode HF token from K8s Secret. Falls back to scanning all data values for `hf_` prefix.

### Model Verification

- `test_model_serving(cmd, namespace, host, port, expected_model, ...) -> str | None` -- Query `/v1/models` via ephemeral curl pod. Retries up to `max_retries` (default 12) with `retry_interval` (default 15s) on transient failures (503, "not ready", "still loading"). Returns `None` on success.
- `validate_model_response(stdout, expected_model, host, port) -> str | None` -- Check that the `/v1/models` response JSON contains the expected model ID.

## kube_helpers.py -- Pod Lifecycle Helpers

Shared kubectl patterns for the run phase.

### Constants

- `CRASH_STATES` -- Terminal container states: `CrashLoopBackOff`, `Error`, `OOMKilled`, `CreateContainerConfigError`, `ImagePullBackOff`, `ErrImagePull`, `InvalidImageName`.
- `DATA_ACCESS_LABEL` = `"role=llm-d-benchmark-data-access"`

### Pod Discovery

- `find_data_access_pod(cmd, namespace) -> str | None` -- Find the data-access pod by its well-known label.

### Pod Waiting

- `wait_for_pods_by_label(cmd, label, namespace, timeout, context) -> list[str]` -- Two-phase wait: (1) `condition=Ready=True` (pods running), (2) `condition=ready=False` (pods finished). Checks for crash states. Returns error list (empty on success).
- `wait_for_pod(cmd, pod_name, namespace, timeout, context, poll_interval=15) -> str` -- Per-pod polling until terminal phase. Returns `"Succeeded"`, `"Failed"`, or an error description. Detects crash states.

### Result Collection

- `collect_pod_results(cmd, data_pod, namespace, remote_prefix, experiment_id, parallel_idx, local_results_dir, context) -> (local_path, success, error_msg)` -- Copy results for a single parallel pod instance from the PVC via `kubectl cp`.
- `sync_analysis_dir(local_path, analysis_dir, experiment_suffix)` -- Move the `analysis/` subdirectory from results to a dedicated directory, then remove it from results.

### Pod Cleanup

- `delete_pods_by_names(cmd, pod_names, namespace, context)` -- Delete pods by individual name.
- `delete_pods_by_label(cmd, label, namespace, context)` -- Delete all pods matching `app=<label>`.

### Log Capture

- `capture_pod_logs(cmd, pod_names, namespace, log_dir, context)` -- Capture logs from individual harness pods to files.
- `capture_label_logs(cmd, namespace, label, dest, label_name, context)` -- Capture aggregated logs for all pods matching a label selector.
- `capture_infrastructure_logs(cmd, namespace, log_dir, model_label, context)` -- Capture pod status snapshot (`kubectl get pods -o wide`) and logs from model-serving pods, EPP (inference scheduler) pods, and IGW (inference gateway) pods. Runs `process_epp_logs.py` on captured EPP logs if the script is available.

## cloud_upload.py -- GCS/S3 Upload

- `upload_results_dir(cmd, local_path, output, context, relative_path=None) -> str | None` -- Upload a single directory to GCS (`gcloud storage cp`) or S3 (`aws s3 cp`). Returns `None` on success, error string on failure. No-op when `output == "local"`. Logs the would-be command in dry-run mode.
- `upload_all_results(cmd, results_dir, output, context) -> str | None` -- Bulk upload the entire results directory (safety-net in step 09).

## huggingface.py -- HuggingFace Hub Helpers

Gated-model detection and token access verification using the `huggingface_hub` library.

```python
class GatedStatus(Enum):
    NOT_GATED = "not_gated"
    GATED = "gated"
    ERROR = "error"

class AccessStatus(Enum):
    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    ERROR = "error"

@dataclass
class ModelAccessResult:
    model_id: str
    gated: GatedStatus
    access: AccessStatus | None
    detail: str
    ok: bool  # property: True if accessible
```

- `is_model_gated(model_id) -> GatedStatus` -- Check gating status via `model_info()`.
- `user_has_model_access(model_id, hf_token) -> AccessStatus` -- Verify token access to a gated model.
- `check_model_access(model_id, hf_token=None) -> ModelAccessResult` -- Combined gated + access check with descriptive detail messages.

## profile_renderer.py -- Workload Profile Template Renderer

Replaces `REPLACE_ENV_*` tokens in `.yaml.in` profile templates with runtime values. Uses regex substitution (not Jinja2).

### Token Registry

```python
PROFILE_TOKENS: dict[str, TokenDef] = {
    "LLMDBENCH_DEPLOY_CURRENT_MODEL":        TokenDef("model.name", "Model name being served"),
    "LLMDBENCH_DEPLOY_CURRENT_TOKENIZER":    TokenDef("model.name", "Tokenizer model name"),
    "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL":  TokenDef(None, "Endpoint URL (runtime-detected)"),
    "LLMDBENCH_RUN_DATASET_DIR":             TokenDef("experiment.datasetDir", "Dataset directory"),
}
```

### Functions

- `build_env_map(plan_config=None, runtime_values=None) -> dict[str, str]` -- Build the substitution map from plan config and runtime overrides.
- `render_profile(template_content, env_map) -> str` -- Replace `REPLACE_ENV_*` tokens. Unknown tokens are left as-is.
- `render_profile_file(source_path, dest_path, env_map) -> Path` -- Render a template file and write the result.
- `apply_overrides(profile_content, overrides) -> str` -- Apply dotted `key=value` overrides to rendered YAML. Coerces values to int/float/bool where appropriate.

## os/filesystem.py -- Filesystem Utilities

- `directory_exists_and_nonempty(path) -> bool`
- `file_exists_and_nonzero(path) -> bool`
- `create_tmp_directory(prefix=None, suffix=None, base_dir=None) -> Path`
- `create_directory(path, exist_ok=True) -> Path`
- `copy_directory(source, destination, overwrite=False)`
- `get_absolute_path(path) -> Path` -- Resolve to absolute, expanding `~`.
- `resolve_specification_file(name_or_path, base_dir=None) -> Path` -- Look up a spec by bare name, category/name, or path. Searches `config/specification/` directories. Raises `FileNotFoundError` (no match) or `ValueError` (ambiguous).
- `remove_directory(path)`
- `create_workspace(workspace_dir) -> Path` -- Create or ensure workspace exists. Uses a temp dir if none specified.
- `create_sub_dir_workload(workspace_dir, sub_dir=None) -> Path` -- Create a run-specific subdirectory with `username-timestamp` naming.

## os/platform.py -- Platform Detection

```python
@dataclass(frozen=True)
class PlatformInfo:
    system: str     # e.g. "darwin", "linux"
    machine: str    # e.g. "x86_64", "arm64"
    is_mac: bool    # property
    is_linux: bool  # property

def get_platform_info() -> PlatformInfo: ...
def get_user_id() -> str: ...  # OS username via getpass.getuser()
```
