#!/usr/bin/env bash
# check-k3s-pods-serf.sh
# Verifies that all pods in each clab-century-serfN container are healthy.

set -uo pipefail

# Container runtime: "docker" (default) or "podman"
RUNTIME="${RUNTIME:-docker}"

# Parallelism (how many containers to check at once)
PARALLEL="${PARALLEL:-6}"

# How long to wait for k3s API in each container
READY_ATTEMPTS="${READY_ATTEMPTS:-40}"   # ~2 minutes with 3s sleeps
READY_SLEEP="${READY_SLEEP:-3}"

# Colors (disable with NO_COLOR=1)
if [[ -z "${NO_COLOR:-}" ]]; then
  C_GREEN=$'\e[32m'; C_RED=$'\e[31m'; C_YELLOW=$'\e[33m'; C_RESET=$'\e[0m'
else
  C_GREEN=; C_RED=; C_YELLOW=; C_RESET=
fi

# Build container list
CONTAINERS=()
for i in $(seq 1 25); do
  CONTAINERS+=("clab-century-serf${i}")
done

check_container() {
  local c="$1"
  local rc=0

  # Ensure the container exists and is running
  if ! $RUNTIME inspect "$c" >/dev/null 2>&1; then
    printf "%s[SKIP]%s %-24s %s\n" "$C_YELLOW" "$C_RESET" "$c" "container not found"
    return 0
  fi

  if [[ "$($RUNTIME inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" != "true" ]]; then
    printf "%s[FAIL]%s %-24s %s\n" "$C_RED" "$C_RESET" "$c" "container not running"
    return 1
  fi

  # One exec session does the whole check
  local output
  if ! output="$($RUNTIME exec -u root -i "$c" bash -lc "
      set -e
      # Prefer the cluster kubeconfig if present
      if [ -f /etc/rancher/k3s/k3s.yaml ]; then
        export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
      fi

      # Wait for k3s API (via kubectl) and containerd (via ctr)
      ready=0
      for i in \$(seq 1 $READY_ATTEMPTS); do
        if k3s ctr version >/dev/null 2>&1 && k3s kubectl get nodes --no-headers >/dev/null 2>&1; then
          ready=1; break
        fi
        sleep $READY_SLEEP
      done
      if [ \"\$ready\" -ne 1 ]; then
        echo 'NOT_READY k3s API/containerd not ready'
        exit 10
      fi

      # Get pods table
      pods=\$(k3s kubectl get pods -A --no-headers 2>/dev/null || true)
      if [ -z \"\$pods\" ]; then
        echo 'FAIL  no pods returned'
        exit 11
      fi

      # Analyze:
      # Column layout: NAMESPACE NAME READY STATUS RESTARTS AGE
      # We require: READY == X/X and STATUS in {Running, Completed}
      # Anything else counts as not-healthy.
      not_ready_lines=\$(awk '
        {
          # READY field is $3 (e.g., 1/1)
          split(\$3, r, \"/\");
          ready_ok = (r[1] == r[2] && r[1] != \"\");
          status_ok = (\$4 == \"Running\" || \$4 == \"Completed\");
          if (!ready_ok || !status_ok) { print \$0 }
        }
      ' <<< \"\$pods\")

      total=\$(wc -l <<< \"\$pods\")
      unhealthy=\$(wc -l <<< \"\$not_ready_lines\")
      if [ \"\$unhealthy\" -eq 0 ]; then
        echo \"OK total=\$total\"
      else
        echo \"UNHEALTHY total=\$total bad=\$unhealthy\"
        echo \"---BEGIN-UNHEALTHY---\"
        echo \"\$not_ready_lines\"
        echo \"---END-UNHEALTHY---\"
        exit 12
      fi
    " 2>&1)"; then
    # Exec failed or health check returned non-zero
    rc=$?
    if grep -q '^NOT_READY' <<<"$output"; then
      printf "%s[WARN]%s %-24s %s\n" "$C_YELLOW" "$C_RESET" "$c" "k3s not ready"
    elif grep -q '^UNHEALTHY' <<<"$output"; then
      local summary
      summary=$(grep '^UNHEALTHY' <<<"$output" | head -1)
      printf "%s[FAIL]%s %-24s %s\n" "$C_RED" "$C_RESET" "$c" "$summary"
      # Show first few problematic lines for context
      awk '/---BEGIN-UNHEALTHY---/{flag=1;next}/---END-UNHEALTHY---/{flag=0}flag' <<<"$output" | head -5 \
        | sed 's/^/    /'
    else
      printf "%s[FAIL]%s %-24s exec error (rc=%s)\n" "$C_RED" "$C_RESET" "$c" "$rc"
      echo "$output" | sed 's/^/    /' | head -20
    fi
    return 1
  fi

  # Healthy path
  local summary
  summary=$(grep '^OK ' <<<"$output" | head -1)
  printf "%s[ OK ]%s %-24s %s\n" "$C_GREEN" "$C_RESET" "$c" "$summary"
  return 0
}

export -f check_container
export RUNTIME READY_ATTEMPTS READY_SLEEP C_GREEN C_RED C_YELLOW C_RESET

# Run checks (in parallel)
printf '%s\n' "${CONTAINERS[@]}" | xargs -I{} -P "$PARALLEL" bash -c 'check_container "$@"' _ {}

# Final result: non-zero if any failure
echo
echo "Check complete."
