#!/bin/bash
cat << EOF | oc apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: vllm-d-benchmark-secret
type: ibm/ibmc-s3fs
data:
  access-key: $(echo -n "${ACCESS_KEY_ID}" | base64)
  secret-key: $(echo -n "${SECRET_KEY}" | base64)
EOF
