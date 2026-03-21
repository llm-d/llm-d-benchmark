# llmdbenchmark.smoketests

Post-deployment validation for llm-d-benchmark. Runs automatically after standup and can be executed independently against an already-deployed stack.

## Why smoketests

Standing up an llm-d stack involves many moving parts -- Helm charts, init containers, sidecars, routing proxies, EPP pods, and scenario-specific vLLM flags. A successful `helm install` doesn't guarantee the pods are configured correctly. The smoketest module catches configuration drift, port mismatches, missing env vars, and broken routing before you spend GPU hours on a benchmark that was doomed from the start.

## Usage

```bash
# Run all smoketest steps against a deployed stack
llmdbenchmark --spec guides/pd-disaggregation smoketest -p my-namespace

# Run a specific step only
llmdbenchmark --spec guides/pd-disaggregation smoketest -p my-namespace -s 0   # health check only
llmdbenchmark --spec guides/pd-disaggregation smoketest -p my-namespace -s 1   # inference test only
llmdbenchmark --spec guides/pd-disaggregation smoketest -p my-namespace -s 2   # config validation only

# Dry-run (shows what would be checked, no cluster access)
llmdbenchmark --spec guides/pd-disaggregation smoketest -p my-namespace --dry-run
```

Smoketests also run automatically at the end of `llmdbenchmark standup`.

## Steps

| Step | Name | What it does | Runs for |
|------|------|--------------|----------|
| 00 | `health_check` | Verifies pods are running, `/health` responds, `/v1/models` returns the expected model, service/gateway is reachable, pod IPs respond, OpenShift route works (if applicable) | All scenarios |
| 01 | `inference_test` | Sends a sample `/v1/completions` request (falls back to `/v1/chat/completions`), logs generated text and a copy-pasteable curl command for demo purposes | All scenarios |
| 02 | `validate_config` | Compares the live pod spec against the rendered `config.yaml` to catch mismatches in resources, parallelism, env vars, probes, volumes, security context, and vLLM flags | Scenarios with a dedicated validator |

Scenarios without a dedicated validator (cicd paths, sim-small, etc.) run steps 00 and 01 only. Step 02 logs a skip message and passes.

## Config validation (step 02)

### How it works

The rendered `config.yaml` in the plan directory captures the exact configuration the scenario intended. Step 02 queries the live cluster for pod specs and compares them field by field. Nothing is hardcoded in the validators -- expected values come from the config, so they adapt automatically when the scenario changes.

The base class (`validate_role_pods`) handles the common checks that apply to every scenario. Per-scenario validators add checks specific to their deployment pattern.

### What the base checks cover

- Replica count matches config
- CPU/memory limits and requests
- DP_SIZE, DP_SIZE_LOCAL env vars match parallelism config
- Init containers present (preprocess, routing-proxy)
- Security context capabilities (IPC_LOCK, SYS_RAWIO, etc.)
- Routing proxy present or absent based on `routing.proxy.enabled`
- Volumes and volume mounts (dshm, shared-config, kubeconfig, etc.)
- Startup/liveness/readiness probe paths, thresholds, and periods
- vLLM command-line flags (enforce-eager, kv-transfer-config, block-size, max-model-len, etc.)
- VLLM_IS_DECODE / VLLM_IS_PREFILL role markers

### Registered validators

| Stack name | Validator | Scenario-specific checks |
|------------|-----------|--------------------------|
| `pd-disaggregation` | `PdDisaggregationValidator` | Both prefill + decode pods, KV transfer with NixlConnector, role markers |
| `precise-prefix-cache-aware` | `PrecisePrefixCacheAwareValidator` | No routing proxy, EPP pod running, `--prefix-caching-hash-algo sha256_cbor`, KV events port 5557 |
| `inference-scheduling` | `InferenceSchedulingValidator` | Decode-only, metrics port exposed, routing proxy present |
| `tiered-prefix-cache` | `TieredPrefixCacheValidator` | KV transfer with OffloadingConnector, LMCACHE env vars, `--max-num-seq`, EPP pod |
| `wide-ep-lws` | `WideEpLwsValidator` | LWS env vars (LWS_GROUP_SIZE, DP_SIZE_LOCAL), expert parallelism flags, RDMA network resource |
| `simulated-accelerators` | `SimulatedAcceleratorsValidator` | No GPU resources on pods, works for both standalone and modelservice |
| `cpu-example-ms` | `CpuValidator` | No GPU resources, CPU vLLM image, kubeconfig + preprocesses volumes |
| `gpu-example` | `GpuValidator` | GPU accelerator resource present, supports both modelservice and standalone |
| `spyre-example` | `SpyreValidator` | Spyre accelerator (`ibm.com/spyre_vf`), Spyre env vars (FLEX_COMPUTE, FLEX_DEVICE, etc.), precompiled model PVC, AIU image |

## Adding a validator for a new scenario

1. Create `llmdbenchmark/smoketests/validators/<your_scenario>.py`
2. Subclass `BaseSmoketest`, override `run_config_validation(self, context, stack_path)`
3. Call `self.validate_role_pods()` for the standard checks, then add scenario-specific checks via `CheckResult`
4. Register in `validators/__init__.py` -- add the import and map the stack name to your class in `VALIDATORS`

The stack name is the `-name` field from the scenario YAML (e.g., `pd-disaggregation`, `cpu-example-ms`). When no validator is registered for a stack name, step 02 falls back to `BaseSmoketest` which skips config validation.

## Module structure

```
smoketests/
├── __init__.py            -- get_validator() registry lookup
├── base.py                -- BaseSmoketest: health checks, inference test, validate_role_pods
├── report.py              -- SmoketestReport / CheckResult tracking
├── steps/
│   ├── __init__.py        -- get_smoketest_steps() registry
│   ├── step_00_health_check.py
│   ├── step_01_inference_test.py
│   └── step_02_validate_config.py
└── validators/
    ├── __init__.py         -- VALIDATORS dict (stack name → validator class)
    ├── cpu.py
    ├── gpu.py
    ├── spyre.py
    ├── inference_scheduling.py
    ├── pd_disaggregation.py
    ├── precise_prefix_cache_aware.py
    ├── simulated_accelerators.py
    ├── tiered_prefix_cache.py
    └── wide_ep_lws.py
```
