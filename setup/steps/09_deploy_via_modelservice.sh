#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then
  extract_environment

  # make sure llm-d-modelservice helm repo is available
  llmdbench_execute_cmd "$LLMDBENCH_CONTROL_HCMD repo add ${LLMDBENCH_VLLM_MODELSERVICE_CHART} ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY_URL} --force-update" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0
  llmdbench_execute_cmd "$LLMDBENCH_CONTROL_HCMD repo update" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0

  # deploy models
  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do

    llmdbench_execute_cmd "mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/helm/${LLMDBENCH_VLLM_DEPLOYER_RELEASE}/ms-${LLMDBENCH_VLLM_DEPLOYER_RELEASE}" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    cat << EOF > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/helm/${LLMDBENCH_VLLM_DEPLOYER_RELEASE}/ms-${LLMDBENCH_VLLM_DEPLOYER_RELEASE}/values.yaml
multinode: false

modelArtifacts:
  uri: "pvc://model-pvc/models/$(model_attribute $model model)"
  size: $LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE
  authSecretName: "llm-d-hf-token"

routing:
  modelName: $(model_attribute $model model)
  servicePort: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: infra-${LLMDBENCH_VLLM_DEPLOYER_RELEASE}-inference-gateway

  inferenceModel:
    create: false

  inferencePool:
    create: false
    name: gaie-${LLMDBENCH_VLLM_DEPLOYER_RELEASE}

  httpRoute:
    create: $(echo $LLMDBENCH_VLLM_DEPLOYER_ROUTE | $LLMDBENCH_CONTROL_SCMD -e 's/^0/false/' -e 's/1/true/')

  epp:
    create: false

decode:
  create: $(echo $LLMDBENCH_VLLM_DEPLOYER_DECODE_REPLICAS | $LLMDBENCH_CONTROL_SCMD -e 's/^0/false/' -e 's/[1-9].*/true/')
  replicas: ${LLMDBENCH_VLLM_DEPLOYER_DECODE_REPLICAS}
  acceleratorTypes:
      labelKey: $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 1)
      labelValues:
        - $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 2)
  containers:
  - name: "vllm"
    image: "$(get_image ${LLMDBENCH_LLMD_IMAGE_REGISTRY} ${LLMDBENCH_LLMD_IMAGE_REPO} ${LLMDBENCH_LLMD_IMAGE_NAME} ${LLMDBENCH_LLMD_IMAGE_TAG} 1)"
    modelCommand: vllmServe
    args:
      $(render_string ${LLMDBENCH_VLLM_DEPLOYER_DECODE_EXTRA_ARGS} $model)
    env:
      - name: VLLM_NIXL_SIDE_CHANNEL_HOST
        valueFrom:
          fieldRef:
            fieldPath: status.podIP
    resources:
      limits:
        memory: $LLMDBENCH_VLLM_DEPLOYER_DECODE_CPU_MEM
        cpu: "$LLMDBENCH_VLLM_DEPLOYER_DECODE_CPU_NR"
        $(echo "$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE: \"${LLMDBENCH_VLLM_DEPLOYER_DECODE_ACCELERATOR_NR}\"")
        $(echo "$LLMDBENCH_VLLM_DEPLOYER_DECODE_NETWORK_RESOURCE: \"${LLMDBENCH_VLLM_DEPLOYER_DECODE_NETWORK_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
      requests:
        memory: $LLMDBENCH_VLLM_DEPLOYER_DECODE_CPU_MEM
        cpu: "$LLMDBENCH_VLLM_DEPLOYER_DECODE_CPU_NR"
        $(echo "$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE: \"${LLMDBENCH_VLLM_DEPLOYER_DECODE_ACCELERATOR_NR}\"")
        $(echo "$LLMDBENCH_VLLM_DEPLOYER_DECODE_NETWORK_RESOURCE: \"${LLMDBENCH_VLLM_DEPLOYER_DECODE_NETWORK_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
    mountModelVolume: true
    volumeMounts:
    - name: metrics-volume
      mountPath: /.config
    - name: cache-volume
      mountPath: ${LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT}
    - name: shm
      mountPath: /dev/shm
    - name: torch-compile-cache
      mountPath: /.cache
  volumes:
  - name: metrics-volume
    emptyDir: {}
  - name: cache-volume
    persistentVolumeClaim:
      claimName: ${LLMDBENCH_VLLM_COMMON_PVC_NAME}
  - name: shm
    emptyDir:
      medium: Memory
      sizeLimit: "16Gi"
  - name: torch-compile-cache
    emptyDir: {}

prefill:
  create: $(echo $LLMDBENCH_VLLM_DEPLOYER_PREFILL_REPLICAS | $LLMDBENCH_CONTROL_SCMD -e 's/^0/false/' -e 's/[1-9].*/true/')
  replicas: ${LLMDBENCH_VLLM_DEPLOYER_PREFILL_REPLICAS}
  acceleratorTypes:
      labelKey: $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 1)
      labelValues:
        - $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 2)
  containers:
  - name: "vllm"
    image: "$(get_image ${LLMDBENCH_LLMD_IMAGE_REGISTRY} ${LLMDBENCH_LLMD_IMAGE_REPO} ${LLMDBENCH_LLMD_IMAGE_NAME} ${LLMDBENCH_LLMD_IMAGE_TAG} 1)"
    modelCommand: vllmServe
    args:
      $(render_string ${LLMDBENCH_VLLM_DEPLOYER_PREFILL_EXTRA_ARGS} $model)
    env:
      - name: VLLM_IS_PREFILL # TODO(rob): remove once we bump vllm version
        value: "1"
      - name: VLLM_NIXL_SIDE_CHANNEL_HOST
        valueFrom:
          fieldRef:
            fieldPath: status.podIP
      $(add_additional_env_to_yaml)
    resources:
      limits:
        memory: $LLMDBENCH_VLLM_DEPLOYER_PREFILL_CPU_MEM
        cpu: "$LLMDBENCH_VLLM_DEPLOYER_PREFILL_CPU_NR"
        $(echo "$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE: \"${LLMDBENCH_VLLM_DEPLOYER_PREFILL_ACCELERATOR_NR}\"")
        $(echo "$LLMDBENCH_VLLM_DEPLOYER_PREFILL_NETWORK_RESOURCE: \"${LLMDBENCH_VLLM_DEPLOYER_PREFILL_NETWORK_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
      requests:
        memory: $LLMDBENCH_VLLM_DEPLOYER_PREFILL_CPU_MEM
        cpu: "$LLMDBENCH_VLLM_DEPLOYER_PREFILL_CPU_NR"
        $(echo "$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE: \"${LLMDBENCH_VLLM_DEPLOYER_PREFILL_ACCELERATOR_NR}\"")
        $(echo "$LLMDBENCH_VLLM_DEPLOYER_PREFILL_NETWORK_RESOURCE: \"${LLMDBENCH_VLLM_DEPLOYER_PREFILL_NETWORK_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
    mountModelVolume: true
    volumeMounts:
    - name: metrics-volume
      mountPath: /.config
    - name: cache-volume
      mountPath: ${LLMDBENCH_VLLM_STANDALONE_PVC_MOUNTPOINT}
    - name: shm
      mountPath: /dev/shm
    - name: torch-compile-cache
      mountPath: /.cache
  volumes:
  - name: metrics-volume
    emptyDir: {}
  - name: cache-volume
    persistentVolumeClaim:
      claimName: ${LLMDBENCH_VLLM_COMMON_PVC_NAME}
  - name: shm
    emptyDir:
      medium: Memory
      sizeLimit: "16Gi"
  - name: torch-compile-cache
    emptyDir: {}
EOF

    sanitized_model_name=$(model_attribute $model as_label)
    helm_opts="--version ${LLMDBENCH_VLLM_MODELSERVICE_CHART_VERSION} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} --values ${LLMDBENCH_VLLM_MODELSERVICE_VALUES_FILE} --set routing.servicePort=${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT} --set fullnameOverride=${sanitized_model_name} ${LLMDBENCH_VLLM_MODELSERVICE_ADDITIONAL_SETS}"
    announce "🚀 Calling helm upgrade --install with options \"${helm_opts}\"..."
    llmdbench_execute_cmd "export HF_TOKEN=$LLMDBENCH_HF_TOKEN; $LLMDBENCH_CONTROL_HCMD upgrade --install $sanitized_model_name ${LLMDBENCH_VLLM_MODELSERVICE_HELM_REPOSITORY}/${LLMDBENCH_VLLM_MODELSERVICE_CHART} $helm_opts" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 0
    announce "✅ helm upgrade completed successfully"

    announce "⏳ waiting for (decode) pods serving model ${model} to be created..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=create pod  -l llm-d.ai/model=$sanitized_model_name,llm-d.ai/role=decode" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "✅ (decode) pods serving model ${model} created"

    announce "⏳ waiting for (prefill) pods serving model ${model} to be created..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=create pod  -l llm-d.ai/model=$sanitized_model_name,llm-d.ai/role=prefill" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "✅ (prefill) pods serving model ${model} created"

    announce "⏳ Waiting for (decode) pods serving model ${model} to be in \"Running\" state (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=jsonpath='{.status.phase}'=Running pod  -l llm-d.ai/model=$sanitized_model_name,llm-d.ai/role=decode" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "🚀 (decode) pods serving model ${model} running"

    announce "⏳ Waiting for (prefill) pods serving model ${model} to be in \"Running\" state (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=jsonpath='{.status.phase}'=Running pod  -l llm-d.ai/model=$sanitized_model_name,llm-d.ai/role=prefill" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "🚀 (prefill) pods serving model ${model} running"

    announce "⏳ Waiting for (decode) pods serving ${model} to be Ready (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=condition=Ready=True pod -l llm-d.ai/model=$sanitized_model_name,llm-d.ai/role=decode" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "🚀 (decode) pods serving model ${model} ready"

    announce "⏳ Waiting for (prefill) pods serving ${model} to be Ready (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=condition=Ready=True pod -l llm-d.ai/model=$sanitized_model_name,llm-d.ai/role=prefill" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "🚀 (prefill) pods serving model ${model} ready"

    announce "✅ modelservice completed model deployment"

  done # for model in ...

else
  announce "⏭️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi
