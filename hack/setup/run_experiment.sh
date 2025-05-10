#!/usr/bin/env bash

# Source the main env.sh first to set up common environment variables
if [ -f "/workspace/env.sh" ]; then
    echo "Sourcing environment file: /workspace/env.sh"
    source /workspace/env.sh
else
    echo "❌ Environment file /workspace/env.sh not found"
    exit 1
fi

# Default to 'standalone' environment if none is provided
EXPERIMENT_FILE=${EXPERIMENT_FILE:-"examples/example_llm-d-lmbenchmark-openshift.py"}  # Default Python script if none is provided

# Ensure that SCENARIO_FILE is set, and source the environment file specified
if [ -z "${SCENARIO_FILE}" ]; then
    echo "❌ SCENARIO_FILE variable is not set. Please provide the full path to the scenario file."
    exit 1
fi

# Source the environment variables from the provided scenario file
if [ -f "$SCENARIO_FILE" ]; then
    echo "Sourcing environment file: $SCENARIO_FILE"
    source $SCENARIO_FILE
else
    echo "❌ Scenario file $SCENARIO_FILE not found"
    exit 1
fi

# Set the Conda environment name statically
CONDA_ENV_NAME="fmperf-env"

# Ensure Conda is initialized correctly
if [ -f "/opt/miniconda/etc/profile.d/conda.sh" ]; then
    source "/opt/miniconda/etc/profile.d/conda.sh"
    conda activate ${CONDA_ENV_NAME}
else
    echo "❌ Could not find conda.sh. Please verify your Conda installation."
    exit 1
fi

# Ensure the correct Python environment is active
echo "✅ Python: $(which python3)"
echo "✅ Conda Environment: $(conda info --envs | grep '*' || true)"

# Install missing dependencies if necessary
python3 -m pip show urllib3 >/dev/null 2>&1 || python3 -m pip install urllib3
python3 -m pip show kubernetes >/dev/null 2>&1 || python3 -m pip install kubernetes
python3 -m pip show pandas >/dev/null 2>&1 || python3 -m pip install pandas

# Run the experiment with the specified Python file
echo "Running experiment using script: ${EXPERIMENT_FILE}"
python3 /workspace/fmperf/${EXPERIMENT_FILE}
