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
echo "=== Failed pod descriptions ==="
for pod in $(kubectl get pods -n "$NS" --field-selector=status.phase!=Running,status.phase!=Succeeded -o name 2>/dev/null); do
  echo "--- $pod ---"
  kubectl describe -n "$NS" "$pod" 2>/dev/null | tail -20
  echo "--- logs ---"
  kubectl logs -n "$NS" "$pod" --tail=30 --all-containers 2>/dev/null || true
done
echo ""
echo "=== Events ==="
kubectl get events -n "$NS" --sort-by='.lastTimestamp' | tail -20 || true
