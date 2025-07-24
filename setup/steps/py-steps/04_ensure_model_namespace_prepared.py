import os
import sys
import subprocess
from pathlib import Path
from typing import Tuple

# helper functions used from env.sh like announce, check_storage_class_and_afinity, etc
# is implimented in bash, once it is converted to python this will need to be changed to call the python functions instead of subprocess calls


def exec_command(command: str, print_out = True) -> Tuple[bool, str]:
    result = None
    try:
        
        # tee /dev/tty writes a copy of the output to the current terminal screen
        # 2>&1 merges error messages with standard output so they are also printed and captured
        # set -o pipefail ensures that if the original command fails, the whole pipe fails
        modified_command = ""
        if print_out:
            # this version prints output to the screen using tee and allows it to be captured
            modified_command = f"set -o pipefail; ({command}) 2>&1 | tee /dev/tty"
        else:
            # this runs silently but still merges stderr with stdout to be captured.
            modified_command = f"set -o pipefail; ({command}) 2>&1"

        # use --noprofile and --norc to ensure a clean bash environment
        result = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-c", modified_command],
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        print(f'Command: {command} failed ... {e}')

    if result and result.returncode == 0:
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
    dry_run =               os.getenv("LLMDBENCH_CONTROL_DRY_RUN", "false")
    control_dir =           os.getenv("LLMDBENCH_CONTROL_DIR", "")
    verbose =               os.getenv("LLMDBENCH_CONTROL_VERBOSE", "false") 
    model_list_str =        os.getenv("LLMDBENCH_DEPLOY_MODEL_LIST", "")
    scmd =                  os.getenv("LLMDBENCH_CONTROL_SCMD", "sed") # Not used directly, but shows it's read
    work_dir =              os.getenv("LLMDBENCH_CONTROL_WORK_DIR", ".")
    current_step =          os.getenv("LLMDBENCH_CURRENT_STEP", "step")
    vllm_pvc_name =         os.getenv("LLMDBENCH_VLLM_COMMON_PVC_NAME", "model-cache-pvc")
    vllm_storage_class =    os.getenv("LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS", "standard")
    vllm_storage_size =     os.getenv("LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE", "20Gi")
    vllm_download_timeout = os.getenv("LLMDBENCH_VLLM_COMMON_PVC_DOWNLOAD_TIMEOUT", "3600s")
    vllm_hf_token_name =    os.getenv("LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME", "")
    vllm_hf_token_key =     os.getenv("LLMDBENCH_VLLM_COMMON_HF_TOKEN_KEY", "")
    hf_token =              os.getenv("LLMDBENCH_HF_TOKEN", "")
    is_openshift =          os.getenv("LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT", "0") 
    vllm_service_account =  os.getenv("LLMDBENCH_VLLM_COMMON_SERVICE_ACCOUNT", "default")
    main_dir =              os.getenv("LLMDBENCH_MAIN_DIR", '.')

    exec_command(f'source \"{control_dir}/env.sh\"')

    # initial checks and cleanup
    exec_command(f'announce \"🔍Checking if \"{vllm_namespace}\" is prepared.\"')

    # check storage class and afinity is a function exported by env.sh
    if not exec_command("check_storage_class_and_affinity")[0]:
        exec_command(f'announce \"Failed to check storage class and affinity\"')
        sys.exit(1)

    #  loop through models and call the installer script
    models = [model.strip() for model in model_list_str.split(',') if model.strip()]
    
    # ran in every loop so just make template once here
    delete_job_cmd = f'llmdbench_execute_cmd \"{kcmd} --namespace \"{vllm_namespace}\" delete job download-model --ignore-not-found\" {dry_run} {verbose}'
    
    for model in models:
        exec_command(delete_job_cmd) 
        
        _, download_model = exec_command(f'model_attribute \"{model}\" \"model\"', print_out=False)

        model_artifact_uri = f"pvc://{vllm_pvc_name}/models/{download_model}"
        
        # in prev bash script ... PROTOCOL=${MODEL_ARTIFACT_URI%%://*} is used, and splits by :// 
        # then pvc_and_model_path is the path after protocol, so they can be asigned with the same split
        protocol, pvc_and_model_path = model_artifact_uri.split('://') # protocol var unused but exists in prev script
        pvc_name, model_path = pvc_and_model_path.split('/', 1) # split from first occurence

        prefix = f'\"{kcmd}\" \"{vllm_namespace}\"'


        exec_command(f'create_namespace {prefix}')


        exec_command(f'create_or_update_hf_secret \
                    {prefix} \
                    \"{vllm_hf_token_name}\" \
                    \"{vllm_hf_token_key}\" \
                    \"{hf_token}\"')

        exec_command(f'validate_and_create_pvc \
                    {prefix} \
                    \"{download_model}\" \
                    \"{pvc_name}\" \
                    \"{vllm_storage_size}\" \
                    \"{vllm_storage_class}\"')
        

        exec_command(f'launch_download_job \
                    {prefix} \
                    \"{vllm_hf_token_name}\" \
                    \"{download_model}\" \
                    \"{model_path}\" \
                    \"{pvc_name}\"')
        
        exec_command(f'wait_for_download_job \
                    {prefix} \
                    \"{vllm_download_timeout}\"')

        exec_command(f'announce \"✅ llm-d-deployer prepared namespace\"')

        if is_openshift == "1":
            exec_command(f'llmdbench_execute_cmd \
                    \"{kcmd} \
                    adm policy add-scc-to-user anyuid \
                    -z {vllm_service_account} \
                    -n {vllm_namespace}\" \
                    \"{dry_run}\" \
                    \"{verbose}\" 1 1 1')
            
            exec_command(f'llmdbench_execute_cmd \
                    \"{kcmd} \
                    adm policy add-scc-to-user privileged \
                    -z {vllm_service_account} \
                    -n {vllm_namespace}\" \
                    \"{dry_run}\" \
                    \"{verbose}\" 1 1 1')

        

    exec_command(f'announce \"✅ Namespace \"{vllm_namespace}\" prepared\"')
    config_map_file = f'{work_dir}/setup/yamls/{current_step}_configmap_preprocesses.yaml'
    
    exec_command(f'announce \"🚚 Creating configmap with contents of all files under workload/preprocesses ...\"')

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
    with open(config_map_file, 'w', encoding='utf-8') as outfile:
        outfile.write(yaml_content)
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

    exec_command(f'announce \"✅ {config_map_file} created\"')

    return  0;

if __name__ == "__main__":
    sys.exit(main())
