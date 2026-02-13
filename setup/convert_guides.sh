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
# convert ${GUIDE} for guadi
# convert ${GUIDE} for cpu
# convert ${GUIDE} for tpu
# convert ${GUIDE} for xpu

# pd-disaggregation (modelservice)
GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/pd-disaggregation'
# convert ${GUIDE}
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
convert ${GUIDE}
convert ${GUIDE} for lmcache-connector
convert ${GUIDE} for offloading-connector
convert ${GUIDE} for tpu

# wide-ep-lws (kustomize)
GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/wide-ep-lws'
# convert ${GUIDE} for base
# convert ${GUIDE} for coreweave
convert ${GUIDE} for gke-a4
convert ${GUIDE} for gke

# workload-autoscaling
GUIDE='https://github.com/llm-d/llm-d/tree/main/guides/workload-autoscaling'
# convert ${GUIDE}
# skipped; not tested