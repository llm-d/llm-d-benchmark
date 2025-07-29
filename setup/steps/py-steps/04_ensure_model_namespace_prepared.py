import os
import sys
import time
import base64
from pathlib import Path
from typing import Tuple

import pykube
from pykube.exceptions import PyKubeError

import asyncio

import subprocess

current_file = Path(__file__).resolve()

# get the projects root directory by going up 2 parent directories
project_root = current_file.parents[2]

#add the project root to the system path
sys.path.insert(0, str(project_root))

from functions import (announce, 
                       wait_for_job, 
                       validate_and_create_pvc, 
                       launch_download_job, 
                       model_attribute,
                       create_namespace)
                      


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

    os.environ['CURRENT_STEP_NAME'] =  os.path.splitext(os.path.basename(__file__))[0]

    vllm_namespace =        os.getenv("LLMDBENCH_VLLM_COMMON_NAMESPACE", "default-namespace")
    kcmd =                  os.getenv("LLMDBENCH_CONTROL_KCMD", "kubectl")
    dry_run =               os.getenv("LLMDBENCH_CONTROL_DRY_RUN", "false") == "1"
    control_dir =           os.getenv("LLMDBENCH_CONTROL_DIR", "")
    verbose =               os.getenv("LLMDBENCH_CONTROL_VERBOSE", "0")  == "1"
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

    api = None
    if dry_run:
        announce("DRY RUN enabled. No actual changes will be made.")

    try:
        api = pykube.HTTPClient(pykube.KubeConfig.from_file(os.path.expanduser('~/.kube/config')))
    except FileNotFoundError:
        print("Kubeconfig file not found. Ensure you are logged into a cluster.")
        sys.exit(1)

    announce(f'🔍 Preparing namespace "{vllm_namespace}"...')
    create_namespace(api=api, namespace_name=vllm_namespace, dry_run=dry_run)


    if hf_token:
        announce(f'🔑 Creating or updating secret "{vllm_hf_token_name}"...')
        secret_obj = {
            "apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": vllm_hf_token_name, "namespace": vllm_namespace},
            "type": "Opaque",
            "data": {vllm_hf_token_key: base64.b64encode(hf_token.encode()).decode()}
        }
        secret = pykube.Secret(api, secret_obj)
        if not dry_run:
            if secret.exists(): secret.update()
            else: secret.create()
            announce("Secret created/updated.")


    
    models = [model.strip() for model in model_list_str.split(',') if model.strip()]
    for model_name in models:

        download_model = model_attribute(model=model_name, attribute='model')
        model_artifact_uri = f"pvc://{vllm_pvc_name}/models/{download_model}"
        protocol, pvc_and_model_path = model_artifact_uri.split('://') # protocol var unused but exists in prev script
        pvc_name, model_path = pvc_and_model_path.split('/', 1) # split from first occurence

        validate_and_create_pvc(
            api=api,
            namespace=vllm_namespace,
            download_model=download_model,
            pvc_name=vllm_pvc_name,
            pvc_size=vllm_storage_size,
            pvc_class=vllm_storage_class,
            dry_run=dry_run
        )

        announce(f'🔽 Launching download job for model: "{model_name}"')

        launch_download_job(
            namespace=vllm_namespace,
            secret_name=vllm_hf_token_name,
            download_model=model_name,
            model_path=model_path,
            pvc_name=vllm_pvc_name,
            dry_run=dry_run,
            verbose=verbose
        )

        asyncio.run(wait_for_job(
            job_name='download-model',
            namespace=vllm_namespace,
            timeout=vllm_download_timeout,
        ))

    # possibly needs to be impliment, is not very simple with pykube
    #if is_openshift:
    #    add_scc_to_service_account(api, "anyuid", vllm_service_account, vllm_namespace, dry_run)
    #    add_scc_to_service_account(api, "privileged", vllm_service_account, vllm_namespace, dry_run)

    announce("🚚 Creating configmap with contents of all files under workload/preprocesses...")
    config_map_name = "llm-d-benchmark-preprocesses"
    config_map_data = {}
    preprocess_dir = Path(main_dir) / "workload" / "preprocesses"

    try:
        file_paths = sorted([p for p in preprocess_dir.rglob('*') if p.is_file()])
        # this loop reads every file and adds its content to the dictionary
        for path in file_paths:
            config_map_data[path.name] = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        print(f"Warning: Directory not found at {preprocess_dir}. Creating empty ConfigMap.")

    cm_obj = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": config_map_name, "namespace": vllm_namespace},
        "data": config_map_data
    }
    
    cm = pykube.ConfigMap(api, cm_obj)
    if not dry_run:
        if cm.exists(): cm.update()
        else: cm.create()
        print(f'ConfigMap "{config_map_name}" created/updated.')

    announce(f'✅ Namespace "{vllm_namespace}" prepared successfully.')
    return 0

if __name__ == "__main__":
    sys.exit( main() )