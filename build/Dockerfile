FROM ghcr.io/llm-d/python:3.12.9-slim-bookworm

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
    cmake \
    wget \
    ca-certificates \
    gnupg \
    infiniband-diags \
    iproute2 \
    sudo \
    unzip \
    libpq-dev \
    vim-tiny \
    && apt-get clean && rm -rf /var/cache/apt && rm -rf /var/lib/apt/lists/*

# Install OpenShift CLI (oc)
RUN OCPFN=$(echo "-$(uname -m)" | sed -e 's/-x86_64//g' -e 's/-amd64//g' -e 's/aarch64/ppc64le/g' -e 's/s390x/s390x-rhel9/g'); \
    wget -N https://mirror.openshift.com/pub/openshift-v4/$(uname -m)/clients/ocp/stable/openshift-client-linux$OCPFN.tar.gz >/dev/null 2>&1 && \
    tar -xzf openshift-client-linux$OCPFN.tar.gz -C /usr/local/bin/ oc kubectl && \
    rm openshift-client-linux$OCPFN.tar.gz && \
    chmod +x /usr/local/bin/oc && \
    chmod +x /usr/local/bin/kubectl

RUN curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg; \
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list

RUN apt-get install -y google-cloud-sdk-gke-gcloud-auth-plugin && apt-get clean && rm -rf /var/cache/apt && rm -rf /var/lib/apt/lists/* || true

RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o "awscliv2.zip" && \
unzip awscliv2.zip >/dev/null 2>&1 && \
sudo ./aws/install && \
rm awscliv2.zip

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
sed -i 's^\-e^^' /etc/rsyncd.conf; ln -s /usr/bin/sleep /usr/local/bin/sleep; ln -s /usr/bin/vi /usr/local/bin/vim

WORKDIR /workspace

# Install harnesses

ARG INFERENCE_PERF_REPO=https://github.com/kubernetes-sigs/inference-perf.git
ARG INFERENCE_PERF_BRANCH=main
ARG INFERENCE_PERF_COMMIT=ad65fe09932a300dfc27fcb1d17d1aac88787ea3
RUN git clone --branch ${INFERENCE_PERF_BRANCH} ${INFERENCE_PERF_REPO}
RUN cd inference-perf; \
    git checkout ${INFERENCE_PERF_COMMIT}; \
    pip install .

ARG VLLM_BENCHMARK_REPO=https://github.com/vllm-project/vllm.git
ARG VLLM_BENCHMARK_BRANCH=v0.17.1
ARG VLLM_BENCHMARK_COMMIT=95c0f928cdeeaa21c4906e73cee6a156e1b3b995
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
RUN pip install setuptools-scm scikit-build-core nanobind
# Install
RUN cd vllm; VLLM_TARGET_DEVICE=empty pip install -e . --no-build-isolation

# GuideLLM also requires torch, installed above
ARG GUIDELLM_REPO=https://github.com/vllm-project/guidellm.git
ARG GUIDELLM_BRANCH=v0.6.0
ARG GUIDELLM_COMMIT=v0.6.0
RUN git clone --branch ${GUIDELLM_BRANCH} ${GUIDELLM_REPO}
RUN cd guidellm; \
    git checkout ${GUIDELLM_COMMIT}; \
    pip install .[recommended]

ARG INFERENCEMAX_REPO=https://github.com/kimbochen/bench_serving.git
ARG INFERENCEMAX_BRANCH=main
ARG INFERENCEMAX_COMMIT=ee867231de0b268e2810a6e31751b23cf5903fc5
RUN git clone --branch ${INFERENCEMAX_BRANCH} ${INFERENCEMAX_REPO}
RUN cd bench_serving; \
    git checkout ${INFERENCEMAX_COMMIT}

ARG AIPERF_REPO=https://github.com/ai-dynamo/aiperf.git
ARG AIPERF_BRANCH=v0.8.0
ARG AIPERF_COMMIT=v0.8.0
RUN git clone --branch ${AIPERF_BRANCH} ${AIPERF_REPO}
RUN cd aiperf; \
    git checkout ${AIPERF_COMMIT}; \
    pip install .

RUN echo "inference-perf: ${INFERENCE_PERF_REPO} ${INFERENCE_PERF_BRANCH}" >> /workspace/repos.txt; \
    echo "vllm-benchmark: ${VLLM_BENCHMARK_REPO} ${VLLM_BENCHMARK_COMMIT}" >> /workspace/repos.txt; \
    echo "guidellm: ${GUIDELLM_REPO} ${GUIDELLM_COMMIT}" >> /workspace/repos.txt; \
    echo "inferencemax: ${INFERENCEMAX_REPO} ${INFERENCEMAX_COMMIT}" >> /workspace/repos.txt; \
    echo "aiperf: ${AIPERF_REPO} ${AIPERF_COMMIT}" >> /workspace/repos.txt;

ADD workload/harnesses/ /usr/local/bin/
ADD llmdbenchmark/analysis/scripts/ /tmp/analysis/
ADD llmdbenchmark/standup/preprocess/ /tmp/preprocess/
COPY llmdbenchmark/analysis/visualize_metrics.py /usr/local/bin/visualize_metrics.py

RUN rsync -az --exclude "__pycache__" /tmp/analysis/ /usr/local/bin/; rsync -az --exclude "__pycache__" /tmp/preprocess/ /usr/local/bin/

COPY llmdbenchmark/analysis/benchmark_report/ /opt/benchmark_report/
RUN mv /opt/benchmark_report/_pyproject.toml /opt/pyproject.toml

# Install requirements for analysis scripts
COPY build/requirements-analysis.txt .
RUN pip install --no-cache-dir -r requirements-analysis.txt && pip install -e /opt

ADD install.sh /usr/local/bin/llmdbench_install.sh
RUN chmod +x /usr/local/bin/*; \
    llmdbench_install.sh  -y && apt-get clean && rm -rf /var/cache/apt && rm -rf /var/lib/apt/lists/* || true

COPY build/llm-d-benchmark.sh /usr/local/bin/llm-d-benchmark.sh
COPY util/debug_info_on_failure.sh /usr/local/bin/debug_info_on_failure.sh

ENTRYPOINT ["llm-d-benchmark.sh"]
