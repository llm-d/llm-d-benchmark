FROM python:3.12.9-slim-bookworm

RUN apt-get update; \
    apt-get install -y \
    bc \
    git \
    gpg \
    jq \
    pip \
    rsync \
    patch \
    curl \
    yq \
    wget \
    && apt-get clean && rm -rf /var/cache/apt

RUN echo "# /etc/rsyncd: configuration file for rsync daemon mode" > /etc/rsyncd.conf; echo -e "\
\n\
[global]\n\
charset = utf-8\n\
port = 20873\n\
max connections = 8\n\
reverse lookup = no\n\
\n\
[requests]\n\
path = /requests\n\
read only = yes\n\
use chroot = false\n\
list = yes\n\
" >> /etc/rsyncd.conf; \
sed -i 's^\-e^^' /etc/rsyncd.conf

WORKDIR /workspace

# Install harnesses

ARG INFERENCE_PERF_REPO=https://github.com/kubernetes-sigs/inference-perf.git
ARG INFERENCE_PERF_BRANCH=main
ARG INFERENCE_PERF_COMMIT=a85b31b5de9fde12b5a0ebaaabb2aee1ccb76657
RUN git clone --branch ${INFERENCE_PERF_BRANCH} ${INFERENCE_PERF_REPO}
RUN cd inference-perf; \
    git checkout ${INFERENCE_PERF_COMMIT}; \
    pip install .

ARG VLLM_BENCHMARK_REPO=https://github.com/vllm-project/vllm.git
ARG VLLM_BENCHMARK_BRANCH=main
ARG VLLM_BENCHMARK_COMMIT=e675dda67be278d21c4ec177b2baa8b7a0550920
RUN git clone --branch ${VLLM_BENCHMARK_BRANCH} ${VLLM_BENCHMARK_REPO}
RUN cd vllm; git checkout ${VLLM_BENCHMARK_COMMIT}
# Patch the pyproject.toml to allow "pip install -e ."
RUN cd vllm; printf '%s\n' \
    '--- a/pyproject.toml' \
    '+++ b/pyproject.toml' \
    '@@ -16,8 +16,7 @@ build-backend = "setuptools.build_meta"' \
    ' [project]' \
    ' name = "vllm"' \
    ' authors = [{name = "vLLM Team"}]' \
    '-license = "Apache-2.0"' \
    '-license-files = ["LICENSE"]' \
    '+license = {text = "Apache-2.0"}' \
    ' readme = "README.md"' \
    ' description = "A high-throughput and memory-efficient inference and serving engine for LLMs"' \
    ' classifiers = [' \
    | git apply
# Install some pre-reqs
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install setuptools-scm
# Install
RUN cd vllm; VLLM_TARGET_DEVICE=empty pip install -e . --no-build-isolation

# GuideLLM also requires torch, installed above
ARG GUIDELLM_REPO=https://github.com/vllm-project/guidellm.git
ARG GUIDELLM_BRANCH=main
ARG GUIDELLM_COMMIT=adfa108ab1df6f2a1452d1037a71817a493303a8
RUN git clone --branch ${GUIDELLM_BRANCH} ${GUIDELLM_REPO}
RUN cd guidellm; \
    git checkout ${GUIDELLM_COMMIT}; \
    pip install .[recommended]

ARG INFERENCEMAX_REPO=https://github.com/kimbochen/bench_serving.git
ARG INFERENCEMAX_BRANCH=main
ARG INFERENCEMAX_COMMIT=499c0b171b499b02a1fd546fb2326d2175a5d66e
RUN git clone --branch ${INFERENCEMAX_BRANCH} ${INFERENCEMAX_REPO}
RUN cd bench_serving; \
    git checkout ${INFERENCEMAX_COMMIT}

# TEMPORARILY using the vllm-benchmark code from https://github.com/kimbochen/bench_serving.git
# It looks like the commit b6381ced9c52271f799a8348fcc98c5f40528cdf was deleted from the tree on https://github.com/vllm-project/vllm.git
RUN mv vllm-benchmark vllm; \
    mkdir vllm-benchmark ; \
    cd vllm-benchmark ; \
    ln -s ../bench_serving benchmarks

RUN echo "inference-perf: ${INFERENCE_PERF_REPO} ${INFERENCE_PERF_BRANCH}" >> /workspace/repos.txt; \
    echo "vllm-benchmark: ${VLLM_BENCHMARK_REPO} ${VLLM_BENCHMARK_COMMIT}" >> /workspace/repos.txt; \
    echo "guidellm: ${GUIDELLM_REPO} ${GUIDELLM_COMMIT}" >> /workspace/repos.txt; \
    echo "inferencemax: ${INFERENCEMAX_REPO} ${INFERENCEMAX_COMMIT}" >> /workspace/repos.txt;

RUN ln -s /usr/bin/sleep /usr/local/bin/sleep

ADD workload/harnesses/ /usr/local/bin/
COPY analysis/inference-perf-analyze_results.sh /usr/local/bin/inference-perf-analyze_results.sh
COPY analysis/nop-analyze_results.py /usr/local/bin/nop-analyze_results.py
COPY analysis/vllm-benchmark-analyze_results.sh /usr/local/bin/vllm-benchmark-analyze_results.sh
COPY analysis/guidellm-analyze_results.sh /usr/local/bin/guidellm-analyze_results.sh
COPY analysis/inferencemax-analyze_results.sh /usr/local/bin/inferencemax-analyze_results.sh
COPY benchmark_report/ /opt/benchmark_report/
RUN mv /opt/benchmark_report/_pyproject.toml /opt/pyproject.toml

# Install requirements for analysis scripts
COPY build/requirements-analysis.txt .
RUN pip install --no-cache-dir -r requirements-analysis.txt
RUN pip install -e /opt

COPY build/llm-d-benchmark.sh /usr/local/bin/llm-d-benchmark.sh

ENTRYPOINT ["llm-d-benchmark.sh"]
