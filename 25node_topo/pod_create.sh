#!/usr/bin/env bash
# apply-manifests-to-serf.sh
# Run a set of `k3s kubectl apply -f ...` commands inside clab-century-serf1..25

set -uo pipefail

# Container runtime: docker (default) or podman
RUNTIME="${RUNTIME:-docker}"

# How many times to check for containerd readiness inside the container
READY_ATTEMPTS="${READY_ATTEMPTS:-40}"   # ~2 minutes with 3s sleeps
READY_SLEEP="${READY_SLEEP:-3}"

# Concurrency for running across containers (adjust to your host)
PARALLEL="${PARALLEL:-6}"

# List of manifest files to apply inside each container
MANIFESTS=(
  /tmp/qos-controller-daemonset.yaml
  /tmp/service-account.yaml
  /tmp/cluster-role.yaml
  /tmp/cluster-role-binding.yaml
  /tmp/deployment-scheduler.yaml
  /tmp/ram_price.yaml
  /tmp/storage_price.yaml
  /tmp/vcpu_price.yaml
  /tmp/vgpu_price.yaml
)

# Build container list: clab-century-serf1..25
CONTAINERS=()
for i in $(seq 11 25); do
  CONTAINERS+=("clab-century-serf${i}")
done

apply_in_container() {
  local c="$1"

  # Ensure the container exists
  if ! $RUNTIME inspect "$c" >/dev/null 2>&1; then
    echo "[$c] WARNING: container not found, skipping." >&2
    return 0
  fi

  echo "[$c] Starting…"

  # Run everything in a single non-interactive bash -lc session
  $RUNTIME exec -u root -i "$c" bash -lc "
    set -o nounset

    # Find k3s
    if ! command -v k3s >/dev/null 2>&1; then
      echo '[ERROR] k3s not found in container' >&2
      exit 1
    fi

    # Wait for containerd via 'k3s ctr version'
    echo 'Waiting for containerd to be ready…'
    ready=0
    for i in \$(seq 1 $READY_ATTEMPTS); do
      if k3s ctr version >/dev/null 2>&1; then
        ready=1; break
      fi
      sleep $READY_SLEEP
    done
    if [ \"\$ready\" -ne 1 ]; then
      echo '[ERROR] containerd did not become ready in time' >&2
      exit 2
    fi

    # Try to use kubeconfig if present
    if [ -f /etc/rancher/k3s/k3s.yaml ]; then
      export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
    fi

    # Apply each manifest if it exists and is non-empty
    rc=0
    for mf in ${MANIFESTS[*]} ; do
      if [ -s \"\$mf\" ]; then
        echo \"Applying \$mf …\"
        if ! k3s kubectl apply -f \"\$mf\"; then
          echo \"[WARN] Failed to apply \$mf (continuing)\" >&2
          rc=3
        fi
      else
        echo \"[SKIP] \$mf not found or empty\"
      fi
    done
    exit \$rc
  "
  status=$?
  if [ $status -eq 0 ]; then
    echo "[$c] Done."
  elif [ $status -eq 3 ]; then
    echo "[$c] Completed with some apply warnings."
  else
    echo "[$c] ERROR (exit code $status)." >&2
  fi
  return $status
}

export -f apply_in_container
export RUNTIME READY_ATTEMPTS READY_SLEEP MANIFESTS

# Run across containers (parallelizable)
printf '%s\n' "${CONTAINERS[@]}" | xargs -I{} -P "$PARALLEL" bash -c 'apply_in_container "$@"' _ {}

# Optional: summarize failures
echo
echo "All tasks dispatched. Review messages above for any errors/warnings."
