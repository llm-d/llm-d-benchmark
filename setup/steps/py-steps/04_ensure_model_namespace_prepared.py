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
                       create_namespace,
                       kube_connect,
                       llmdbench_execute_cmd)
                      

def main():

    os.environ['CURRENT_STEP_NAME'] =  os.path.splitext(os.path.basename(__file__))[0]

    ev = {}
    for key in dict(os.environ).keys():
        if 'LLMDBENCH_' in key:
            ev.update({key.split('LLMDBENCH_')[1].lower():os.environ.get(key)})

    llmdbench_execute_cmd(actual_cmd=f'source \"{ev['control_dir']}/env.sh\"', dry_run=ev['control_dry_run'] == '1', verbose=ev['control_verbose'] == '1')

    api = kube_connect()
    if ev['control_dry_run'] == '1':
        announce("DRY RUN enabled. No actual changes will be made.")



    announce(f'🔍 Preparing namespace "{ev['vllm_common_namespace']}"...')
    create_namespace(api=api, namespace_name=ev['vllm_common_namespace'], dry_run=ev['control_dry_run'] == '1')


    if ev['hf_token']:
        announce(f'🔑 Creating or updating secret "{ev['vllm_common_hf_token_name']}"...')
        secret_obj = {
            "apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": ev['vllm_common_hf_token_name'], "namespace": ev['vllm_common_namespace']},
            "type": "Opaque",
            "data": {ev['vllm_common_hf_token_key']: base64.b64encode(ev['hf_token'].encode()).decode()}
        }
        secret = pykube.Secret(api, secret_obj)
        if ev['control_dry_run'] != '1':
            if secret.exists(): secret.update()
            else: secret.create()
            announce("Secret created/updated.")


    
    models = [model.strip() for model in ev['deploy_model_list'].split(',') if model.strip()]
    for model_name in models:

        download_model = model_attribute(model=model_name, attribute='model')
        model_artifact_uri = f"pvc://{ev['vllm_common_pvc_name']}/models/{download_model}"
        protocol, pvc_and_model_path = model_artifact_uri.split('://') # protocol var unused but exists in prev script
        pvc_name, model_path = pvc_and_model_path.split('/', 1) # split from first occurence

        validate_and_create_pvc(
            api=api,
            namespace=ev['vllm_common_namespace'],
            download_model=download_model,
            pvc_name=ev['vllm_common_pvc_name'],
            pvc_size=ev['vllm_common_pvc_model_cache_size'],
            pvc_class=ev['vllm_common_pvc_storage_class'],
            dry_run=ev['control_dry_run'] == '1'
        )

        announce(f'🔽 Launching download job for model: "{model_name}"')

        launch_download_job(
            namespace=ev['vllm_common_namespace'],
            secret_name=ev['vllm_common_hf_token_name'],
            download_model=model_name,
            model_path=model_path,
            pvc_name=ev['vllm_common_pvc_name'],
            dry_run=ev['control_dry_run'] == '1',
            verbose=ev['control_verbose'] == '1'
        )

        asyncio.run(wait_for_job(
            job_name='download-model',
            namespace=ev['vllm_common_namespace'],
            timeout=ev['vllm_common_pvc_download_timeout'],
        ))

    # possibly needs to be impliment, is not very simple with pykube
    #if is_openshift:
    #    add_scc_to_service_account(api, "anyuid", vllm_service_account, vllm_namespace, dry_run)
    #    add_scc_to_service_account(api, "privileged", vllm_service_account, vllm_namespace, dry_run)

    announce("🚚 Creating configmap with contents of all files under workload/preprocesses...")
    config_map_name = "llm-d-benchmark-preprocesses"
    config_map_data = {}
    preprocess_dir = Path(ev['main_dir']) / "workload" / "preprocesses"

    try:
        file_paths = sorted([p for p in preprocess_dir.rglob('*') if p.is_file()])
        # this loop reads every file and adds its content to the dictionary
        for path in file_paths:
            config_map_data[path.name] = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        print(f"Warning: Directory not found at {preprocess_dir}. Creating empty ConfigMap.")

    cm_obj = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": config_map_name, "namespace": ev['vllm_common_namespace']},
        "data": config_map_data
    }
    
    cm = pykube.ConfigMap(api, cm_obj)
    if ev['control_dry_run'] != '1':
        if cm.exists(): cm.update()
        else: cm.create()
        print(f'ConfigMap "{config_map_name}" created/updated.')

    announce(f'✅ Namespace "{ev['vllm_common_namespace']}" prepared successfully.')
    return 0

if __name__ == "__main__":
    sys.exit( main() )