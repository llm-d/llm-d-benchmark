# llm-d-benchmark

```
export OPENSHIFT_HOST="https://api.fmaas-vllm-d.fmaas.res.ibm.com:6443"
export OPENSHIFT_TOKEN="sha256~sVYh-xxx"
export OPENSHIFT_NAMESPACE="e2e-solution"
export HF_TOKEN="hf_xxx"
export QUAY_USER=""
export QUAY_PASSWORD=""

benchmarking vllm-d

## install conda
```
brew install anaconda
echo 'export PATH="/opt/homebrew/anaconda3/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

conda
```

Start with fmperf fork found at https://github.com/wangchen615/fmperf.git

## install fmperf fork
```
git clone https://github.com/wangchen615/fmperf.git -b dev-lmbenchmark
cd fmperf
conda create -y -n fmperf-env python=3.11
conda activate fmperf-env
pip install -r requirements.txt
pip install -e .

docker build -t fmperf .
mkdir requests
chmod o+w requests

cp .env.example .env

```

## prep namespace

### give perms to default SA to runasroot
```
oc adm policy add-scc-to-user anyuid -z default
oc adm policy add-scc-to-user privileged -z default
```

### create secret for HF_TOKEN - must have access to llama-8b and llama-70b
```
cat <<EOF | oc apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: hf-token
  namespace: ${OPENSHIFT_NAMESPACE}
type: Opaque
stringData:
  token: ${HF_TOKEN}
EOF
```

```
oc create secret docker-registry quay-secret \
  --docker-server=quay.io \
  --docker-username=${QUAY_USER} \
  --docker-password=${QUAY_PASSWORD} \
  --docker-email=your@email.address \
  -n ${OPENSHIFT_NAMESPACE}
```

### add registry secret to default SA
```
oc patch serviceaccount default \
  -n ${OPENSHIFT_NAMESPACE} \
  --type=merge \
  -p '{"imagePullSecrets":[{"name":"quay-secret"}]}'
  ```


### create PVC for llama-8b model cache
```
cat <<EOF | oc apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: llama-8b-cache
  namespace: ${OPENSHIFT_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 300Gi
  storageClassName: ocs-storagecluster-cephfs
EOF
```

### create PVC for llama-70b model cache
```
cat <<EOF | oc apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: llama-70b-cache
  namespace: ${OPENSHIFT_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 300Gi
  storageClassName: ocs-storagecluster-cephfs
EOF
```

## deploy a baseline model (non-llm version)
```
https://github.com/neuralmagic/llm-d-benchmark/tree/dev/yamls/exp-0
```

## deploy llm
```
```


## run the experiment
```
python3 examples/example_llm-d-lmbenchmark-openshift.py 
```

#### Logs are shown in the pod starting with lmbenchmark-    and you can check the job status as well. Once the jobs is started, the fmperf script termination will not impact the job to do benchmarking.
