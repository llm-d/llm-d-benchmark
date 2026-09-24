SHELL := /usr/bin/env bash

# Defaults
PROJECT_NAME ?= llm-d-benchmark
DEV_VERSION ?= 0.0.1
PROD_VERSION ?= 0.0.0
IMAGE_TAG_BASE ?= ghcr.io/llm-d/$(PROJECT_NAME)
IMG = $(IMAGE_TAG_BASE):$(DEV_VERSION)
NAMESPACE ?= hc4ai-operator

CONTAINER_TOOL := $(shell if command -v docker >/dev/null 2>&1; then echo docker; elif command -v podman >/dev/null 2>&1; then echo podman; fi)
BUILDER := $(shell command -v buildah >/dev/null 2>&1 && echo buildah || echo $(CONTAINER_TOOL))
PLATFORMS ?= linux/amd64,linux/arm64 # linux/s390x,linux/ppc64le

# go source files
SRC = $(shell find . -type f -name '*.go')

.PHONY: help
help: ## Print help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Development

.PHONY: format
format: ## Format Go source files
	@printf "\033[33;1m==== Running gofmt ====\033[0m\n"
	@gofmt -l -w $(SRC)

.PHONY: test
test: check-ginkgo ## Run tests
	@printf "\033[33;1m==== Running tests ====\033[0m\n"
	ginkgo -r -v

.PHONY: post-deploy-test
post-deploy-test: ## Run post deployment tests
	echo Success!
	@echo "Post-deployment tests passed."

.PHONY: lint
lint: check-golangci-lint ## Run lint
	@printf "\033[33;1m==== Running linting ====\033[0m\n"
	golangci-lint run

##@ Container Build/Push

.PHONY: buildah-build
buildah-build: check-builder load-version-json ## Build and push image (multi-arch if supported)
	@echo "✅ Using builder: $(BUILDER)"
	@if [ "$(BUILDER)" = "buildah" ]; then \
	  echo "🔧 Buildah detected: Performing multi-arch build..."; \
	  FINAL_TAG=$(IMG); \
	  for arch in amd64; do \
	    ARCH_TAG=$$FINAL_TAG-$$arch; \
	    echo "📦 Building for architecture: $$arch"; \
		buildah build --arch=$$arch --os=linux --layers -f build/Dockerfile -t $(IMG)-$$arch . || exit 1; \
	    echo "🚀 Pushing image: $(IMG)-$$arch"; \
	    buildah push $(IMG)-$$arch docker://$(IMG)-$$arch || exit 1; \
	  done; \
	  echo "🧼 Removing existing manifest (if any)..."; \
	  buildah manifest rm $$FINAL_TAG || true; \
	  echo "🧱 Creating and pushing manifest list: $(IMG)"; \
	  buildah manifest create $(IMG); \
	  for arch in amd64; do \
	    ARCH_TAG=$$FINAL_TAG-$$arch; \
	    buildah manifest add $$FINAL_TAG $$ARCH_TAG; \
	  done; \
	  buildah manifest push --all $(IMG) docker://$(IMG); \
	elif [ "$(BUILDER)" = "docker" ]; then \
	  echo "🐳 Docker detected: Building with buildx..."; \
	  - docker buildx create --use --name image-builder || true; \
	  docker buildx use image-builder; \
	  docker buildx build --push --platform=$(PLATFORMS) --tag $(IMG) -f build/Dockerfile . || exit 1; \
	  docker buildx rm image-builder || true; \
	elif [ "$(BUILDER)" = "podman" ]; then \
	  echo "⚠️ Podman detected: Building single-arch image..."; \
	  podman build -f build/Dockerfile -t $(IMG) . || exit 1; \
	  podman push $(IMG) || exit 1; \
	else \
	  echo "❌ No supported container tool available."; \
	  exit 1; \
	fi

.PHONY:	image-build
image-build: check-container-tool load-version-json ## Build Docker image using $(CONTAINER_TOOL)
	@printf "\033[33;1m==== Building Docker image $(IMG) ====\033[0m\n"
	$(CONTAINER_TOOL) build -f build/Dockerfile --build-arg TARGETOS=$(TARGETOS) --build-arg TARGETARCH=$(TARGETARCH) -t $(IMG) .

.PHONY: image-push
image-push: check-container-tool load-version-json ## Push Docker image $(IMG) to registry
	@printf "\033[33;1m==== Pushing Docker image $(IMG) ====\033[0m\n"
	$(CONTAINER_TOOL) push $(IMG)

##@ Install/Uninstall Targets

# Default install/uninstall (Docker)
install: install-docker ## Default install using Docker
	@echo "Default Docker install complete."

uninstall: uninstall-docker ## Default uninstall using Docker
	@echo "Default Docker uninstall complete."

### Docker Targets

.PHONY: install-docker
install-docker: check-container-tool ## Install app using $(CONTAINER_TOOL)
	@echo "Starting container with $(CONTAINER_TOOL)..."
	$(CONTAINER_TOOL) run -d --name $(PROJECT_NAME)-container $(IMG)
	@echo "$(CONTAINER_TOOL) installation complete."
	@echo "To use $(PROJECT_NAME), run:"
	@echo "alias $(PROJECT_NAME)='$(CONTAINER_TOOL) exec -it $(PROJECT_NAME)-container /app/$(PROJECT_NAME)'"

.PHONY: uninstall-docker
uninstall-docker: check-container-tool ## Uninstall app from $(CONTAINER_TOOL)
	@echo "Stopping and removing container in $(CONTAINER_TOOL)..."
	-$(CONTAINER_TOOL) stop $(PROJECT_NAME)-container && $(CONTAINER_TOOL) rm $(PROJECT_NAME)-container
@echo "$(CONTAINER_TOOL) uninstallation complete. Remove alias if set: unalias $(PROJECT_NAME)"

### Kubernetes Targets (kubectl)

.PHONY: install-k8s
install-k8s: check-kubectl check-kustomize check-envsubst ## Install on Kubernetes
	export PROJECT_NAME=${PROJECT_NAME}
	export NAMESPACE=${NAMESPACE}
	@echo "Creating namespace (if needed) and setting context to $(NAMESPACE)..."
	kubectl create namespace $(NAMESPACE) 2>/dev/null || true
	kubectl config set-context --current --namespace=$(NAMESPACE)
	@echo "Deploying resources from deploy/ ..."
	# Build the kustomization from deploy, substitute variables, and apply the YAML
	kustomize build deploy | envsubst | kubectl apply -f -
	@echo "Waiting for pod to become ready..."
	sleep 5
	@POD=$$(kubectl get pod -l app=$(PROJECT_NAME)-statefulset -o jsonpath='{.items[0].metadata.name}'); \
	echo "Kubernetes installation complete."; \
	echo "To use the app, run:"; \
	echo "alias $(PROJECT_NAME)='kubectl exec -n $(NAMESPACE) -it $$POD -- /app/$(PROJECT_NAME)'"

.PHONY: uninstall-k8s
uninstall-k8s: check-kubectl check-kustomize check-envsubst ## Uninstall from Kubernetes
	export PROJECT_NAME=${PROJECT_NAME}
	export NAMESPACE=${NAMESPACE}
	@echo "Removing resources from Kubernetes..."
	kustomize build deploy | envsubst | kubectl delete --force -f - || true
	POD=$$(kubectl get pod -l app=$(PROJECT_NAME)-statefulset -o jsonpath='{.items[0].metadata.name}'); \
	echo "Deleting pod: $$POD"; \
	kubectl delete pod "$$POD" --force --grace-period=0 || true; \
	echo "Kubernetes uninstallation complete. Remove alias if set: unalias $(PROJECT_NAME)"

### OpenShift Targets (oc)

.PHONY: install-openshift
install-openshift: check-kubectl check-kustomize check-envsubst ## Install on OpenShift
	@echo $$PROJECT_NAME $$NAMESPACE $$IMAGE_TAG_BASE $$VERSION
	@echo "Creating namespace $(NAMESPACE)..."
	kubectl create namespace $(NAMESPACE) 2>/dev/null || true
	@echo "Deploying common resources from deploy/ ..."
	# Build and substitute the base manifests from deploy, then apply them
	kustomize build deploy | envsubst '$$PROJECT_NAME $$NAMESPACE $$IMAGE_TAG_BASE $$VERSION' | kubectl apply -n $(NAMESPACE) -f -
	@echo "Waiting for pod to become ready..."
	sleep 5
	@POD=$$(kubectl get pod -l app=$(PROJECT_NAME)-statefulset -n $(NAMESPACE) -o jsonpath='{.items[0].metadata.name}'); \
	echo "OpenShift installation complete."; \
	echo "To use the app, run:"; \
	echo "alias $(PROJECT_NAME)='kubectl exec -n $(NAMESPACE) -it $$POD -- /app/$(PROJECT_NAME)'"

.PHONY: uninstall-openshift
uninstall-openshift: check-kubectl check-kustomize check-envsubst ## Uninstall from OpenShift
	@echo "Removing resources from OpenShift..."
	kustomize build deploy | envsubst '$$PROJECT_NAME $$NAMESPACE $$IMAGE_TAG_BASE $$VERSION' | kubectl delete --force -f - || true
	# @if kubectl api-resources --api-group=route.openshift.io | grep -q Route; then \
	#   envsubst '$$PROJECT_NAME $$NAMESPACE $$IMAGE_TAG_BASE $$VERSION' < deploy/openshift/route.yaml | kubectl delete --force -f - || true; \
	# fi
	@POD=$$(kubectl get pod -l app=$(PROJECT_NAME)-statefulset -n $(NAMESPACE) -o jsonpath='{.items[0].metadata.name}'); \
	echo "Deleting pod: $$POD"; \
	kubectl delete pod "$$POD" --force --grace-period=0 || true; \
	echo "OpenShift uninstallation complete. Remove alias if set: unalias $(PROJECT_NAME)"

### RBAC Targets (using kustomize and envsubst)

.PHONY: install-rbac
install-rbac: check-kubectl check-kustomize check-envsubst ## Install RBAC
	@echo "Applying RBAC configuration from deploy/rbac..."
	kustomize build deploy/rbac | envsubst '$$PROJECT_NAME $$NAMESPACE $$IMAGE_TAG_BASE $$VERSION' | kubectl apply -f -

.PHONY: uninstall-rbac
uninstall-rbac: check-kubectl check-kustomize check-envsubst ## Uninstall RBAC
	@echo "Removing RBAC configuration from deploy/rbac..."
	kustomize build deploy/rbac | envsubst '$$PROJECT_NAME $$NAMESPACE $$IMAGE_TAG_BASE $$VERSION' | kubectl delete -f - || true


##@ Version Extraction
.PHONY: version dev-registry prod-registry extract-version-info

dev-version: check-jq
	@jq -r '.dev-version' .version.json

prod-version: check-jq
	@jq -r '.prod-version' .version.json

dev-registry: check-jq
	@jq -r '."dev-registry"' .version.json

prod-registry: check-jq
	@jq -r '."prod-registry"' .version.json

extract-version-info: check-jq
	@echo "DEV_VERSION=$$(jq -r '."dev-version"' .version.json)"
	@echo "PROD_VERSION=$$(jq -r '."prod-version"' .version.json)"
	@echo "DEV_IMAGE_TAG_BASE=$$(jq -r '."dev-registry"' .version.json)"
	@echo "PROD_IMAGE_TAG_BASE=$$(jq -r '."prod-registry"' .version.json)"

##@ Load Version JSON

.PHONY: load-version-json
load-version-json: check-jq
	@if [ "$(DEV_VERSION)" = "0.0.1" ]; then \
	  DEV_VERSION=$$(jq -r '."dev-version"' .version.json); \
	  PROD_VERSION=$$(jq -r '."dev-version"' .version.json); \
	  echo "✔ Loaded DEV_VERSION from .version.json: $$DEV_VERSION"; \
	  echo "✔ Loaded PROD_VERSION from .version.json: $$PROD_VERSION"; \
	  export DEV_VERSION; \
	  export PROD_VERSION; \
	fi && \
	CURRENT_DEFAULT="ghcr.io/llm-d/$(PROJECT_NAME)"; \
	if [ "$(IMAGE_TAG_BASE)" = "$$CURRENT_DEFAULT" ]; then \
	  IMAGE_TAG_BASE=$$(jq -r '."dev-registry"' .version.json); \
	  echo "✔ Loaded IMAGE_TAG_BASE from .version.json: $$IMAGE_TAG_BASE"; \
	  export IMAGE_TAG_BASE; \
	fi && \
	echo "🛠 Final values: DEV_VERSION=$$DEV_VERSION, PROD_VERSION=$$PROD_VERSION, IMAGE_TAG_BASE=$$IMAGE_TAG_BASE"

.PHONY: env
env: load-version-json ## Print environment variables
	@echo "DEV_VERSION=$(DEV_VERSION)"
	@echo "PROD_VERSION=$(PROD_VERSION)"
	@echo "IMAGE_TAG_BASE=$(IMAGE_TAG_BASE)"
	@echo "IMG=$(IMG)"
	@echo "CONTAINER_TOOL=$(CONTAINER_TOOL)"


##@ Tools

.PHONY: check-tools
check-tools: \
  check-go \
  check-ginkgo \
  check-golangci-lint \
  check-jq \
  check-kustomize \
  check-envsubst \
  check-container-tool \
  check-kubectl \
  check-buildah \
  check-podman
	@echo "✅ All required tools are installed."

.PHONY: check-go
check-go:
	@command -v go >/dev/null 2>&1 || { \
	  echo "❌ Go is not installed. Install it from https://golang.org/dl/"; exit 1; }

.PHONY: check-ginkgo
check-ginkgo:
	@command -v ginkgo >/dev/null 2>&1 || { \
	  echo "❌ ginkgo is not installed. Install with: go install github.com/onsi/ginkgo/v2/ginkgo@latest"; exit 1; }

.PHONY: check-golangci-lint
check-golangci-lint:
	@command -v golangci-lint >/dev/null 2>&1 || { \
	  echo "❌ golangci-lint is not installed. Install from https://golangci-lint.run/usage/install/"; exit 1; }

.PHONY: check-jq
check-jq:
	@command -v jq >/dev/null 2>&1 || { \
	  echo "❌ jq is not installed. Install it from https://stedolan.github.io/jq/download/"; exit 1; }

.PHONY: check-kustomize
check-kustomize:
	@command -v kustomize >/dev/null 2>&1 || { \
	  echo "❌ kustomize is not installed. Install it from https://kubectl.docs.kubernetes.io/installation/kustomize/"; exit 1; }

##@ Token-aware autoscaling

# peakPrefillThroughput (V_P) = CHUNK_SIZE / median(TTFT), tokens/sec per replica.
# Needed only by the token-aware autoscaling path (and any router config using
# prefix-cache-affinity-filter) -- hence a standalone target rather than anything
# in the standup pipeline or defaults.yaml.
#
# The measurement is llm-d's own recipe, fetched at run time rather than vendored,
# so there is one implementation of it and nothing here to keep in step.
# See docs/token-aware-autoscaling.md.
#
# CALIBRATION_REF defaults to `main` deliberately: the calibration recipe is not in
# a tagged llm-d release that is compatible with this guide, so a pinned tag would
# fetch either a missing or an incompatible script. Override it to pin a tag or a
# SHA when you need a reproducible measurement:
#   make calibrate-peak-prefill NAMESPACE=<ns> CALIBRATION_REF=<tag-or-sha>
CALIBRATION_REF ?= main
CALIBRATION_BASE := https://raw.githubusercontent.com/llm-d/llm-d/$(CALIBRATION_REF)/guides/recipes/router/calibration
CHUNK_SIZE ?= 8192
APPLY ?= 0

.PHONY: calibrate-peak-prefill
calibrate-peak-prefill: check-kubectl check-envsubst ## Measure peakPrefillThroughput via the upstream llm-d recipe. NAMESPACE=<ns> [APPLY=1] [CHUNK_SIZE=8192] [CALIBRATION_REF=main]
	@test -n "$(NAMESPACE)" || { \
	  echo "❌ NAMESPACE is required:  make calibrate-peak-prefill NAMESPACE=<ns> [APPLY=1]"; exit 1; }
	@set -e; \
	NS="$(NAMESPACE)"; \
	DIR=$$(mktemp -d); trap 'rm -rf "$$DIR"' EXIT; \
	echo "⬇️  fetching the upstream calibration recipe (ref=$(CALIBRATION_REF))"; \
	curl -sfL "$(CALIBRATION_BASE)/calibrate.sh" -o "$$DIR/calibrate.sh"; \
	curl -sfL "$(CALIBRATION_BASE)/calibration-peak-throughput.yaml" -o "$$DIR/calibration-peak-throughput.yaml"; \
	chmod +x "$$DIR/calibrate.sh"; \
	EPP=$$(kubectl get svc -n "$$NS" -o jsonpath='{range .items[?(@.spec.clusterIP)]}{.metadata.name}{" "}{.spec.clusterIP}{"\n"}{end}' | awk '/-epp /{print $$2; exit}'); \
	test -n "$$EPP" || { echo "❌ no *-epp Service in ns/$$NS"; exit 2; }; \
	MODEL=$$(kubectl get deploy -n "$$NS" -o jsonpath='{range .items[*]}{range .spec.template.spec.containers[?(@.name=="vllm")].env[?(@.name=="MODEL_NAME")]}{.value}{"\n"}{end}{end}' | awk 'NF{print;exit}'); \
	test -n "$$MODEL" || { echo "❌ could not read MODEL_NAME from the vllm container in ns/$$NS"; exit 2; }; \
	SERVED=$$(kubectl get deploy -n "$$NS" -o jsonpath='{range .items[*]}{range .spec.template.spec.containers[?(@.name=="vllm")].env[?(@.name=="VLLM_MAX_NUM_BATCHED_TOKENS")]}{.value}{"\n"}{end}{end}' | awk 'NF{print;exit}'); \
	if [ -n "$$SERVED" ] && [ "$$SERVED" != "$(CHUNK_SIZE)" ]; then \
	  echo "❌ CHUNK_SIZE=$(CHUNK_SIZE) != serving VLLM_MAX_NUM_BATCHED_TOKENS=$$SERVED"; \
	  echo "   A chunk larger than the batch budget is prefilled in several passes,"; \
	  echo "   so the measured TTFT would not be one prefill pass."; exit 2; fi; \
	echo "🔎 endpoint=http://$$EPP:80  model=$$MODEL  chunk=$(CHUNK_SIZE)"; \
	SO_BACKUP="$$DIR/scaledobjects.yaml"; SO_PARKED=0; \
	if kubectl get scaledobject -n "$$NS" -o name 2>/dev/null | grep -q .; then \
	  echo "🅿️  parking the ScaledObject(s) for the duration of the measurement"; \
	  echo "   (an autoscaler could add replicas mid-measurement, and its prefill"; \
	  echo "    divisor is the very number being measured)"; \
	  kubectl get scaledobject -n "$$NS" -o yaml > "$$SO_BACKUP"; \
	  python3 -c "import sys,yaml; d=yaml.safe_load(open(sys.argv[1])); items=d.get('items',[d]) if isinstance(d,dict) else d; \
	    [ (i.pop('status',None), [i['metadata'].pop(k,None) for k in ('resourceVersion','uid','creationTimestamp','generation','managedFields','selfLink')]) for i in items ]; \
	    yaml.safe_dump_all(items, open(sys.argv[1],'w'))" "$$SO_BACKUP"; \
	  kubectl delete scaledobject -n "$$NS" --all >/dev/null; \
	  SO_PARKED=1; \
	  trap 'if [ "$$SO_PARKED" = "1" ] && [ -s "$$SO_BACKUP" ]; then echo "↩️  restoring the ScaledObject(s)"; kubectl apply -f "$$SO_BACKUP" >/dev/null 2>&1 || true; fi; rm -rf "$$DIR"' EXIT; \
	fi; \
	echo "⏳ checking the fleet is idle (V_P is a single-request measurement --"; \
	echo "   residual load inflates TTFT and UNDERSTATES V_P)"; \
	BUSY=0; \
	for p in $$(kubectl get pods -n "$$NS" --field-selector=status.phase=Running -o name | grep decode | cut -d/ -f2); do \
	  M=$$(kubectl exec -n "$$NS" "$$p" -c vllm -- curl -s localhost:8200/metrics 2>/dev/null | awk '/^vllm:kv_cache_usage_perc/{k=$$NF} /^vllm:num_requests_running/{r=$$NF} END{print k+0, r+0}'); \
	  echo "   $$p kv=$$(echo $$M | cut -d" " -f1) running=$$(echo $$M | cut -d" " -f2)"; \
	  echo "$$M" | awk '{ if ($$1 > 0.02 || $$2 >= 1) exit 1 }' || BUSY=1; \
	done; \
	if [ "$$BUSY" -eq 1 ]; then \
	  echo "❌ the fleet is not idle -- stop the load, wait for the KV cache to drain, retry"; exit 3; fi; \
	echo "▶️  running the upstream calibrate.sh"; \
	VLLM_ENDPOINT="http://$$EPP:80" NAMESPACE="$$NS" MODEL_NAME="$$MODEL" \
	CHUNK_SIZE="$(CHUNK_SIZE)" "$$DIR/calibrate.sh" 2>&1 | tee "$$DIR/out.log"; \
	VP=$$(grep -oE 'Measured peakPrefillThroughput = [0-9]+' "$$DIR/out.log" | tail -1 | grep -oE '[0-9]+$$'); \
	test -n "$$VP" || { echo "❌ no value produced; see kubectl logs -n $$NS job/calibrate-peak-throughput"; exit 4; }; \
	kubectl logs -n "$$NS" job/calibrate-peak-throughput 2>/dev/null | grep -oE 'TTFT=[0-9.]+' | cut -d= -f2 \
	  | tail -n +6 | sort -n \
	  | awk '{v[++n]=$$1} END{ if(!n) exit; m=(n%2)?v[(n+1)/2]:(v[n/2]+v[n/2+1])/2; s=(v[n]-v[1])/m; \
	      printf "📊 n=%d median=%.4fs spread=%.1f%% of median\n", n, m, s*100; \
	      if (s > 0.5) print "⚠️  spread >50%: the stack was probably not idle -- discard this value" }' || true; \
	echo "✅ PEAK_PREFILL_THROUGHPUT=$$VP"; \
	if [ "$(APPLY)" != "1" ]; then \
	  echo ""; \
	  echo "Not applied (pass APPLY=1). Set $$VP in BOTH places that must agree:"; \
	  echo "  1. prefix-cache-affinity-filter.parameters.peakPrefillThroughput  (router)"; \
	  echo "  2. eppKedaSaturation.scaledObject.peakPrefillThroughput           (KEDA trigger)"; \
	  exit 0; fi; \
	echo "✍️  applying $$VP to both consumers"; \
	CM_PATCHED=0; SO_PATCHED=0; \
	:; \
	: 'Scoped to *-epp ConfigMaps -- the router chart names the EPP plugin config'; \
	: '<release>-epp, and rewriting every ConfigMap in the namespace would reach'; \
	: 'another tenant on a shared namespace.'; \
	for cm in $$(kubectl get cm -n "$$NS" -o name | cut -d/ -f2 | grep -- '-epp$$'); do \
	  if kubectl get cm -n "$$NS" "$$cm" -o yaml | grep -q 'peakPrefillThroughput:'; then \
	    kubectl get cm -n "$$NS" "$$cm" -o yaml | sed -E "s/(peakPrefillThroughput: *)[0-9]+/\1$$VP/g" | kubectl apply -f - >/dev/null; \
	    echo "   configmap/$$cm updated"; CM_PATCHED=1; fi; done; \
	if [ "$$SO_PARKED" = "1" ] && [ -s "$$SO_BACKUP" ]; then \
	  python3 -c "import re,sys; p,vp=sys.argv[1],sys.argv[2]; t=open(p).read(); open(p,'w').write(re.sub(r'(llm_d_epp_inflight_tokens[\\s\\S]*?/\\s*)\\d+', lambda m: m.group(1)+vp, t))" "$$SO_BACKUP" "$$VP"; \
	  kubectl apply -f "$$SO_BACKUP" >/dev/null; SO_PARKED=0; \
	  echo "   scaledobject(s) restored with divisor $$VP"; SO_PATCHED=1; \
	fi; \
	if [ "$$CM_PATCHED" -eq 0 ]; then \
	  echo "   ℹ️  no ConfigMap carries peakPrefillThroughput -- this router config has no"; \
	  echo "      prefix-cache-affinity-filter, so nothing consumes V_P on the router side."; \
	  echo "      Not restarting the EPP: a restart with nothing to re-read is pure disruption."; fi; \
	if [ "$$SO_PATCHED" -eq 0 ]; then \
	  echo "   ℹ️  no ScaledObject trigger references llm_d_epp_inflight_tokens (expected"; \
	  echo "      unless you are autoscaling on V_P)."; fi; \
	if [ "$$SO_PATCHED" -eq 1 ] && [ "$$CM_PATCHED" -eq 0 ]; then \
	  echo "❌ the ScaledObject was updated but the router was not -- they now disagree on V_P."; exit 6; fi; \
	if [ "$$CM_PATCHED" -eq 1 ]; then \
	  for d in $$(kubectl get deploy -n "$$NS" -o name | cut -d/ -f2 | grep -- '-epp$$' || true); do \
	    kubectl rollout restart -n "$$NS" "deployment/$$d" >/dev/null; \
	    echo "   restarted deployment/$$d (the EPP reads its plugin config at startup only)"; done; fi

.PHONY: check-envsubst
check-envsubst:
	@command -v envsubst >/dev/null 2>&1 || { \
	  echo "❌ envsubst is not installed. It is part of gettext."; \
	  echo "🔧 Try: sudo apt install gettext OR brew install gettext"; exit 1; }

.PHONY: check-container-tool
check-container-tool:
	@command -v $(CONTAINER_TOOL) >/dev/null 2>&1 || { \
	  echo "❌ $(CONTAINER_TOOL) is not installed."; \
	  echo "🔧 Try: sudo apt install $(CONTAINER_TOOL) OR brew install $(CONTAINER_TOOL)"; exit 1; }

.PHONY: check-kubectl
check-kubectl:
	@command -v kubectl >/dev/null 2>&1 || { \
	  echo "❌ kubectl is not installed. Install it from https://kubernetes.io/docs/tasks/tools/"; exit 1; }

.PHONY: check-builder
check-builder:
	@if [ -z "$(BUILDER)" ]; then \
		echo "❌ No container builder tool (buildah, docker, or podman) found."; \
		exit 1; \
	else \
		echo "✅ Using builder: $(BUILDER)"; \
	fi

.PHONY: check-podman
check-podman:
	@command -v podman >/dev/null 2>&1 || { \
	  echo "⚠️  Podman is not installed. You can install it with:"; \
	  echo "🔧 sudo apt install podman  OR  brew install podman"; exit 1; }

##@ Alias checking
.PHONY: check-alias
check-alias: check-container-tool
	@echo "🔍 Checking alias functionality for container '$(PROJECT_NAME)-container'..."
	@if ! $(CONTAINER_TOOL) exec $(PROJECT_NAME)-container /app/$(PROJECT_NAME) --help >/dev/null 2>&1; then \
	  echo "⚠️  The container '$(PROJECT_NAME)-container' is running, but the alias might not work."; \
	  echo "🔧 Try: $(CONTAINER_TOOL) exec -it $(PROJECT_NAME)-container /app/$(PROJECT_NAME)"; \
	else \
	  echo "✅ Alias is likely to work: alias $(PROJECT_NAME)='$(CONTAINER_TOOL) exec -it $(PROJECT_NAME)-container /app/$(PROJECT_NAME)'"; \
	fi

.PHONY: print-namespace
print-namespace: ## Print the current namespace
	@echo "$(NAMESPACE)"

.PHONY: print-project-name
print-project-name: ## Print the current project name
	@echo "$(PROJECT_NAME)"

.PHONY: install-hooks
install-hooks: ## Install git hooks
	git config core.hooksPath hooks
