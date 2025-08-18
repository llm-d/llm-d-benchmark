"""
Mocks DB storing info about GPU specs, model specs, etc.
"""
from dataclasses import dataclass

@dataclass
class GPUSpec:
    name: str
    memory: int

gpu_specs = {
    # https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/a100/pdf/nvidia-a100-datasheet-us-nvidia-1758950-r4-web.pdf
    # https://medium.com/@bijit211987/top-nvidia-gpus-for-llm-inference-8a5316184a10
    # https://www.databasemart.com/blog/best-nvidia-gpus-for-llm-inference-2025?srsltid=AfmBOopcvcdN6yzBF24k7_DyRS_csYOmNyDLJK7zq9Rg89weW6AQAx5F
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
     "NVIDIA-A6000":{
        "memory": 48
     },
     "NVIDIA-A4000":{
        "memory": 16
     },
     "NVIDIA-T4": {
         "memory": 16
     }
}

benchmark_data = {
    "Model": [
        "ibm-granite/granite-3.3-8b-instruct",
        "ibm-granite/granite-3.2-8b-instruct",
              ],
    "Accelerator": [
        "NVIDIA-A100-40GB",
        "NVIDIA-A100-40GB",
                    ],
    "Memory": [
        40,
        40,
    ],
    "TP": [
        1,
        2,
    ],
    "Replicas": [
        1,
        2,
        ],
    "Prefill": [
        1,
        2,
    ],
    "Prefill TP": [
        1,
        2,
    ],
    "Decode": [
        1,
        2,
    ],
    "Decode Replicas": [
        1,
        1,
    ],
    "Number of Accelerators": [
        4,
        10,
    ],
    "Is PD": [
        True,
        True
    ],
    "TTFT": [
        10,
        20,
    ],
    "TPOT": [
        20,
        10,
    ],
    "Request latency": [
        2,
        3,
    ],
    "Request/s": [
        2,
        3,
    ],
    "Input tokens/s": [
        10,
        20,
    ],
    "Output tokens/s": [
        20,
        10,
    ],
    "Total tokens/s": [
        100,
        200
    ],
    "Cost": [
        0.23,
        0.19,
    ]

}