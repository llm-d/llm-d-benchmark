# IMPORTANT NOTE
# All parameters not defined here or exported externally will be the default values found in setup/env.sh
# Many commonly defined values were left blank (default) so that this scenario is applicable to as many environments as possible.

# Model parameters
#export LLMDBENCH_DEPLOY_MODEL_LIST="Qwen/Qwen3-0.6B"
export LLMDBENCH_DEPLOY_MODEL_LIST="facebook/opt-125m"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-8B-Instruct"
#export LLMDBENCH_DEPLOY_MODEL_LIST="meta-llama/Llama-3.1-70B-Instruct"

# PVC parameters
#             Storage class (leave uncommented to automatically detect the "default" storage class)
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=standard-rwx
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=shared-vast
#export LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=ocs-storagecluster-cephfs
#export LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE=1Ti

#             Affinity to select node with appropriate accelerator (leave uncommented to automatically detect GPU... WILL WORK FOR OpenShift, Kubernetes and GKE)
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=gpu.nvidia.com/model:H200                           # Kubernetes
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-tesla-a100  # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=cloud.google.com/gke-accelerator:nvidia-h100-80gb   # GKE
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-L40S                  # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-A100-SXM4-80GB        # OpenShift
#export LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu                                      # ANY GPU (useful for Minikube)

#             Uncomment to request specific network devices
#####export LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE=rdma/roce_gdr
#######export LLMDBENCH_VLLM_COMMON_NETWORK_RESOURCE=rdma/ib
#export LLMDBENCH_VLLM_COMMON_NETWORK_NR=4

#export LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT=auto (default is "auto")

#export LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT=safetensors
#export LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT=tensorizer
#export LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT=runai_streamer
#export LLMDBENCH_VLLM_STANDALONE_VLLM_LOAD_FORMAT=fastsafetensors

# set to debug so that all vllm log lines can be categorized
#export LLMDBENCH_VLLM_STANDALONE_VLLM_LOGGING_LEVEL=DEBUG

# source preprocessor script that will install libraries for some load formats and set env. variables
# run preprocessor python that will change the debug log date format and pre-serialize a model when using
# tensorizer load format
#export LLMDBENCH_VLLM_STANDALONE_PREPROCESS="source /setup/preprocess/standalone-preprocess.sh ; /setup/preprocess/standalone-preprocess.py"