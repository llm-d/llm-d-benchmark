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
    && apt-get clean && rm -rf /var/cache/apt

RUN OC_FILE_NAME=openshift-client-$(uname -s | sed -e "s/Linux/linux/g" -e "s/Darwin/apple-darwin/g")$(echo "-$(uname -m)" | sed -e 's/-x86_64//g' -e 's/-amd64//g' -e 's/aarch64/arm64-rhel9/g').tar.gz; \
    curl https://mirror.openshift.com/pub/openshift-v4/$(uname -m)/clients/ocp/stable/$OC_FILE_NAME  -o $OC_FILE_NAME > /dev/null 2>&1 && \
    tar xzf $OC_FILE_NAME && \
    mv oc /usr/local/bin/ && \
    mv kubectl /usr/local/bin/ && \
    chmod +x /usr/local/bin/oc && \
    chmod +x /usr/local/bin/kubectl && \
    rm openshift-client-*.tar.gz

RUN curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 && chmod 700 get_helm.sh && ./get_helm.sh

RUN cd /usr/local/bin; \
    curl -s "https://raw.githubusercontent.com/kubernetes-sigs/kustomize/master/hack/install_kustomize.sh"  | bash

COPY build/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

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

ARG FM_PERF_REPO=https://github.com/fmperf-project/fmperf.git
ARG FM_PERF_BRANCH=main
RUN git clone --branch ${FM_PERF_BRANCH} ${FM_PERF_REPO}
RUN cd fmperf; \
    pip install --no-cache-dir -r requirements.txt && \
    python3 setup.py install

ARG INFERENCE_PERF_REPO=https://github.com/kubernetes-sigs/inference-perf.git
ARG INFERENCE_PERF_BRANCH=main
RUN git clone --branch ${INFERENCE_PERF_BRANCH} ${INFERENCE_PERF_REPO}
RUN cd inference-perf; pip install .

ARG VLLM_BENCHMARK_REPO=https://github.com/vllm-project/vllm.git
ARG VLLM_BENCHMARK_BRANCH=main
RUN git clone --branch ${VLLM_BENCHMARK_BRANCH} ${VLLM_BENCHMARK_REPO}
RUN cd vllm; pip install vllm; cd ..; mv -f vllm vllm-benchmark

RUN ln -s /usr/bin/sleep /usr/local/bin/sleep

ADD workload/harnesses/ /usr/local/bin/
COPY analysis/fmperf-analyze_results.py /usr/local/bin/fmperf-analyze_results.py
COPY analysis/inference-perf-analyze_results.sh /usr/local/bin/inference-perf-analyze_results.sh
COPY analysis/nop-analyze_results.py /usr/local/bin/nop-analyze_results.py
COPY analysis/vllm-benchmark-analyze_results.sh /usr/local/bin/vllm-benchmark-analyze_results.sh

RUN echo "#!/usr/bin/env bash" > /usr/local/bin/llm-d-benchmark.sh; echo -e "\

if [[ ! -z \$1 ]]; then\n\
  export LLMDBENCH_RUN_EXPERIMENT_HARNESS=\$(find /usr/local/bin | grep \${1}.*-llm-d-benchmark | rev | cut -d '/' -f 1 | rev)\n\
  export LLMDBENCH_RUN_EXPERIMENT_ANALYZER=\$(find /usr/local/bin | grep \${1}.*-analyze_results | rev | cut -d '/' -f 1 | rev)\n\
  export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR=/requests/\$(echo \$LLMDBENCH_RUN_EXPERIMENT_HARNESS | sed \"s^-llm-d-benchmark^^g\" | cut -d '.' -f 1)_\${LLMDBENCH_RUN_EXPERIMENT_ID}_\${LLMDBENCH_HARNESS_STACK_NAME}\n\
fi\n\
if [[ ! -z \$2 ]]; then\n\
  export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME=\$(echo \$2\".yaml\" | sed \"s^.yaml.yaml^.yaml^g\")\n\
else \n\
  if [[ ! -z \${LLMDBENCH_BASE64_HARNESS_WORKLOAD_CONTENTS} ]]; then\n\
    echo \${LLMDBENCH_BASE64_HARNESS_WORKLOAD_CONTENTS} | base64 -d > /workspace/\${LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME}\n\
  fi\n\
fi\n\
export LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR=\$(echo \$LLMDBENCH_RUN_EXPERIMENT_HARNESS | sed \"s^-llm-d-benchmark^^g\" | cut -d '.' -f 1)\n\
mkdir -p ~/.kube\n\
if [[ ! -z \${LLMDBENCH_BASE64_CONTEXT_CONTENTS} ]]; then\n\
  echo \${LLMDBENCH_BASE64_CONTEXT_CONTENTS} | base64 -d > ~/.kube/config\n\
fi\n\
if [[ -f ~/.bashrc ]]; then \n\
  mv -f ~/.bashrc ~/fixbashrc\n\
fi \n\
if [[ -d \$LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR ]]; then \n\
  pushd /workspace/\$LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR\n\
  current_repo=\$(git remote -v | grep \(fetch\) | awk '{ print \$2 }')\n\
  if [[ \$current_repo == \$LLMDBENCH_HARNESS_GIT_REPO ]]; then\n\
    git fetch\n\
  else\n\
    popd\n\
    rm -rf /workspace/\$LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR\n\
    git clone \$LLMDBENCH_HARNESS_GIT_REPO\n\
    pushd /workspace/\$LLMDBENCH_RUN_EXPERIMENT_HARNESS_DIR\n\
  fi\n\
  git checkout \$LLMDBENCH_HARNESS_GIT_BRANCH\n\
  popd \n\
fi\n\
if [[ ! -f /workspace/vllm-benchmark ]]; then\n\
  mv /workspace/vllm /workspace/vllm-benchmark\n\
fi\n\
/usr/local/bin/\${LLMDBENCH_RUN_EXPERIMENT_HARNESS}\n\
ec=\$?\n\
if [[ \$ec -ne 0 ]]; then\n\
  echo \"execution of /usr/local/bin/\${LLMDBENCH_RUN_EXPERIMENT_HARNESS} failed, wating 30 seconds and trying again\"\n\
  sleep 30\n\
fi\n\
if [[ -f ~/fixbashrc ]]; then \n\
  mv -f ~/fixbashrc ~/.bashrc\n\
fi \n\
/usr/local/bin/\${LLMDBENCH_RUN_EXPERIMENT_ANALYZER}\n\
ec=\$?\n\
if [[ \$ec -ne 0 ]]; then\n\
  echo \"execution of /usr/local/bin/\${LLMDBENCH_RUN_EXPERIMENT_ANALYZER} failed, wating 30 seconds and trying again\"\n\
  sleep 30\n\
fi\n\
exit \$ec\n\
" >> /usr/local/bin/llm-d-benchmark.sh; \
sed -i 's^\-e^^' /usr/local/bin/llm-d-benchmark.sh; \
chmod +x /usr/local/bin/llm-d-benchmark.sh


#RUN mkdir /root/.kube
#RUN touch /root/.llmdbench_dependencies_checked
#RUN touch /root/.llm-d-benchmark.image

ENTRYPOINT ["llm-d-benchmark.sh"]
