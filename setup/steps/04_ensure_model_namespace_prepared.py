import os
import sys
import time
import base64
from pathlib import Path

import pykube
from pykube.exceptions import PyKubeError

import asyncio


current_file = Path(__file__).resolve()

# get the projects root directory by going up 1 parent directories
project_root = current_file.parents[1]

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


class SecurityContextConstraints(pykube.objects.APIObject):
    version = "security.openshift.io/v1"
    endpoint = "securitycontextconstraints"
    kind = "SecurityContextConstraints"

def is_openshift(api: pykube.HTTPClient) -> bool:
    try:
        # the priviledged scc is a standard built in component for oc
        # if we get we are on oc
        SecurityContextConstraints.objects(api).get(name="privileged")
        announce("OpenShift cluster detected")
        return True
    except PyKubeError as e:
        # a 404 error means the scc resource type itself doesnt exist
        if e.code == 404:
            announce("Standard Kubernetes cluster detected (not OpenShift)")
            return False
        # for other errors like 403, we might be on OpenShift but lack permissions
        #  if we cant query sccs we cant modify them either
        announce(f'Could not query SCCs due to an API error (perhaps permissions?): {e}. Assuming not OpenShift for SCC operations')
        return False
    except Exception as e:
        #  other potential non pykube errors
        announce(f'An unexpected error occurred while checking for OpenShift: {e}. Assuming not OpenShift for SCC operations')
        return False

def add_scc_to_service_account(api: pykube.HTTPClient, scc_name: str, service_account_name: str, namespace: str, dry_run: bool):
    announce(f'Attempting to add SCC "{scc_name}" to Service Account "{service_account_name}" in namespace "{namespace}"...')

    try:
        # get the specified SecurityContextConstraints object
        scc = SecurityContextConstraints.objects(api).get(name=scc_name)
    except PyKubeError as e:
        if e.code == 404:
            announce(f'Warning: SCC "{scc_name}" not found. Skipping.')
            return
        else:
            # re raise other API errors
            raise e

    # the username for a service account in scc is in the format:
    # system:serviceaccount:<namespace>:<service_account_name>
    sa_user_name = f'system:serviceaccount:{namespace}:{service_account_name}'

    # ensure the users field exists in the scc object it might be None or not present
    if "users" not in scc.obj or scc.obj["users"] is None:
        scc.obj["users"] = []

    # check if the service account is already in the list
    if sa_user_name in scc.obj["users"]:
        announce(f'Service Account "{sa_user_name}" already has SCC "{scc_name}". No changes needed')
    else:
        if dry_run:
            announce(f'DRY RUN: Would add "{sa_user_name}" to SCC "{scc_name}"')
        else:
            announce(f'Adding "{sa_user_name}" to SCC "{scc_name}"...')
            scc.obj["users"].append(sa_user_name)
            scc.update()
            announce(f'Successfully updated SCC "{scc_name}"')

def main():

    os.environ["CURRENT_STEP_NAME"] =  os.path.splitext(os.path.basename(__file__))[0]

    ev = {}
    for key in dict(os.environ).keys():
        if "LLMDBENCH_" in key:
            ev.update({key.split("LLMDBENCH_")[1].lower():os.environ.get(key)})

    llmdbench_execute_cmd(actual_cmd=f'source "{ev["control_dir"]}/env.sh"', dry_run=ev["control_dry_run"] == '1', verbose=ev["control_verbose"] == '1')

    api = kube_connect()
    if ev["control_dry_run"] == '1':
        announce("DRY RUN enabled. No actual changes will be made.")



    announce(f'🔍 Preparing namespace "{ev["vllm_common_namespace"]}"...')
    create_namespace(api=api, namespace_name=ev["vllm_common_namespace"], dry_run=ev["control_dry_run"] == '1')


    if ev["hf_token"]:
        announce(f'🔑 Creating or updating secret "{ev["vllm_common_hf_token_name"]}"...')
        secret_obj = {
            "apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": ev["vllm_common_hf_token_name"], "namespace": ev["vllm_common_namespace"]},
            "type": "Opaque",
            "data": {ev["vllm_common_hf_token_key"]: base64.b64encode(ev["hf_token"].encode()).decode()}
        }
        secret = pykube.Secret(api, secret_obj)
        if ev["control_dry_run"] != '1':
            if secret.exists(): secret.update()
            else: secret.create()
            announce("Secret created/updated.")



    models = [model.strip() for model in ev["deploy_model_list"].split(',') if model.strip()]
    for model_name in models:

        if [[ ev["VLLM_MODELSERVICE_URI_PROTOCOL"]] == "pv" or ev["CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE"] == "1" ]
            download_model = model_attribute(model=model_name, attribute="model")
            model_artifact_uri = f'pvc://{ev["vllm_common_pvc_name"]}/models/{download_model}'
            protocol, pvc_and_model_path = model_artifact_uri.split("://") # protocol var unused but exists in prev script
            pvc_name, model_path = pvc_and_model_path.split('/', 1) # split from first occurence

            validate_and_create_pvc(
                api=api,
                namespace=ev["vllm_common_namespace"],
                download_model=download_model,
                pvc_name=ev["vllm_common_pvc_name"],
                pvc_size=ev["vllm_common_pvc_model_cache_size"],
                pvc_class=ev["vllm_common_pvc_storage_class"],
                dry_run=ev["control_dry_run"] == '1'
            )

            announce(f'🔽 Launching download job for model: "{model_name}"')
            launch_download_job(
                namespace=ev["vllm_common_namespace"],
                secret_name=ev["vllm_common_hf_token_name"],
                download_model=download_model,
                model_path=model_path,
                pvc_name=ev["vllm_common_pvc_name"],
                dry_run=ev["control_dry_run"] == '1',
                verbose=ev["control_verbose"] == '1'
            )

            asyncio.run(wait_for_job(
                job_name="download-model",
                namespace=ev["vllm_common_namespace"],
                timeout=ev["vllm_common_pvc_download_timeout"],
            ))

    if is_openshift(api):
        # vllm workloads may need to run as a specific non-root UID , the  default SA needs anyuid
        # some setups might also require privileged access for GPU resources
        add_scc_to_service_account(api, "anyuid", ev["vllm_common_service_account"], ev["vllm_common_namespace"], ev["control_dry_run"]=='1')
        add_scc_to_service_account(api, "privileged", ev["vllm_common_service_account"], ev["vllm_common_namespace"], ev["control_dry_run"]=='1')


    announce(f'🚚 Creating configmap with contents of all files under workload/preprocesses...')
    config_map_name = "llm-d-benchmark-preprocesses"
    config_map_data = {}
    preprocess_dir = Path(ev["main_dir"]) / "workload" / "preprocesses"

    try:
        file_paths = sorted([p for p in preprocess_dir.rglob('*') if p.is_file()])
        # this loop reads every file and adds its content to the dictionary
        for path in file_paths:
            config_map_data[path.name] = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        announce(f'Warning: Directory not found at {preprocess_dir}. Creating empty ConfigMap.')

    cm_obj = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": config_map_name, "namespace": ev["vllm_common_namespace"]},
        "data": config_map_data
    }

    cm = pykube.ConfigMap(api, cm_obj)
    if ev["control_dry_run"] != '1':
        if cm.exists(): cm.update()
        else: cm.create()
        announce(f'ConfigMap "{config_map_name}" created/updated.')

    announce(f'✅ Namespace "{ev["vllm_common_namespace"]}" prepared successfully.')
    return 0

if __name__ == "__main__":
    sys.exit( main() )
