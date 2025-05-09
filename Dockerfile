# Step 1: Use a Python base image
FROM python:3.9-slim AS builder

# Install necessary dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

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
COPY ./hack/setup/scenarios /workspace/scenarios 
COPY ./hack/setup/env.sh /workspace/env.sh  
COPY ./hack/setup/run_experiment.sh /workspace/hack/setup/run_experiment.sh

# Step 6: Set the environment variable for the experiment environment (standalone, p2p, etc.)
ARG ENVIRONMENT=standalone
ENV ENVIRONMENT=${ENVIRONMENT}

# Step 7: Set the entrypoint to run the experiment
ENTRYPOINT ["/workspace/hack/setup/run_experiment.sh"]
