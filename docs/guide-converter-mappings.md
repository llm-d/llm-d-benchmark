# Guide Converter Mapping Rules

This document defines the mappings from llm-d guide Helm values to llm-d-benchmark environment variables.

## How to Use This Document

1. Look up the Helm value path in the appropriate section below
2. Find the corresponding `LLMDBENCH_*` variable
3. Apply any noted transformations
4. Note: Environment variables in Python code (e.g., `ev["..."]`) use lowercase keys without the `LLMDBENCH_` prefix

## GAIE (Gateway API Inference Extension) Configuration

The GAIE Helm chart is deployed via `setup/steps/08_deploy_gaie.py`. The generated `gaie-values.yaml` maps from these environment variables:

### InferenceExtension Settings

| Helm Path | LLMDBENCH Variable | ev[] Key | Notes |
|-----------|-------------------|----------|-------|
| `inferenceExtension.image.name` | `LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_NAME` | `llmd_inferencescheduler_image_name` | Scheduler image name |
| `inferenceExtension.image.hub` | `LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_REGISTRY` + `LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_REPO` | `llmd_inferencescheduler_image_registry`, `llmd_inferencescheduler_image_repo` | Combined as `{registry}/{repo}` |
| `inferenceExtension.image.tag` | `LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_TAG` | `llmd_inferencescheduler_image_tag` | Use `auto` for latest |
| `inferenceExtension.pluginsConfigFile` | `LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE` | `vllm_modelservice_gaie_plugins_configfile` | Plugin config filename |
| `inferenceExtension.pluginsCustomConfig` | `LLMDBENCH_VLLM_MODELSERVICE_GAIE_CUSTOM_PLUGINS` | `vllm_modelservice_gaie_custom_plugins` | Custom plugin YAML content |

### InferencePool Settings

| Helm Path | LLMDBENCH Variable | ev[] Key | Notes |
|-----------|-------------------|----------|-------|
| `inferencePool.targetPortNumber` | `LLMDBENCH_VLLM_COMMON_INFERENCE_PORT` | `vllm_common_inference_port` | Default: `8000` |
| `inferencePool.provider` | `LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_POOL_PROVIDER_CONFIG` | `vllm_modelservice_inference_pool_provider_config` | Provider-specific config |

### Gateway/Provider Settings

| Helm Path | LLMDBENCH Variable | ev[] Key | Notes |
|-----------|-------------------|----------|-------|
| `provider.name` | `LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME` | `vllm_modelservice_gateway_class_name` | `istio`, `kgateway`, or `gke` |

### GAIE Plugin Presets

Preset plugin configurations are stored in `setup/presets/gaie/`. Set the filename (without path) via:
- `LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE=default-plugins.yaml`

Available presets:
- `default.yaml` - Standard load balancing
- `pd-config.yaml` - Prefill/Decode disaggregation
- `prefix-cache-estimate-config.yaml` - Prefix cache estimation
- `prefix-cache-tracking-config.yaml` - Prefix cache tracking
- `inf-sche-*.yaml` - Various inference scheduling configurations

## Infrastructure Configuration

The infrastructure Helm chart is deployed via `setup/steps/07_deploy_setup.py`.

### Gateway Settings

| Helm Path | LLMDBENCH Variable | ev[] Key | Notes |
|-----------|-------------------|----------|-------|
| `gateway.gatewayClassName` | `LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME` | `vllm_modelservice_gateway_class_name` | `istio`, `kgateway`, or `gke` |
| `gateway.service.type` | `LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_SERVICE_TYPE` | `vllm_modelservice_gateway_service_type` | Default: `NodePort` |

### Chart Versions

| Helm Chart | LLMDBENCH Variable | ev[] Key | Default |
|------------|-------------------|----------|---------|
| llm-d-infra | `LLMDBENCH_VLLM_INFRA_CHART_VERSION` | `vllm_infra_chart_version` | `v1.3.5` |
| llm-d-modelservice | `LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION` | `vllm_modelservice_chart_version` | `auto` |
| inferencepool (GAIE) | `LLMDBENCH_VLLM_GAIE_CHART_VERSION` | `vllm_gaie_chart_version` | `v1.2.0` |
| kgateway | `LLMDBENCH_GATEWAY_PROVIDER_KGATEWAY_CHART_VERSION` | `gateway_provider_kgateway_chart_version` | `v2.1.1` |
| istio | `LLMDBENCH_GATEWAY_PROVIDER_ISTIO_CHART_VERSION` | `gateway_provider_istio_chart_version` | `1.28.1` |

## ModelService Configuration

The ModelService Helm chart is deployed via `setup/steps/09_deploy_via_modelservice.py`.

### Model Configuration

| Helm Path | LLMDBENCH Variable | ev[] Key | Notes |
|-----------|-------------------|----------|-------|
| `modelArtifacts.name` | `LLMDBENCH_DEPLOY_MODEL_LIST` | `deploy_model_list` | Model identifier (e.g., `Qwen/Qwen3-32B`) |
| `modelArtifacts.size` | `LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE` | `vllm_common_pvc_model_cache_size` | Storage size (e.g., `80Gi`) |
| `modelArtifacts.authSecretName` | `LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME` | `vllm_common_hf_token_name` | Secret name for HF token |
| `modelArtifacts.uri` | `LLMDBENCH_VLLM_MODELSERVICE_URI_PROTOCOL` | `vllm_modelservice_uri_protocol` | `pvc` or `hf` (auto-generated) |

### Decode Stage

| Helm Path | LLMDBENCH Variable | ev[] Key | Notes |
|-----------|-------------------|----------|-------|
| `decode.create` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS` | `vllm_modelservice_decode_replicas` | If `false`, set replicas to `0` |
| `decode.replicas` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS` | `vllm_modelservice_decode_replicas` | Number of decode pods |
| `decode.parallelism.tensor` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM` | `vllm_modelservice_decode_tensor_parallelism` | Tensor parallel size |
| `decode.parallelism.data` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_PARALLELISM` | `vllm_modelservice_decode_data_parallelism` | Data parallel size |
| `decode.parallelism.dataLocal` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_LOCAL_PARALLELISM` | `vllm_modelservice_decode_data_local_parallelism` | Data local parallel size |
| `decode.parallelism.workers` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_NUM_WORKERS_PARALLELISM` | `vllm_modelservice_decode_num_workers_parallelism` | Number of workers |
| `decode.containers[0].resources.requests.cpu` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR` | `vllm_modelservice_decode_cpu_nr` | CPU cores requested |
| `decode.containers[0].resources.requests.memory` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM` | `vllm_modelservice_decode_cpu_mem` | Memory requested |
| `decode.schedulerName` | `LLMDBENCH_VLLM_COMMON_POD_SCHEDULER` | `vllm_common_pod_scheduler` | Pod scheduler name |
| `decode.annotations` | `LLMDBENCH_VLLM_COMMON_ANNOTATIONS` | `vllm_common_annotations` | Deployment annotations |
| `decode.podAnnotations` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS` | `vllm_modelservice_decode_podannotations` | Pod annotations |
| `decode.containers[0].modelCommand` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND` | `vllm_modelservice_decode_model_command` | `vllmServe` or `custom` |
| `decode.containers[0].args` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS` | `vllm_modelservice_decode_extra_args` | vLLM arguments |
| `decode.containers[0].env` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_ENVVARS_TO_YAML` | `vllm_modelservice_decode_envvars_to_yaml` | Env vars (file path) |
| `decode.containers[0].extraConfig` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_CONTAINER_CONFIG` | `vllm_modelservice_decode_extra_container_config` | Extra container config |
| `decode.containers[0].volumeMounts` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS` | `vllm_modelservice_decode_extra_volume_mounts` | Volume mounts (file) |
| `decode.volumes` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES` | `vllm_modelservice_decode_extra_volumes` | Volumes (file path) |
| `decode.extraConfig` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_POD_CONFIG` | `vllm_modelservice_decode_extra_pod_config` | Extra pod config |

**Important:** GPU count is automatically calculated as `tensor_parallelism * data_local_parallelism`. Do NOT set `decode.containers[0].resources.limits."nvidia.com/gpu"` directly. Instead, set:
- `LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM`
- `LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_LOCAL_PARALLELISM`
- Or use `LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR=auto` (recommended)

### Prefill Stage

| Helm Path | LLMDBENCH Variable | ev[] Key | Notes |
|-----------|-------------------|----------|-------|
| `prefill.create` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS` | `vllm_modelservice_prefill_replicas` | If `false`, set replicas to `0` |
| `prefill.replicas` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS` | `vllm_modelservice_prefill_replicas` | Number of prefill pods |
| `prefill.parallelism.tensor` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM` | `vllm_modelservice_prefill_tensor_parallelism` | Tensor parallel size |
| `prefill.parallelism.data` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM` | `vllm_modelservice_prefill_data_parallelism` | Data parallel size |
| `prefill.parallelism.dataLocal` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_LOCAL_PARALLELISM` | `vllm_modelservice_prefill_data_local_parallelism` | Data local parallel size |
| `prefill.parallelism.workers` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_NUM_WORKERS_PARALLELISM` | `vllm_modelservice_prefill_num_workers_parallelism` | Number of workers |
| `prefill.containers[0].resources.requests.cpu` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_NR` | `vllm_modelservice_prefill_cpu_nr` | CPU cores requested |
| `prefill.containers[0].resources.requests.memory` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_MEM` | `vllm_modelservice_prefill_cpu_mem` | Memory requested |
| `prefill.schedulerName` | `LLMDBENCH_VLLM_COMMON_POD_SCHEDULER` | `vllm_common_pod_scheduler` | Pod scheduler name |
| `prefill.annotations` | `LLMDBENCH_VLLM_COMMON_ANNOTATIONS` | `vllm_common_annotations` | Deployment annotations |
| `prefill.podAnnotations` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PODANNOTATIONS` | `vllm_modelservice_prefill_podannotations` | Pod annotations |
| `prefill.containers[0].modelCommand` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_MODEL_COMMAND` | `vllm_modelservice_prefill_model_command` | `vllmServe` or `custom` |
| `prefill.containers[0].args` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS` | `vllm_modelservice_prefill_extra_args` | vLLM arguments |
| `prefill.containers[0].env` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ENVVARS_TO_YAML` | `vllm_modelservice_prefill_envvars_to_yaml` | Env vars (file path) |
| `prefill.containers[0].extraConfig` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_CONTAINER_CONFIG` | `vllm_modelservice_prefill_extra_container_config` | Extra container config |
| `prefill.containers[0].volumeMounts` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUME_MOUNTS` | `vllm_modelservice_prefill_extra_volume_mounts` | Volume mounts (file) |
| `prefill.volumes` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUMES` | `vllm_modelservice_prefill_extra_volumes` | Volumes (file path) |
| `prefill.extraConfig` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_POD_CONFIG` | `vllm_modelservice_prefill_extra_pod_config` | Extra pod config |

**Important:** GPU count is automatically calculated as `tensor_parallelism * data_local_parallelism`. Do NOT set `prefill.containers[0].resources.limits."nvidia.com/gpu"` directly. Instead, set:
- `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM`
- `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_LOCAL_PARALLELISM`
- Or use `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR=auto` (recommended)

### Common vLLM Settings

| Helm Path | LLMDBENCH Variable | ev[] Key | Notes |
|-----------|-------------------|----------|-------|
| `multinode` | `LLMDBENCH_VLLM_MODELSERVICE_MULTINODE` | `vllm_modelservice_multinode` | Multi-node deployment |
| `routing.servicePort` | `LLMDBENCH_VLLM_COMMON_INFERENCE_PORT` | `vllm_common_inference_port` | Inference service port |
| `routing.proxy.connector` | `LLMDBENCH_LLMD_ROUTINGSIDECAR_CONNECTOR` | `llmd_routingsidecar_connector` | Routing connector type |
| `routing.proxy.debugLevel` | `LLMDBENCH_LLMD_ROUTINGSIDECAR_DEBUG_LEVEL` | `llmd_routingsidecar_debug_level` | Debug level |
| `accelerator.type` | `LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE` | `vllm_common_accelerator_resource` | GPU resource type |

## vLLM Launch Arguments

These are typically found in `decode.containers[0].args` or extracted from command strings:

| Argument | LLMDBENCH Variable | ev[] Key | Notes |
|----------|-------------------|----------|-------|
| `--max-model-len` | `LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN` | `vllm_common_max_model_len` | Maximum sequence length |
| `--block-size` | `LLMDBENCH_VLLM_COMMON_BLOCK_SIZE` | `vllm_common_block_size` | KV cache block size |
| `--gpu-memory-utilization` | `LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL` | `vllm_common_accelerator_mem_util` | GPU memory fraction (0.0-1.0) |
| `--tensor-parallel-size` | See decode/prefill parallelism | | Usually matches container config |
| `--enable-prefix-caching` | (extra args) | | Add to `DECODE_EXTRA_ARGS` if present |
| `--no-enable-prefix-caching` | (extra args) | | Add to `DECODE_EXTRA_ARGS` if present |
| `--kv-transfer-config` | (extra args) | | Add to `DECODE_EXTRA_ARGS` for P/D disaggregation |
| `--enforce-eager` | (extra args) | | Add to `DECODE_EXTRA_ARGS` if present |
| `--disable-log-requests` | (extra args) | | Add to `DECODE_EXTRA_ARGS` if present |

## Shared Memory and Volumes

| Helm Path | LLMDBENCH Variable | ev[] Key | Notes |
|-----------|-------------------|----------|-------|
| `decode.volumes[?name=dshm].emptyDir.sizeLimit` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_SHM_MEM` | `vllm_modelservice_decode_shm_mem` | Shared memory size |
| `prefill.volumes[?name=dshm].emptyDir.sizeLimit` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_SHM_MEM` | `vllm_modelservice_prefill_shm_mem` | Shared memory size |

## Monitoring (Usually Omitted)

| Helm Path | LLMDBENCH Variable | Notes |
|-----------|-------------------|-------|
| `decode.monitoring.podmonitor.enabled` | (not mapped) | Pod monitoring not used in benchmarks |
| `decode.monitoring.podmonitor.interval` | (not mapped) | Omit from scenario files |

## Unmappable Values

These Helm values have no direct LLMDBENCH equivalent and should be noted in comments:

- `modelArtifacts.labels` - Kubernetes labels, automatically set by benchmark framework
- `modelArtifacts.uri` - Derived from model name with `pvc://` or `hf://` prefix
- `fullnameOverride` - Derived from model name label
- `decode.containers[0].image` - Image determined by benchmark framework (`LLMDBENCH_LLMD_IMAGE_*`)
- `decode.containers[0].livenessProbe` - Probes managed by framework
- `decode.containers[0].readinessProbe` - Probes managed by framework
- `decode.containers[0].startupProbe` - Probes managed by framework
- `decode.containers[0].resources.limits."nvidia.com/gpu"` - GPU count auto-calculated from parallelism settings
- `prefill.containers[0].resources.limits."nvidia.com/gpu"` - GPU count auto-calculated from parallelism settings

## Default Values Reference

The following defaults are defined in `setup/env.sh`. Only override if the guide specifies different values:

| Variable | ev[] Key | Default | Description |
|----------|----------|---------|-------------|
| `LLMDBENCH_VLLM_COMMON_TENSOR_PARALLELISM` | `vllm_common_tensor_parallelism` | `1` | Tensor parallel size |
| `LLMDBENCH_VLLM_COMMON_DATA_PARALLELISM` | `vllm_common_data_parallelism` | `1` | Data parallel size |
| `LLMDBENCH_VLLM_COMMON_DATA_LOCAL_PARALLELISM` | `vllm_common_data_local_parallelism` | `1` | Data local parallel size |
| `LLMDBENCH_VLLM_COMMON_NUM_WORKERS_PARALLELISM` | `vllm_common_num_workers_parallelism` | `1` | Workers for multi-node |
| `LLMDBENCH_VLLM_COMMON_REPLICAS` | `vllm_common_replicas` | `1` | Default replica count |
| `LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN` | `vllm_common_max_model_len` | `16384` | Maximum model length |
| `LLMDBENCH_VLLM_COMMON_BLOCK_SIZE` | `vllm_common_block_size` | `64` | KV cache block size |
| `LLMDBENCH_VLLM_COMMON_CPU_NR` | `vllm_common_cpu_nr` | `4` | CPU cores |
| `LLMDBENCH_VLLM_COMMON_CPU_MEM` | `vllm_common_cpu_mem` | `40Gi` | CPU memory |
| `LLMDBENCH_VLLM_COMMON_SHM_MEM` | `vllm_common_shm_mem` | `16Gi` | Shared memory |
| `LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEM_UTIL` | `vllm_common_accelerator_mem_util` | `0.95` | GPU memory utilization |
| `LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR` | `vllm_common_accelerator_nr` | `auto` | GPU count (auto-calculated) |
| `LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE` | `vllm_common_pvc_model_cache_size` | `300Gi` | PVC size for model cache |
| `LLMDBENCH_VLLM_COMMON_INFERENCE_PORT` | `vllm_common_inference_port` | `8000` | Inference port |
| `LLMDBENCH_VLLM_COMMON_METRICS_PORT` | `vllm_common_metrics_port` | `8200` | Metrics port |
| `LLMDBENCH_VLLM_MODELSERVICE_MULTINODE` | `vllm_modelservice_multinode` | `false` | Multi-node deployment |
| `LLMDBENCH_VLLM_MODELSERVICE_GATEWAY_CLASS_NAME` | `vllm_modelservice_gateway_class_name` | `istio` | Gateway class |
| `LLMDBENCH_VLLM_MODELSERVICE_GAIE_PLUGINS_CONFIGFILE` | `vllm_modelservice_gaie_plugins_configfile` | `default-plugins.yaml` | GAIE plugins config |
| `LLMDBENCH_HARNESS_NAME` | `harness_name` | `inference-perf` | Default load generator |
| `LLMDBENCH_HARNESS_EXPERIMENT_PROFILE` | `harness_experiment_profile` | `sanity_random.yaml` | Default workload profile |

## Complex Value Transformations

### File-Based Values

Several environment variables accept file paths containing YAML content. When converting from inline Helm values, create temporary files:

```bash
export LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML
- name: VLLM_LOGGING_LEVEL
  value: INFO
- name: UCX_TLS
  value: "sm,cuda_ipc,cuda_copy,tcp"
EOF
```

### Custom Commands with REPLACE_ENV Placeholders

When using `modelCommand=custom`, the args should use `REPLACE_ENV_*` placeholders:

```bash
export LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS=$(mktemp)
cat << EOF > $LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS
vllm serve /model-cache/models/REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL \
--host 0.0.0.0 \
--port REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT \
--max-model-len REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN \
--tensor-parallel-size REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM
EOF
```

### Common REPLACE_ENV Placeholders

| Placeholder | Description |
|------------|-------------|
| `REPLACE_ENV_LLMDBENCH_DEPLOY_CURRENT_MODEL` | Current model being deployed |
| `REPLACE_ENV_LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN` | Maximum model length |
| `REPLACE_ENV_LLMDBENCH_VLLM_COMMON_BLOCK_SIZE` | KV cache block size |
| `REPLACE_ENV_LLMDBENCH_VLLM_COMMON_METRICS_PORT` | Metrics port |
| `REPLACE_ENV_LLMDBENCH_VLLM_COMMON_INFERENCE_PORT` | Inference port |
| `REPLACE_ENV_LLMDBENCH_VLLM_COMMON_NIXL_SIDE_CHANNEL_PORT` | NIXL side channel port |
| `REPLACE_ENV_LLMDBENCH_VLLM_COMMON_SHM_MEM` | Shared memory size |
| `REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM` | Decode tensor parallelism |
| `REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_MEM_UTIL` | Decode GPU memory utilization |
| `REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_INFERENCE_PORT` | Decode inference port |
| `REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM` | Prefill tensor parallelism |
| `REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_MEM_UTIL` | Prefill GPU memory utilization |
| `REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_INFERENCE_PORT` | Prefill inference port |
| `REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_DECODE_PREPROCESS` | Decode preprocess command |
| `REPLACE_ENV_LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PREPROCESS` | Prefill preprocess command |
