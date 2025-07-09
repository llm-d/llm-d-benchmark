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

RUN echo "#!/usr/bin/env bash" > /usr/local/bin/llm-d-benchmark.sh; echo -e "\
mkdir -p ~/.kube\n\
if [[ ! -z \${LLMDBENCH_BASE64_CONTEXT} ]]; then\n\
  echo \${LLMDBENCH_BASE64_CONTEXT} | base64 -d > ~/.kube/config\n\
fi\n\
if [[ ! -z \${LLMDBENCH_BASE64_HARNESS_WORKLOAD} ]]; then\n\
  echo \${LLMDBENCH_BASE64_HARNESS_WORKLOAD} | base64 -d > /workspace/llmdbench_workload.yaml\n\
fi\n\
mv -f ~/.bashrc ~/fixbashrc\n\
/usr/local/bin/\${LLMDBENCH_RUN_EXPERIMENT_HARNESS}\n\
ec=\$?\n\
if [[ \$ec -ne 0 ]]; then\n\
  echo \"execution of /usr/local/bin/\${LLMDBENCH_RUN_EXPERIMENT_HARNESS} failed, wating 30 seconds and trying again\"\n\
  sleep 30\n\
fi\n\
mv -f ~/fixbashrc ~/.bashrc\n\
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

WORKDIR /workspace

ARG FM_PERF_REPO=https://github.com/fmperf-project/fmperf.git
ARG FM_PERF_BRANCH=main
RUN git clone --branch ${FM_PERF_BRANCH} ${FM_PERF_REPO}

ARG INFERENCE_PERF_REPO=https://github.com/kubernetes-sigs/inference-perf.git
ARG INFERENCE_PERF_BRANCH=main
RUN git clone --branch ${INFERENCE_PERF_BRANCH} ${INFERENCE_PERF_REPO}
RUN cd inference-perf; pip install .

RUN cd fmperf; \
    pip install --no-cache-dir -r requirements.txt && \
    python3 setup.py install

RUN ln -s /usr/bin/sleep /usr/local/bin/sleep

ADD workload/harnesses/ /usr/local/bin/
COPY analysis/fmperf-analyze_results.py /usr/local/bin/fmperf-analyze_results.py
COPY analysis/nop-analyze_results.py /usr/local/bin/nop-analyze_results.py

#RUN mkdir /root/.kube
#RUN touch /root/.llmdbench_dependencies_checked
#RUN touch /root/.llm-d-benchmark.image

ENTRYPOINT ["llm-d-benchmark.sh"]
