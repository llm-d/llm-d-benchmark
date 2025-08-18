"""
Mocks DB storing info about GPU specs, model specs, etc.
"""
from dataclasses import dataclass
import pickle
import pandas as pd

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

columns = ['Name', 'Model', 'GPU', 'DP', 'TP', 'PP', 'EP', 'Replicas',
       'P_DP', 'P_TP', 'P_PP', 'P_EP', 'P_Replicas', 'D_DP', 'D_TP', 'D_PP',
       'D_EP', 'D_Replicas', 'Concurrency', 'ISL', 'OSL', 'Backend',
       'Duration', 'Completed', 'Request_Throughput',
       'Output_Token_Throughput', 'Total_Token_Throughput', 'Mean_TTFT_ms',
       'Mean_TPOT_ms', 'Mean_ITL_ms', 'Mean_E2EL_ms', 'Is_PD', 'Num_GPUs',
       'Thpt_per_GPU', 'Thpt_per_User']

input_cols = ['Model', 'GPU', 'DP', 'TP', 'PP', 'EP', 'Replicas',
       'P_DP', 'P_TP', 'P_PP', 'P_EP', 'P_Replicas', 'D_DP', 'D_TP', 'D_PP',
       'D_EP', 'D_Replicas', 'Concurrency', 'ISL', 'OSL', 'Backend', 'Is_PD',
       'Num_GPUs']

output_cols = ['Request_Throughput',
       'Output_Token_Throughput', 'Total_Token_Throughput', 'Mean_TTFT_ms',
       'Mean_TPOT_ms', 'Mean_ITL_ms', 'Mean_E2EL_ms',
       'Thpt_per_GPU', 'Thpt_per_User']

def get_color_by_col(input: str):
    """
    Removes the selected input from input_cols
    """
    color_by = input_cols.copy()
    color_by.remove(input)
    return color_by

def read_benchmark_data():
    with open("./config-rec/df.pkl", 'rb') as file:
        data = pickle.load(file)
        df = pd.DataFrame(data)
        print(df.columns)

        # Clean data
        df.drop('Directory', axis=1, inplace=True)

        return df
