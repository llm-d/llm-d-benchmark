#!/usr/bin/env bash

# Installs dependencies for different load formats
# Set server extra arguments

export LLMDBENCH_VLLM_TENSORIZER_URI=""
export LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG="{}"

# installs dependencies for load formats
if [[ ${LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT} == "fastsafetensors" ]]; then
    pip install --root-user-action=ignore fastsafetensors==0.1.14
elif [[ ${LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT} == "tensorizer" ]]; then
    pip install --root-user-action=ignore tensorizer==2.10.1
    # path to save serialized file
    export LLMDBENCH_VLLM_TENSORIZER_URI="${HF_HOME}/${LLMDBENCH_VLLM_STANDALONE_MODEL}/v1/model.tensors"
    export LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG="{ \"tensorizer_uri\": \"$LLMDBENCH_VLLM_TENSORIZER_URI\" }"
elif [[ ${LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT} == "runai_streamer" ]]; then
    pip install --root-user-action=ignore runai==0.4.1
    pip install --root-user-action=ignore runai-model-streamer==0.13.2
    # controls the level of concurrency and number of OS threads
    # reading tensors from the file to the CPU buffer
     https://github.com/run-ai/runai-model-streamer/blob/master/docs/src/env-vars.md
    export LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG="{ \"concurrency\": 32 }"
fi

echo "vllm extra arguments: '${LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG}'"