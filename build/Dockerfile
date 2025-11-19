FROM python:3.12.9-slim-bookworm

RUN apt-get update; \
    apt-get install -y \
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
ARG INFERENCE_PERF_COMMIT=e8e0aa99c57f2ffa0912df7ba1fbd2a8a596a041
RUN git clone --branch ${INFERENCE_PERF_BRANCH} ${INFERENCE_PERF_REPO}
RUN cd inference-perf; \
    git checkout ${INFERENCE_PERF_COMMIT}; \
    pip install .

ARG VLLM_BENCHMARK_REPO=https://github.com/vllm-project/vllm.git
ARG VLLM_BENCHMARK_BRANCH=main
ARG VLLM_BENCHMARK_COMMIT=b6381ced9c52271f799a8348fcc98c5f40528cdf
RUN git clone --branch ${VLLM_BENCHMARK_BRANCH} ${VLLM_BENCHMARK_REPO}
RUN cd vllm; \
    git checkout ${VLLM_BENCHMARK_COMMIT}; \
    cd ..; mv -f vllm vllm-benchmark

ARG GUIDELLM_REPO=https://github.com/vllm-project/guidellm.git
ARG GUIDELLM_BRANCH=main
ARG GUIDELLM_COMMIT=f6175cdd8a88f0931bd46822ed7a71787dcd7cee
RUN git clone --branch ${GUIDELLM_BRANCH} ${GUIDELLM_REPO}
RUN cd guidellm; \
    pip install torch --index-url https://download.pytorch.org/whl/cpu; \
    git checkout ${GUIDELLM_COMMIT}; \
    pip install .[recommended]

RUN echo "inference-perf: ${INFERENCE_PERF_REPO} ${INFERENCE_PERF_BRANCH}" >> /workspace/repos.txt; \
    echo "vllm-benchmark: ${VLLM_BENCHMARK_REPO} ${VLLM_BENCHMARK_COMMIT}" >> /workspace/repos.txt; \
    echo "guidellm: ${GUIDELLM_REPO} ${GUIDELLM_COMMIT}" >> /workspace/repos.txt

RUN ln -s /usr/bin/sleep /usr/local/bin/sleep

ADD workload/harnesses/ /usr/local/bin/
COPY workload/report/*.py /usr/local/bin/
COPY analysis/inference-perf-analyze_results.sh /usr/local/bin/inference-perf-analyze_results.sh
COPY analysis/nop-analyze_results.py /usr/local/bin/nop-analyze_results.py
COPY analysis/vllm-benchmark-analyze_results.sh /usr/local/bin/vllm-benchmark-analyze_results.sh
COPY analysis/guidellm-analyze_results.sh /usr/local/bin/guidellm-analyze_results.sh

# Install requirements for analysis scripts
COPY build/requirements-analysis.txt .
RUN pip install --no-cache-dir -r requirements-analysis.txt

COPY build/llm-d-benchmark.sh /usr/local/bin/llm-d-benchmark.sh

ENTRYPOINT ["llm-d-benchmark.sh"]
