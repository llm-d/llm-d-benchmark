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
# Pods stuck in CrashLoopBackOff stay in phase Running, so the loop above
# never sees them and the crash output only exists in the *previous*
# container instance. Dump it for every container that has restarted or
# is waiting on a crash/backoff, along with the last termination state.
echo "=== Restarted / crash-looping containers (previous logs) ==="
for pod in $(kubectl get pods -n "$NS" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
  kubectl get pod -n "$NS" "$pod" -o jsonpath='{range .status.containerStatuses[*]}{.name}{"|"}{.restartCount}{"|"}{.state.waiting.reason}{"|"}{.lastState.terminated.reason}{"|"}{.lastState.terminated.exitCode}{"|"}{.lastState.terminated.startedAt}{"|"}{.lastState.terminated.finishedAt}{"\n"}{end}' 2>/dev/null \
  | while IFS='|' read -r container restarts waiting reason exitcode started finished; do
    [[ -z $container ]] && continue
    if [[ ${restarts:-0} -gt 0 || $waiting == CrashLoopBackOff || $waiting == Error ]]; then
      echo "--- pod/$pod container=$container restarts=$restarts waiting=${waiting:-none} lastTerminated=${reason:-none} exitCode=${exitcode:-none} ran=${started:-?} -> ${finished:-?} ---"
      kubectl logs -n "$NS" "$pod" -c "$container" --previous --tail=100 2>&1 || true
    fi
  done
done
echo ""
echo "=== Events ==="
kubectl get events -n "$NS" --sort-by='.lastTimestamp' | tail -20 || true
