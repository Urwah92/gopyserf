#!/usr/bin/env bash
# Replace /opt/serfapp/service_discovery.py in clab-century-serf1..serf10
# Usage: ./push_service_discovery_simple.sh [path/to/service_discovery_v2.py]

set -euo pipefail

SRC="${1:-./ch_broker}"
DEST_DIR="/opt/serfapp"
DEST_FILE="ch_broker"

if [ ! -f "$SRC" ]; then
  echo "ERROR: source file not found: $SRC" >&2
  exit 1
fi

for i in $(seq 1 20); do
  cname="clab-century-serf$i"

  if ! docker container inspect "$cname" >/dev/null 2>&1; then
    echo "[-] $cname not found (not running or wrong name), skipping"
    continue
  fi

  echo "[+] $cname: updating ${DEST_DIR}/${DEST_FILE}"
  # copy to a temp path first, then move into place
  docker cp "$SRC" "$cname:/tmp/__service_discovery.py"
  docker exec "$cname" sh -lc "
    mkdir -p '$DEST_DIR' &&
    mv -f '/tmp/__service_discovery_v5.py' '$DEST_DIR/$DEST_FILE' &&
    chmod 0755 '$DEST_DIR/$DEST_FILE'
  "
  echo "    $cname: done"
done

echo "[i] All done."
