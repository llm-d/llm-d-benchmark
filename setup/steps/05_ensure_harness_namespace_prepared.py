import os
import sys
import time
import base64
from pathlib import Path

import pykube
from pykube.exceptions import PyKubeError

import asyncio

# Add project root to path for imports
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
sys.path.insert(0, str(project_root))

from functions import (
    announce,
    wait_for_job,
    validate_and_create_pvc,
    launch_download_job,
    model_attribute,
    kube_connect,
    llmdbench_execute_cmd,
    environment_variable_to_dict,
    is_openshift,
    SecurityContextConstraints,
    add_scc_to_service_account,
    get_image,
    kubectl_apply
)

def main():

    os.environ["LLMDBENCH_CURRENT_STEP"] = os.path.splitext(os.path.basename(__file__))[0]

    ev = {}
    environment_variable_to_dict(ev)

    env_cmd = f'source "{ev["control_dir"]}/env.sh"'
    result = llmdbench_execute_cmd(
        actual_cmd=env_cmd, dry_run=ev["control_dry_run"], verbose=ev["control_verbose"]
    )
    if result != 0:
        announce(f'❌ Failed while running "{env_cmd}" (exit code: {result})')
        exit(result)

    api, client = kube_connect(f'{ev["control_work_dir"]}/environment/context.ctx')

    if ev["control_dry_run"]:
        announce("DRY RUN enabled. No actual changes will be made.")

    announce(f'🔍 Preparing namespace "{ev["harness_namespace"]}"...')
    namespace_yaml = f"""apiVersion: v1
kind: Namespace
metadata:
  name: {ev["harness_namespace"]}
  namespace: {ev["vllm_common_namespace"]}
"""

    kubectl_apply(api=api, manifest_data=namespace_yaml, dry_run=ev["control_dry_run"])

    if ev["hf_token"]:
        secret_data = base64.b64encode(ev["hf_token"].encode()).decode()
        secret_yaml = f"""apiVersion: v1
kind: Secret
metadata:
  name: {ev["vllm_common_hf_token_name"]}
  namespace: {ev["harness_namespace"]}
type: Opaque
data:
  {ev["vllm_common_hf_token_key"]}: {secret_data}
"""
        kubectl_apply(api=api, manifest_data=secret_yaml, dry_run=ev["control_dry_run"])

    volumes = [
        model.strip() for model in ev["harness_pvc_name"].split(",") if model.strip()
    ]

    image = get_image(
        ev["image_registry"],
        ev["image_repo"],
        ev["image_name"],
        ev["image_tag"]
    )

    for volume in volumes:
          validate_and_create_pvc(
              api=api,
              client=client,
              namespace=ev["harness_namespace"],
              download_model='',
              pvc_name=volume,
              pvc_size=ev["harness_pvc_size"],
              pvc_class=ev["vllm_common_pvc_storage_class"],
              pvc_access_mode=ev['vllm_common_pvc_access_mode'],
              dry_run=ev["control_dry_run"]
          )

          pod_yaml = f"""apiVersion: v1
kind: Pod
metadata:
  name: access-to-harness-data-{volume}
  labels:
    app: llm-d-benchmark-harness
    role: llm-d-benchmark-data-access
  namespace: {ev["harness_namespace"]}
spec:
  containers:
  - name: rsync
    image: {image}
    imagePullPolicy: Always
    securityContext:
      runAsUser: 0
    command: ["rsync", "--daemon", "--no-detach", "--port=20873", "--log-file=/dev/stdout"]
    volumeMounts:
    - name: requests
      mountPath: /requests
#    - name: cache-volume
#      mountPath: {ev["vllm_standalone_pvc_mountpoint"]}
  volumes:
  - name: requests
    persistentVolumeClaim:
      claimName:  {ev["harness_pvc_name"]}
#  - name: cache-volume
#    persistentVolumeClaim:
#      claimName: {ev["vllm_standalone_pvc_mountpoint"]}
"""

          kubectl_apply(api=api, manifest_data=pod_yaml, dry_run=ev["control_dry_run"])

          service_yaml = f"""apiVersion: v1
apiVersion: v1
kind: Service
metadata:
  name: llm-d-benchmark-harness
  namespace: {ev["harness_namespace"]}
spec:
  ports:
  - name: rsync
    protocol: TCP
    port: 20873
    targetPort: 20873
  selector:
    app: llm-d-benchmark-harness
  type: ClusterIP
"""
          kubectl_apply(api=api, manifest_data=service_yaml, dry_run=ev["control_dry_run"])

    if is_openshift(api) and ev["user_is_admin"]:
        # vllm workloads may need to run as a specific non-root UID , the  default SA needs anyuid
        # some setups might also require privileged access for GPU resources
        add_scc_to_service_account(
            api,
            "anyuid",
            ev["vllm_common_service_account"],
            ev["vllm_common_namespace"],
            ev["control_dry_run"],
        )
        add_scc_to_service_account(
            api,
            "privileged",
            ev["vllm_common_service_account"],
            ev["vllm_common_namespace"],
            ev["control_dry_run"],
        )

    announce(
        f"🚚 Creating configmap with contents of all files under workload/preprocesses..."
    )
    config_map_name = "llm-d-benchmark-preprocesses"
    config_map_data = {}
    preprocess_dir = Path(ev["main_dir"]) / "setup" / "preprocess"

    try:
        file_paths = sorted([p for p in preprocess_dir.rglob("*") if p.is_file()])
        # this loop reads every file and adds its content to the dictionary
        for path in file_paths:
            config_map_data[path.name] = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        announce(
            f"Warning: Directory not found at {preprocess_dir}. Creating empty ConfigMap."
        )

    cm_obj = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": config_map_name, "namespace": ev["harness_namespace"]},
        "data": config_map_data,
    }

    kubectl_apply(api=api, manifest_data=cm_obj, dry_run=ev["control_dry_run"])

    announce(f'✅ Namespace "{ev["harness_namespace"]}" prepared successfully.')
    return 0


if __name__ == "__main__":
    sys.exit(main())