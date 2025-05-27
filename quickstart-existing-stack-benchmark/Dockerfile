# Multi-stage build: Use the existing llm-d-benchmark image as source
FROM ghcr.io/llm-d/llm-d-benchmark:v0.0.8 as base

FROM registry.access.redhat.com/ubi9/python-39:latest

# Switch to root to install packages
USER root

# Copy requirements and install Python dependencies
COPY --from=base /requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy and install fmperf properly
COPY --from=base /workspace/fmperf /workspace/fmperf
RUN cd /workspace/fmperf && \
    pip install --no-cache-dir -r requirements.txt && \
    python3 setup.py install

# Copy the workload directory (contains the harness script)
COPY workload /workspace/workload

# Copy the analysis directory (contains the analysis script)
COPY analysis /workspace/analysis

COPY quickstart-existing-stack-benchmark/compare-stacks/compare-analyze.py /workspace/compare-stacks/compare-analyze.py

# Create necessary directories with proper permissions for OpenShift
# OpenShift runs with arbitrary UID but GID 0, so we need group write permissions
RUN mkdir -p /workspace /requests && \
    # Set permissions for arbitrary UID (OpenShift compatibility)
    chmod -R g+rwX /workspace /requests && \
    chgrp -R 0 /workspace /requests

WORKDIR /workspace

# Note: OpenShift will override this with an arbitrary UID anyway
USER 1001
