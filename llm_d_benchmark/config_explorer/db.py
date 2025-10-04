"""
Mocks DB storing info about common accelerators used for LLM serving and inference
"""

gpu_specs = {
    # https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/a100/pdf/nvidia-a100-datasheet-us-nvidia-1758950-r4-web.pdf
    # https://medium.com/@bijit211987/top-nvidia-gpus-for-llm-inference-8a5316184a10
    # https://www.databasemart.com/blog/best-nvidia-gpus-for-llm-inference-2025?srsltid=AfmBOopcvcdN6yzBF24k7_DyRS_csYOmNyDLJK7zq9Rg89weW6AQAx5F
    "NVIDIA-H100-80GB-HBM3": {
        "memory": 80
    },
    "NVIDIA-A100-40GB": {
        "memory": 40
    },
     "NVIDIA-A100-80GB": {
        "memory": 80
    },
     "NVIDIA-H100-80GB": {
        "memory": 80
    },
     "NVIDIA-L40-40GB": {
         "memory": 40
     },
     "NVIDIA-RTX-4090": {
         "memory": 24
     },
     "NVIDIA-RTX-5090": {
         "memory": 32
     },
     "NVIDIA-RTX-6000":{
        "memory": 48
     },
     "NVIDIA-A6000": {
        "memory": 48
     },
     "NVIDIA-A4000": {
        "memory": 16
     },
     "NVIDIA-T4": {
         "memory": 16
     }
}
