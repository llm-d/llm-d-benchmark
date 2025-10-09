#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

check_storage_class
if [[ $? -ne 0 ]]
then
  announce "❌ Failed to check storage class"
  return 1
fi

if [[ $LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY -eq 0 ]]; then
  announce "⏭️ Environment variable \"LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY\" is set to 0, skipping local setup of harness"
else
  announce "🛠️  Cloning and setting up harness locally..."
  pushd ${LLMDBENCH_HARNESS_DIR} &>/dev/null
  if [[ ! -d ${LLMDBENCH_HARNESS_NAME} ]]; then
    llmdbench_execute_cmd "cd ${LLMDBENCH_HARNESS_DIR}; git clone \"$(resolve_harness_git_repo $LLMDBENCH_HARNESS_NAME)\" -b \"${LLMDBENCH_HARNESS_GIT_BRANCH}\"" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  else
    pushd ${LLMDBENCH_HARNESS_NAME} &>/dev/null
    llmdbench_execute_cmd "cd ${LLMDBENCH_HARNESS_DIR}/${LLMDBENCH_HARNESS_NAME}; git checkout ${LLMDBENCH_HARNESS_GIT_BRANCH}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    popd &>/dev/null
  fi
  pushd ${LLMDBENCH_HARNESS_NAME} &>/dev/null
  is_ce=$(conda env list | grep $LLMDBENCH_HARNESS_CONDA_ENV_NAME || true)
  is_ce=$(echo "$is_ce" | awk '{ print $1 }')
  if [[ ! -z $is_ce ]]; then
    announce "⏭️  Conda environment \"${LLMDBENCH_HARNESS_CONDA_ENV_NAME}\" already set. Skipping install."
  else
    conda create -y -n "$LLMDBENCH_HARNESS_CONDA_ENV_NAME" python=3.11
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate "$LLMDBENCH_HARNESS_CONDA_ENV_NAME"
    pip install -r requirements.txt
    pip install -r config_explorer/requirements.txt

    ${LLMDBENCH_CONTROL_CCMD} build -t ${LLMDBENCH_HARNESS_NAME} .
    mkdir -p requests && chmod o+w requests
    cp .env.example .env
  fi
  popd &>/dev/null
  popd &>/dev/null
  announce "✅ harness setup locally."
fi

check_storage_class
if [[ $? -ne 0 ]]
then
  announce "❌ Failed to check storage class"
  if [[ "${BASH_SOURCE[0]}" == "${0}" ]]
  then
      exit 1
  else
      return 1
  fi
fi

announce "🔄 Creating namespace (${LLMDBENCH_HARNESS_NAMESPACE}), service account (${LLMDBENCH_HARNESS_SERVICE_ACCOUNT}) and rbac for harness..."
create_namespace "${LLMDBENCH_CONTROL_KCMD}" "${LLMDBENCH_HARNESS_NAMESPACE}"
cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_namespace_sa_rbac_secret.yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: ${LLMDBENCH_HARNESS_NAMESPACE}
  labels:
    kubernetes.io/metadata.name: ${LLMDBENCH_HARNESS_NAMESPACE}
$(${LLMDBENCH_CONTROL_KCMD} get namespace/${LLMDBENCH_VLLM_COMMON_NAMESPACE} -o yaml | yq .metadata.labels | grep -Ev "metadata.name" | sed 's|^|    |g')
spec:
  finalizers:
  - kubernetes
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${LLMDBENCH_HARNESS_SERVICE_ACCOUNT}
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: ${LLMDBENCH_HARNESS_NAME}-job-creator
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
rules:
- apiGroups: ["batch"]
  resources: ["jobs"]
  verbs: ["create", "get", "list", "watch", "delete", "patch", "update"]
- apiGroups: [""]
  resources: ["serviceaccounts"]
  verbs: ["get"]
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["pods/log"]
  verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${LLMDBENCH_HARNESS_NAME}-job-creator-binding
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
subjects:
- kind: ServiceAccount
  name: ${LLMDBENCH_HARNESS_NAME}-runner
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
roleRef:
  kind: Role
  name: ${LLMDBENCH_HARNESS_NAME}-job-creator
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${LLMDBENCH_HARNESS_NAME}-restricted-scc
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
subjects:
- kind: ServiceAccount
  name: ${LLMDBENCH_HARNESS_NAME}-runner
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
roleRef:
  kind: ClusterRole
  name: system:openshift:scc:restricted
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: v1
kind: Secret
metadata:
  name: ${LLMDBENCH_VLLM_COMMON_HF_TOKEN_NAME}
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
data:
  HF_TOKEN: "$(echo ${LLMDBENCH_HF_TOKEN} | base64)"
EOF

llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_namespace_sa_rbac_secret.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
if [[ $? -ne 0 ]]; then
  return 1
fi
announce "✅ Namespace (${LLMDBENCH_HARNESS_NAMESPACE}), service account (${LLMDBENCH_HARNESS_SERVICE_ACCOUNT}) and rbac for harness created"

for vol in ${LLMDBENCH_HARNESS_PVC_NAME}; do

  is_pvc=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pvc --ignore-not-found | grep ${LLMDBENCH_HARNESS_PVC_NAME} || true)
  if [[ -z ${is_pvc} ]]; then
    announce "🔄 Creating PVC \"${vol}\" for harness data storage..."
    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${vol}.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${vol}
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: ${LLMDBENCH_HARNESS_PVC_SIZE}
  storageClassName: ${LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS}
EOF
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_pvc_${vol}.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    if [[ $? -ne 0 ]]; then
      return 1
    fi
  fi
  announce "✅ PVC \"${vol}\" for harness data storage created"

  is_pod=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_HARNESS_NAMESPACE} get pod --ignore-not-found | grep access-to-harness-data-${vol} || true)
  if [[ -z ${is_pod} ]]; then
    announce "🔄 Starting pod \"access-to-harness-data-${vol}\" to provide access to PVC ${vol} (harness data storage)..."
    cat <<EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_a_pod_access_to_harness_data.yaml
apiVersion: v1
kind: Pod
metadata:
  name: access-to-harness-data-${vol}
  labels:
    app: llm-d-benchmark-harness
    role: llm-d-benchmark-data-access
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
spec:
  containers:
  - name: rsync
    image: $(get_image ${LLMDBENCH_IMAGE_REGISTRY} ${LLMDBENCH_IMAGE_REPO} ${LLMDBENCH_IMAGE_NAME} ${LLMDBENCH_IMAGE_TAG})
    imagePullPolicy: Always
    securityContext:
      runAsUser: 0
    command: ["rsync", "--daemon", "--no-detach", "--port=20873", "--log-file=/dev/stdout"]
    volumeMounts:
    - name: requests
      mountPath: /requests
#    - name: cache-volume
#      mountPath: ${LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT}
  volumes:
  - name: requests
    persistentVolumeClaim:
      claimName: $LLMDBENCH_HARNESS_PVC_NAME
#  - name: cache-volume
#    persistentVolumeClaim:
#      claimName: ${LLMDBENCH_VLLM_COMMON_PVC_NAME}
EOF

    if [[ $LLMDBENCH_VLLM_MODELSERVICE_URI_PROTOCOL == "pvc" || ${LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE} -eq 1 ]]; then
      $LLMDBENCH_CONTROL_SCMD -i "s^\^#^^g" $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_a_pod_access_to_harness_data.yaml
    fi
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_a_pod_access_to_harness_data.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    if [[ $? -ne 0 ]]; then
      return 1
    fi
  fi
    cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_b_service_access_to_harness_data.yaml
apiVersion: v1
kind: Service
metadata:
  name: llm-d-benchmark-harness
  namespace: ${LLMDBENCH_HARNESS_NAMESPACE}
spec:
  ports:
  - name: rsync
    protocol: TCP
    port: 20873
    targetPort: 20873
  selector:
    app: llm-d-benchmark-harness
  type: ClusterIP
EOF

  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/yamls/${LLMDBENCH_CURRENT_STEP}_b_service_access_to_harness_data.yaml" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
  if [[ $? -ne 0 ]]; then
    return 1
  fi

  announce "✅ Pod \"access-to-harness-data-${vol}\" started, providing access to PVC ${vol}"
done

if [[ $LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT -eq 1 && $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
adm \
policy \
add-scc-to-user \
anyuid \
-z ${LLMDBENCH_HARNESS_SERVICE_ACCOUNT} \
-n $LLMDBENCH_HARNESS_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

  llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} \
adm \
policy \
add-scc-to-user \
privileged \
-z ${LLMDBENCH_HARNESS_SERVICE_ACCOUNT} \
-n $LLMDBENCH_HARNESS_NAMESPACE" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
fi
