#!/usr/bin/env bash

# Installs dependencies for different load formats
# Set server extra arguments

export LLMDBENCH_VLLM_TENSORIZER_URI=""
export LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG="{}"

# export a custom log format path
shopt -s nocasematch # Enable case-insensitive matching
if [[ ${VLLM_LOGGING_LEVEL} == "DEBUG" ]]; then
    # export a custom log format path
    # the preprocess python script will create the file with custom log format
    export VLLM_LOGGING_CONFIG_PATH=/tmp/vllm_logging_config.json
fi
shopt -u nocasematch # Disable case-insensitive matching

# installs dependencies for load formats
if [[ ${LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT} == "fastsafetensors" ]]; then
    pip install --root-user-action=ignore fastsafetensors==0.1.15
elif [[ ${LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT} == "tensorizer" ]]; then
    pip install --root-user-action=ignore tensorizer==2.10.1
    # path to save serialized file
    export LLMDBENCH_VLLM_TENSORIZER_URI="${HF_HOME}/${LLMDBENCH_VLLM_STANDALONE_MODEL}/v1/model.tensors"
    export LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG="{ \"tensorizer_uri\": \"$LLMDBENCH_VLLM_TENSORIZER_URI\" }"
elif [[ ${LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT} == "runai_streamer" ]]; then
    pip install --root-user-action=ignore runai==0.4.1
    # controls the level of concurrency and number of OS threads
    # reading tensors from the file to the CPU buffer
     https://github.com/run-ai/runai-model-streamer/blob/master/docs/src/env-vars.md
    export LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG="{ \"concurrency\": 32 }"
fi

echo "vllm extra arguments: '${LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG}'"