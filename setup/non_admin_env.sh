# Replace with your namespace
export LLMDBENCH_VLLM_COMMON_NAMESPACE="naomi-llmd"
export LLMDBENCH_HARNESS_NAMESPACE="naomi-llmd"

# HuggingFace token (or leave blank and use a secret in the cluster)
# export LLMDBENCH_HF_TOKEN=<replace with your token>

export LLMDBENCH_VLLM_GAIE_CHART_NAME="oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool"