#!/usr/bin/env bash
# make-config-buyer-exec.sh
# Mark /opt/serfapp/config_buyer.sh executable in clab-century-serf1..25

set -euo pipefail

# Container runtime: docker (default) or podman
RUNTIME="${RUNTIME:-docker}"

for i in $(seq 1 25); do
  C="clab-century-serf$i"

  # Ensure container exists and is running
  if ! $RUNTIME inspect "$C" >/dev/null 2>&1; then
    echo "[$C] container not found, skipping."
    continue
  fi
  if [[ "$($RUNTIME inspect -f '{{.State.Running}}' "$C")" != "true" ]]; then
    echo "[$C] container not running, skipping."
    continue
  fi

  # Do the chmod, then show the result (if file exists)
  echo "[$C] setting executable bit on /opt/serfapp/config_buyer.sh …"
  $RUNTIME exec -u root "$C" sh -lc '
    if [ -f /opt/serfapp/config_buyer.sh ]; then
      chmod +x /opt/serfapp/config_buyer.sh && ls -l /opt/serfapp/config_buyer.sh
    else
      echo "[WARN] /opt/serfapp/config_buyer.sh not found"
      exit 0
    fi
  '
done

echo "Done."
