#!/usr/bin/env bash

NS=$1

if [[ -z $NS ]]; then
  NS=default
fi

echo "=== All pods ==="
kubectl get pods -n "$NS" -o wide || true
echo ""
echo "=== Download job logs ==="
kubectl logs job/download-model -n "$NS" --tail=50 || true
echo ""
echo "=== Download pod logs (previous) ==="
for pod in $(kubectl get pods -n "$NS" -l job-name=download-model -o name 2>/dev/null); do
  echo "--- $pod ---"
  kubectl logs -n "$NS" "$pod" --tail=50 2>/dev/null || true
  kubectl logs -n "$NS" "$pod" --previous --tail=50 2>/dev/null || true
done
echo ""
echo "=== Disk usage on node ==="
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}: allocatable ephemeral={.status.allocatable.ephemeral-storage}, capacity={.status.capacity.ephemeral-storage}{"\n"}{end}' || true
echo ""
echo "=== Failed or restarting pod descriptions ==="
failed_pods=$(kubectl get pods -n "$NS" -o json 2>/dev/null | jq -r '.items[] | select(.status.phase != "Succeeded" and (.status.phase != "Running" or any(.status.containerStatuses[]?; .ready == false or .restartCount > 0))) | "pod/" + .metadata.name')
for pod in $failed_pods; do
  echo "--- $pod ---"
  kubectl describe -n "$NS" "$pod" 2>/dev/null | tail -30
  echo "--- current logs ---"
  kubectl logs -n "$NS" "$pod" --tail=50 --all-containers 2>/dev/null || true
  echo "--- previous logs ---"
  kubectl logs -n "$NS" "$pod" --previous --tail=50 --all-containers 2>/dev/null || true
done
echo ""
echo "=== Events ==="
kubectl get events -n "$NS" --sort-by='.lastTimestamp' | tail -20 || true
