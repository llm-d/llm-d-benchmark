#!/usr/bin/env bash

# Installs dependencies for different load formats
# Handles server extra arguments

export LLMDBENCH_VLLM_TENSORIZER_URI=""

# export a custom log format path
shopt -s nocasematch # Enable case-insensitive matching
if [[ ${VLLM_LOGGING_LEVEL} == "DEBUG" ]]; then
    # export a custom log format path
    # the preprocess python script will create the file with custom log format
    export VLLM_LOGGING_CONFIG_PATH=/tmp/vllm_logging_config.json
fi
shopt -u nocasematch # Disable case-insensitive matching

# unescape double quotes if existent
export LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG=$(echo "$LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG" | sed 's/\\"/"/g')

# installs dependencies for load formats
if [[ ${LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT} == "fastsafetensors" ]]; then
    pip install --root-user-action=ignore fastsafetensors==0.1.15
elif [[ ${LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT} == "tensorizer" ]]; then
    sudo apt update
    sudo apt install -y jq
    pip install --root-user-action=ignore tensorizer==2.10.1
    # path to save serialized file
    export LLMDBENCH_VLLM_TENSORIZER_URI="${HF_HOME}/${LLMDBENCH_VLLM_STANDALONE_MODEL}/v1/model.tensors"
    export LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG=$(echo "$LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG" | jq '.tensorizer_uri = env.LLMDBENCH_VLLM_TENSORIZER_URI' | tr -d '\n')
elif [[ ${LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT} == "runai_streamer" ]]; then
    sudo apt update
    sudo apt install -y jq
    pip install --root-user-action=ignore runai==0.4.1
    # controls the level of concurrency and number of OS threads
    # reading tensors from the file to the CPU buffer
    # https://github.com/run-ai/runai-model-streamer/blob/master/docs/src/env-vars.md
    export LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG=$(echo "$LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG" | jq '.concurrency = 32' | tr -d '\n')
fi

echo "vllm extra arguments: '${LLMDBENCH_VLLM_STANDALONE_MODEL_LOADER_EXTRA_CONFIG}'"