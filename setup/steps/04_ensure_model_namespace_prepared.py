import os
import sys
import subprocess
import shlex
from pathlib import Path

# helper functions used from env.sh like announce, check_storage_class_and_afinity, etc
# is implimented in bash, once it is converted to python this will need to be changed to call the python functions instead of subprocess calls


def exec_command(command: str) -> list[bool, str]:
    result = None
    try:
        result = subprocess.run([command], capture_output=True, text=True)
    except Exception as e:
        print(f'Command: {command} failed ... {e}')

    if result.returncode == 0:
        return True, result.stdout
    else:
        return False, result.stderr


# ==================
# Main script logic 
# ==================


def main():

    # load all necessary environment variables from the environment
    vllm_namespace =        os.getenv("LLMDBENCH_VLLM_COMMON_NAMESPACE", "default-namespace")
    kcmd =                  os.getenv("LLMDBENCH_CONTROL_KCMD", "kubectl")
    dry_run =               os.getenv("LLMDBENCH_CONTROL_DRY_RUN", "false").lower() in ['true', '1']
    verbose =               os.getenv("LLMDBENCH_CONTROL_VERBOSE", "false").lower() in ['true', '1']
    model_list_str =        os.getenv("LLMDBENCH_DEPLOY_MODEL_LIST", "")
    scmd =                  os.getenv("LLMDBENCH_CONTROL_SCMD", "sed") # Not used directly, but shows it's read
    work_dir =              os.getenv("LLMDBENCH_CONTROL_WORK_DIR", ".")
    current_step =          os.getenv("LLMDBENCH_CURRENT_STEP", "step")
    vllm_pvc_name =         os.getenv("LLMDBENCH_VLLM_COMMON_PVC_NAME", "model-cache-pvc")
    vllm_storage_class =    os.getenv("LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS", "standard")
    vllm_storage_size =     os.getenv("LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE", "20Gi")
    vllm_download_timeout = os.getenv("LLMDBENCH_VLLM_COMMON_PVC_DOWNLOAD_TIMEOUT", "3600s")
    deployer_dir =          os.getenv("LLMDBENCH_DEPLOYER_DIR", ".")
    vllm_hf_token_name =    os.getenv("LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME", "")
    vllm_hf_token_key =     os.getenv("LLMDBENCH_VLLM_COMMON_HF_TOKEN_KEY", "")
    hf_token =              os.getenv("LLMDBENCH_HF_TOKEN", "")
    is_openshift =          os.getenv("LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT", "0") == "1"
    vllm_service_account =  os.getenv("LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT", "default")
    main_dir =              os.getenv("LLMDBENCH_MAIN_DIR", '.')
    
    exec_command(f"source \"{vllm_namespace}/env.sh\"")
    
    # initial checks and cleanup
    exec_command(f'announce \"Checking if \"{vllm_namespace}\" is prepared.\"')

    # check storage class and afinity is a function exported by env.sh
    if exec_command(command="check_storage_class_and_affinity")[0]:
        exec_command(f'announce \"Failed to check storage class and affinity\"')
        sys.exit(1)

    #  loop through models and call the installer script
    models = [model.strip() for model in model_list_str.split(',') if model.strip()]

    for model in models:
        
        delete_job_cmd = f"\"{kcmd} --namespace \"{vllm_namespace}\" delete job download-model --ignore-not-found\" {dry_run} {verbose}"
        if exec_command(command=f"llmdbench_execute_cmd \"{delete_job_cmd}")[0]:
            exec_command(f'announce \"llmdbench_execute_cmd failed ...\"')


        _, download_model = exec_command(command=f'model_attribute \"{model}\" model')

        model_artifact_uri = f"pvc://{vllm_namespace}/models/{download_model}"
        
        # in prev bash script ... PROTOCOL=${MODEL_ARTIFACT_URI%%://*} is used, and splits by :// 
        # then pvc_and_model_path is the path after protocol, so they can be asigned with the same split
        protocol, pvc_and_model_path = model_artifact_uri.split('://') # protocol var unused but exists in prev script
        pvc_name, model_path = pvc_and_model_path.split('/')


        exec_command(f'create_namespace \"{kcmd}\" \"{vllm_namespace}\"')

        exec_command(f'create_or_update_hf_secret \
                    \"{kcmd}\" \"{vllm_namespace}\" \
                    \"{vllm_hf_token_name}\" \
                    \"{vllm_hf_token_key}\" \
                    \"{hf_token}')


        exec_command(f'validate_and_create_pvc \
                    \"{kcmd}\" \
                    \"{vllm_namespace}\" \
                    \"{download_model}\" \
                    \"{pvc_name}\" \
                    \"{vllm_storage_size}\" \
                    \"{vllm_storage_class}\"')
        
        exec_command(f'launch_download_job \
                    \"{kcmd}\" \
                    \"{vllm_namespace}\" \
                    \"{vllm_hf_token_name}\" \
                    \"{download_model}\" \
                    \"{model_path}\" \
                    \"{pvc_name}\"')
        
        exec_command(f'wait_for_download_job \
                    \"{kcmd}\" \
                    \"{vllm_namespace}\" \
                    \"{vllm_download_timeout}\"')

        exec_command(f'announce \"llm-d-deployer prepared namespace\"')


        if is_openshift:
            exec_command(f'llmdbench_execute_cmd \
                    \"{kcmd}\" \
                    adm policy add-scc-to-user anyuid \
                    -z \"{vllm_service_account}\" \
                    -n \"{vllm_namespace}\" \
                    \"{dry_run}\" \
                    \"{verbose}\" 1 1 1')
            
            exec_command(f'llmdbench_execute_cmd \
                    \"{kcmd}\" \
                    adm policy add-scc-to-user privileged \
                    -z \"{vllm_service_account}\" \
                    -n \"{vllm_namespace}\" \
                    \"{dry_run}\" \
                    \"{verbose}\" 1 1 1')

        
        exec_command(f'announce \"{vllm_namespace}\" prepared\"')
    
    exec_command(f'announce \"{vllm_namespace}\" prepared\"')
    config_map_file = f'{work_dir}/setup/yamls/{current_step}_configmap_preprocesses.yaml'

    yaml_content = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: llm-d-benchmark-preprocesses
  namespace: {vllm_namespace}
data:
"""
    
    # find all files in the directory
    # equiv of find ${LLMDBENCH_MAIN_DIR}/workload/preprocesses -type f
    preprocess_dir = Path(main_dir) / "workload" / "preprocesses"

    try:
        file_paths = sorted([path for path in preprocess_dir.rglob('*') if path.is_file()])
    except FileNotFoundError:
        print(f"Error: Directory not found at {preprocess_dir}")
        file_paths = []

    with open(config_map_file, 'a', encoding='utf-8') as outfile:
        for path in file_paths:
            # get just the filename from the full path.
            # this is much simpler than rev | cut | rev.
            filename = path.name
            
            # write the header line, from ...
            # echo "  $filename: |" >> "$configmapfile"
            outfile.write(f"  {filename}: |\n")
            
            # open the source file to read its content
            with open(path, 'r', encoding='utf-8') as infile:
                # read file line-by-line
                for line in infile:
                    # write each line to the output file, indented with 4 spaces.
                    # echo "    $line" >> "$configmapfile" line already has \n
                    outfile.write(f"    {line}")
    
    exec_command(f'llmdbench_execute_cmd \
                    \"{kcmd} \
                    apply -f \
                    {config_map_file}\" \
                    {dry_run} \
                    {verbose}')
    
    exec_command(f'\"announce \"{config_map_file}\" created.\"')

    return  0;

if __name__ == "__main__":
    sys.exit(main())
