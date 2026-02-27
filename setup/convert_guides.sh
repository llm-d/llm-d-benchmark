#!/bin/bash

convert() {
    GUIDE="${1}"
    shift
    QUALIFIERS="$*"

    echo "-----"
    echo "Using genAI to convert ${GUIDE} ${QUALIFIERS}"
    echo "-----"

    claude --model aws/claude-sonnet-4-5 \
        --permission-mode bypassPermissions \
        -p "Convert llm-d guide ${GUIDE} ${QUALIFIERS}"

    echo "-----"
    echo "Result in scenarios/guides"
    echo "-----"

}

# main

# inference-scheduling (modelservice)
GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/inference-scheduling'
# convert ${GUIDE}
# convert ${GUIDE} for amd
# convert ${GUIDE} for cpu
# convert ${GUIDE} for hpu
# convert ${GUIDE} for tpu
# convert ${GUIDE} for xpu

# pd-disaggregation (modelservice)
GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/pd-disaggregation'
# convert ${GUIDE}
# convert ${GUIDE} for gke rdma
# convert ${GUIDE} for hpu
# convert ${GUIDE} for tpu
# convert ${GUIDE} for xpu

# precise-prefix-cache-aware
GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/precise-prefix-cache-aware'
# convert ${GUIDE}
# convert ${GUIDE} for xpu

# predicted-latency-based-scheduling (modelservice)
GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/predicted-latency-based-scheduling'
# skipped

# simulated-accelerators (modelservice)
GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/simulated-accelerators'
# convert ${GUIDE}
# skipped; convert works; not suitable for llm-d-benchmark

# tiered-prefix-cache (kustomize)
GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/tiered-prefix-cache'
# convert ${GUIDE}
# convert ${GUIDE} for cpu and lmcache-connector
# convert ${GUIDE} for cpu and offloading-connector
# convert ${GUIDE} for storage and llm-d-fs-connector
# convert ${GUIDE} for storage and lmcache-connector

# wide-ep-lws (kustomize)
GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/wide-ep-lws'
# convert ${GUIDE} for base
# convert ${GUIDE} for coreweave
# convert ${GUIDE} for gke-a4
# convert ${GUIDE} for gke

GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/wide-ep-lws/dp-aware'
# convert ${GUIDE} for base
# convert ${GUIDE} for coreweave
# convert ${GUIDE} for base with random values
# convert ${GUIDE} for coreweave random values

# workload-autoscaling
GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/workload-autoscaling'
# convert ${GUIDE}
# skipped; not tested