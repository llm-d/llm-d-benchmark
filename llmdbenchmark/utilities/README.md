# llmdbenchmark.utilities

Shared helper functions used across multiple phases. Provides Kubernetes helpers, endpoint detection, cloud upload, capacity validation, HuggingFace access checks, workload profile rendering, and OS-level utilities.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Empty package marker |
| `kube_helpers.py` | Kubernetes pod lifecycle helpers: pod discovery (`find_data_access_pod`), waiting (`wait_for_pods_by_label`, `wait_for_pod`), result collection (`collect_pod_results`, `sync_analysis_dir`), cleanup (`delete_pods_by_names`, `delete_pods_by_label`), and log capture (`capture_pod_logs`, `capture_infrastructure_logs`). Defines `CRASH_STATES` for terminal pod detection. |
| `endpoint.py` | Endpoint detection and model verification: `find_standalone_endpoint`, `find_gateway_endpoint`, `find_custom_endpoint`, `test_model_serving` (retryable `/v1/models` check via ephemeral curl pods), `discover_hf_token_secret`, `extract_hf_token_from_secret` |
| `cluster.py` | Cluster connectivity and platform detection: `resolve_cluster` (connect, detect platform, store kubeconfig, resolve metadata), `kube_connect` (establish Kubernetes API connection via kubeconfig, token, or in-cluster config) |
| `cloud_upload.py` | Cloud storage upload for benchmark results: `upload_results_dir` using `gcloud storage cp` for GCS and `aws s3 cp` for S3 |
| `capacity_validator.py` | Capacity planning validation using `config_explorer.capacity_planner`: validates GPU memory, tensor parallelism, KV cache, and max concurrent requests against model and hardware constraints |
| `huggingface.py` | HuggingFace Hub helpers: gated-model detection (`GatedStatus`), token access verification (`AccessStatus`), `ModelAccessResult` |
| `profile_renderer.py` | Workload profile template renderer: replaces `REPLACE_ENV_*` tokens in `.yaml.in` profile templates with runtime values. Maintains a registry of known tokens (`PROFILE_TOKENS`) with config paths and descriptions. |

### os/ subdirectory

| File | Description |
|------|-------------|
| `__init__.py` | Empty package marker |
| `filesystem.py` | Filesystem utilities: `directory_exists_and_nonempty`, `file_exists_and_nonzero`, `create_tmp_directory`, `create_workspace`, `create_sub_dir_workload`, `get_absolute_path`, `resolve_specification_file` |
| `platform.py` | Platform detection: `PlatformInfo` dataclass (system, machine, is_mac, is_linux), `get_platform_info()`, `get_user_id()` |
