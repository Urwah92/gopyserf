#!/usr/bin/env bash
# Leave Serf on clab-century-serf1..clab-century-serf25
# Serf binary path: /opt/serfapp/serf

set -euo pipefail

BASE="clab-century-serf"
START=1
END=25
PORTS=(7373 7393 7400)   # try LAN/WAN/alt RPC ports

echo "Leaving Serf on ${BASE}{${START}..${END}} …"

for i in $(seq "$START" "$END"); do
  cname="${BASE}${i}"
  # skip if container not running
  if ! docker ps --format '{{.Names}}' | grep -qx "$cname"; then
    echo "[$cname] not running — skip"
    continue
  fi

  echo "[$cname] attempting graceful leave"
  docker exec -i "$cname" bash -lc '
    set -e
    SERF="/opt/serfapp/serf"
    if [[ ! -x "$SERF" ]]; then
      echo "  [skip] /opt/serfapp/serf not found or not executable"
      exit 0
    fi

    LEFT=0
    for p in '"${PORTS[*]}"'; do
      if "$SERF" members -rpc-addr=127.0.0.1:"$p" >/dev/null 2>&1; then
        if "$SERF" leave -rpc-addr=127.0.0.1:"$p" >/dev/null 2>&1; then
          echo "  left via RPC :$p"
          LEFT=1
        else
          echo "  tried RPC :$p but leave failed"
        fi
      fi
    done

    if [[ $LEFT -eq 0 ]]; then
      echo "  no local agent reachable on ports: '"${PORTS[*]}"'"
    fi
  '
done

echo "Done."
