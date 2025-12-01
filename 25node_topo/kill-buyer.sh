#!/usr/bin/env bash
set -euo pipefail
RUNTIME="${RUNTIME:-docker}"   # or podman

for i in $(seq 10 10); do
  C="clab-century-serf$i"
  if ! $RUNTIME inspect "$C" >/dev/null 2>&1; then
    echo "[$C] not found, skipping"
    continue
  fi

  echo "[$C] stopping buyer on :8090 (if running)…"
  $RUNTIME exec -u root "$C" sh -lc '
    # Try to find by port and send SIGTERM
    if command -v ss >/dev/null 2>&1 && ss -ltnp "sport = :8090" 2>/dev/null | grep -q LISTEN; then
      PIDS=$(ss -ltnp "sport = :8090" | awk -F, "/users/ {gsub(/pid=/,\"\",\$2); print \$2}" | awk "{print \$1}")
      for p in $PIDS; do kill -15 "$p" 2>/dev/null || true; done
      sleep 1
    fi
    # Also try by command line
    pkill -15 -f "buyer.*8090" 2>/dev/null || true
    sleep 1
    # Force if still present
    pkill -9  -f "buyer.*8090" 2>/dev/null || true

    # Show what’s left on 8090
    echo "Remaining listeners on 8090:"
    ss -ltnp "sport = :8090" 2>/dev/null || true
  '
done

echo "Done."
