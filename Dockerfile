# Step 1: Use a Python base image
FROM python:3.11-slim AS builder

# Install necessary dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    gpg \
    jq \
    && rm -rf /var/lib/apt/lists/*

RUN OC_FILE_NAME=openshift-client-$(uname -s | sed -e "s/Linux/linux/g" -e "s/Darwin/apple-darwin/g")$(echo "-$(uname -m)" | sed -e 's/-x86_64//g' -e 's/-amd64//g' -e 's/aarch64/arm64-rhel9/g').tar.gz; \
    curl https://mirror.openshift.com/pub/openshift-v4/$(uname -m)/clients/ocp/stable/$OC_FILE_NAME  -o $OC_FILE_NAME > /dev/null 2>&1 && \
    tar xzf $OC_FILE_NAME && \
    mv oc /usr/local/bin/ && \
    mv kubectl /usr/local/bin/ && \
    chmod +x /usr/local/bin/oc && \
    chmod +x /usr/local/bin/kubectl && \
    rm openshift-client-*.tar.gz

RUN curl https://baltocdn.com/helm/signing.asc | gpg --dearmor | tee /usr/share/keyrings/helm.gpg > /dev/null; \
    apt-get install apt-transport-https --yes && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/helm.gpg] https://baltocdn.com/helm/stable/debian/ all main" > /etc/apt/sources.list.d/helm-stable-debian.list && \
    apt-get update && \
    apt-get install helm && \
    rm -rf /var/lib/apt/lists/*

RUN cd /usr/local/bin; \
    curl -s "https://raw.githubusercontent.com/kubernetes-sigs/kustomize/master/hack/install_kustomize.sh"  | bash

# Set the working directory
WORKDIR /workspace

# Step 2: Download the appropriate Miniconda version based on the platform
# For ARM architecture (linux/arm64)
RUN if [ "$(uname -m)" = "aarch64" ]; then \
    curl -sSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh -o miniconda.sh; \
    elif [ "$(uname -m)" = "x86_64" ]; then \
    curl -sSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o miniconda.sh; \
    fi \
    && bash miniconda.sh -b -p /opt/miniconda \
    && rm miniconda.sh \
    && /opt/miniconda/bin/conda init

# Step 3: Install Python dependencies
RUN /opt/miniconda/bin/conda install -y python=3.9 \
    && /opt/miniconda/bin/conda install -y pip \
    && pip install --no-cache-dir urllib3 kubernetes pandas

# Step 4: Clone the correct GitHub repository and branch for fmperf
ARG FM_PERF_REPO=https://github.com/wangchen615/fmperf.git
ARG FM_PERF_BRANCH=dev-llm-d
RUN git clone --branch ${FM_PERF_BRANCH} ${FM_PERF_REPO}

# Step 5: Copy local fmperf files and environment variable files
ADD ./hack/deploy /workspace/llm-d-benchmark/hack/deploy
ADD ./workload /workspace/llm-d-benchmark/workload

# Step 6: Set the environment variable for the experiment environment (standalone, p2p, etc.)
ARG SCENARIO=none
ENV SCENARIO=${SCENARIO}

# Step 7: Set the entrypoint to run the experiment
ENTRYPOINT ["/workspace/llm-d-benchmark/run.sh -c ${SCENARIO} -n"]