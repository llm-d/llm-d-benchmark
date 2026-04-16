# Upstream Dependency Version Tracking

> This file is the source of truth for the `upstream dependency monitor` workflow.
> Add your project's key upstream dependencies below. The monitor runs daily and creates GitHub issues when breaking changes are detected.

> **Pin type conventions:** Entries with `auto`, `latest`, `stable`, or `unpinned` use floating (unversioned) references
> that resolve to the latest available version at deploy/install time. The `auto` pin resolves to the latest
> tag at the moment of deployment. The monitor workflow skips these when checking for version drift.

## Helm Charts

| Dependency | Current Pin | Pin Type | File Location | Upstream Repo |
|-----------|-------------|----------|---------------|---------------|
<!-- | **example-lib** | `v1.2.3` | tag | `go.mod` line 10 | example-org/example-lib | -->
=======
| **llm-d-modelservice** | `auto` | floating (chart) | `setup/env.sh` (`LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION`) | [llm-d-incubation/llm-d-modelservice](https://github.com/llm-d-incubation/llm-d-modelservice) |
| **llm-d-infra** | `v1.4.0` | chart version | `setup/env.sh` (`LLMDBENCH_VLLM_INFRA_CHART_VERSION`) | [llm-d-incubation/llm-d-infra](https://github.com/llm-d-incubation/llm-d-infra) |
| **GAIE InferencePool** | `v1.3.1` | chart version | `setup/env.sh` (`LLMDBENCH_VLLM_GAIE_CHART_VERSION`) | [kubernetes-sigs/gateway-api-inference-extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension) |
| **kgateway** | `v2.1.1` | chart version | `setup/env.sh` (`LLMDBENCH_GATEWAY_PROVIDER_KGATEWAY_CHART_VERSION`) | [kgateway-dev/kgateway](https://github.com/kgateway-dev/kgateway) |
| **Istio** | `1.29.1` | chart version | `setup/env.sh` (`LLMDBENCH_GATEWAY_PROVIDER_ISTIO_CHART_VERSION`) | [istio/istio](https://github.com/istio/istio) |
| **Workload Variant Autoscaler** | `0.5.1` | chart version | `setup/env.sh` (`LLMDBENCH_WVA_CHART_VERSION`) | [llm-d/llm-d-workload-variant-autoscaler](https://github.com/llm-d/llm-d-workload-variant-autoscaler) |
| **Gateway API CRDs** | `v1.4.0` | tag | `setup/env.sh` (`LLMDBENCH_GATEWAY_API_CRD_REVISION`) | [kubernetes-sigs/gateway-api](https://github.com/kubernetes-sigs/gateway-api) |

## Container Images

| Dependency | Current Pin | Pin Type | File Location | Upstream Repo |
|-----------|-------------|----------|---------------|---------------|
| **llm-d-cuda** | `auto` | floating (image) | `setup/env.sh` (`LLMDBENCH_LLMD_IMAGE_TAG`) | [llm-d/llm-d](https://github.com/llm-d/llm-d) |
| **llm-d-model-service** | `auto` | floating (image) | `setup/env.sh` (`LLMDBENCH_LLMD_MODELSERVICE_IMAGE_TAG`) | [llm-d/llm-d](https://github.com/llm-d/llm-d) |
| **llm-d-inference-scheduler** | `auto` | floating (image) | `setup/env.sh` (`LLMDBENCH_LLMD_INFERENCESCHEDULER_IMAGE_TAG`) | [llm-d/llm-d](https://github.com/llm-d/llm-d) |
| **llm-d-routing-sidecar** | `auto` | floating (image) | `setup/env.sh` (`LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_TAG`) | [llm-d/llm-d](https://github.com/llm-d/llm-d) |
| **vllm-openai** | `auto` | floating (image) | `setup/env.sh` (`LLMDBENCH_VLLM_STANDALONE_IMAGE_TAG`) | [vllm-project/vllm](https://github.com/vllm-project/vllm) |
| **llm-d-workload-variant-autoscaler** | `v0.5.1` | image tag | `setup/env.sh` (`LLMDBENCH_WVA_IMAGE_TAG`) | [llm-d/llm-d-workload-variant-autoscaler](https://github.com/llm-d/llm-d-workload-variant-autoscaler) |

## Harness Tools (Dockerfile pins)

| Dependency | Current Pin | Pin Type | File Location | Upstream Repo |
|-----------|-------------|----------|---------------|---------------|
| **inference-perf** | `v0.4.0` | tag | `build/Dockerfile` (`INFERENCE_PERF_COMMIT`) | [kubernetes-sigs/inference-perf](https://github.com/kubernetes-sigs/inference-perf) |
| **vllm (benchmarks)** | `95c0f928cdeeaa21c4906e73cee6a156e1b3b995` | commit SHA | `build/Dockerfile` (`VLLM_BENCHMARK_COMMIT`) | [vllm-project/vllm](https://github.com/vllm-project/vllm) |
| **guidellm** | `v0.5.3` | version tag | `build/Dockerfile` (`GUIDELLM_COMMIT`) | [vllm-project/guidellm](https://github.com/vllm-project/guidellm) |
| **inferencemax (bench_serving)** | `ee867231de0b268e2810a6e31751b23cf5903fc5` | commit SHA | `build/Dockerfile` (`INFERENCEMAX_COMMIT`) | [kimbochen/bench_serving](https://github.com/kimbochen/bench_serving) |
| **Python base image** | `3.12.9-slim-bookworm` | image tag | `build/Dockerfile` (`FROM`) | [python (Docker Hub)](https://hub.docker.com/_/python) |

## Python Dependencies (planner / llm-d-planner)

Installed via: `pip install git+https://github.com/llm-d-incubation/llm-d-planner.git@f51812bebca30e0291ec541bd2ef2acf0572e8a4`

| Dependency | Current Pin | Pin Type | File Location | Upstream Repo |
|-----------|-------------|----------|---------------|---------------|
| **huggingface_hub** | `>=0.34.4` | minimum version | `llm-d-planner/pyproject.toml` | [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) |
| **transformers** | `>=4.55.4` | minimum version | `llm-d-planner/pyproject.toml` | [huggingface/transformers](https://github.com/huggingface/transformers) |
| **pydantic** | `==2.12.5` | exact version | `llm-d-planner/pyproject.toml` | [pydantic/pydantic](https://github.com/pydantic/pydantic) |
| **pandas** | `==3.0.2` | exact version | `llm-d-planner/pyproject.toml` | [pandas-dev/pandas](https://github.com/pandas-dev/pandas) |
| **fastapi** | `>=0.115.3` | minimum version | `llm-d-planner/pyproject.toml` | [fastapi/fastapi](https://github.com/fastapi/fastapi) |
| **uvicorn** | `==0.44.0` | exact version | `llm-d-planner/pyproject.toml` | [encode/uvicorn](https://github.com/encode/uvicorn) |
| **ollama** | `==0.6.1` | exact version | `llm-d-planner/pyproject.toml` | [ollama/ollama-python](https://github.com/ollama/ollama-python) |
| **psycopg2-binary** | `==2.9.11` | exact version | `llm-d-planner/pyproject.toml` | [psycopg/psycopg2](https://github.com/psycopg/psycopg2) |
| **llm-optimizer** | git main | git ref | `llm-d-planner/pyproject.toml` | [bentoml/llm-optimizer](https://github.com/bentoml/llm-optimizer) |

## Python Dependencies (analysis)

| Dependency | Current Pin | Pin Type | File Location | Upstream Repo |
|-----------|-------------|----------|---------------|---------------|
| **matplotlib** | `>=3.7.0` | minimum version | `build/requirements-analysis.txt` | [matplotlib/matplotlib](https://github.com/matplotlib/matplotlib) |
| **numpy** | `>=2.3.1` | minimum version | `build/requirements-analysis.txt` | [numpy/numpy](https://github.com/numpy/numpy) |
| **seaborn** | `>=0.12.0` | minimum version | `build/requirements-analysis.txt` | [mwaskom/seaborn](https://github.com/mwaskom/seaborn) |
| **pandas** | `>=2.2.3` | minimum version | `build/requirements-analysis.txt` | [pandas-dev/pandas](https://github.com/pandas-dev/pandas) |
| **pydantic** | `>=2.11.7` | minimum version | `build/requirements-analysis.txt` | [pydantic/pydantic](https://github.com/pydantic/pydantic) |
| **scipy** | `>=1.16.0` | minimum version | `build/requirements-analysis.txt` | [scipy/scipy](https://github.com/scipy/scipy) |
| **kubernetes** | `>=24.2.0` | minimum version | `build/requirements-analysis.txt` | [kubernetes-client/python](https://github.com/kubernetes-client/python) |

## CLI Tools

| Dependency | Current Pin | Pin Type | File Location | Upstream Repo |
|-----------|-------------|----------|---------------|---------------|
| **yq** | `v4.52.4` | release tag | `setup/install_deps.sh` (`install_yq_linux`) | [mikefarah/yq](https://github.com/mikefarah/yq) |
| **helmfile** | `v1.4.1` | release tag | `setup/install_deps.sh` (`install_helmfile_linux`) | [helmfile/helmfile](https://github.com/helmfile/helmfile) |
| **helm** | `latest` | floating | `setup/install_deps.sh` (`install_helm_linux`) | [helm/helm](https://github.com/helm/helm) |
| **kubectl** | `stable` | floating | `build/Dockerfile` | [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes) |

## Runtime Python Dependencies

| Dependency | Current Pin | Pin Type | File Location | Upstream Repo |
|-----------|-------------|----------|---------------|---------------|
| **kubernetes** | unpinned | floating | `setup/install_deps.sh` | [kubernetes-client/python](https://github.com/kubernetes-client/python) |
| **pykube-ng** | unpinned | floating | `setup/install_deps.sh` | [hjacobs/pykube-ng](https://codeberg.org/hjacobs/pykube-ng) |
| **kubernetes-asyncio** | unpinned | floating | `setup/install_deps.sh` | [tomplus/kubernetes_asyncio](https://github.com/tomplus/kubernetes_asyncio) |
| **GitPython** | unpinned | floating | `setup/install_deps.sh` | [gitpython-developers/GitPython](https://github.com/gitpython-developers/GitPython) |
| **requests** | unpinned | floating | `setup/install_deps.sh` | [psf/requests](https://github.com/psf/requests) |
| **Jinja2** | unpinned | floating | `setup/install_deps.sh` | [pallets/jinja](https://github.com/pallets/jinja) |
