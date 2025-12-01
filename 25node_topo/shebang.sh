#!/usr/bin/env bash
set -euo pipefail
RUNTIME="${RUNTIME:-docker}"

for i in $(seq 1 25); do
  C="clab-century-serf$i"
  if ! $RUNTIME inspect "$C" >/dev/null 2>&1; then
    echo "[$C] not found, skipping"
    continue
  fi
  echo "[$C] patching /opt/serfapp/config_service_discovery.sh"
  $RUNTIME exec -u root "$C" sh -lc '
    f="/opt/serfapp/config_service_discovery.sh"
    if [ ! -f "$f" ]; then
      echo "  [WARN] file not found"; exit 0
    fi
    sed -i "1s|^#!.*|#!/usr/bin/env bash|" "$f" || exit 1
    perl -i -pe "s/\r$//" "$f" || true
    chmod +x "$f"
    head -n 2 "$f"
  '
done

echo "Done. You can now execute the script in each container."
1~#!/usr/bin/env bash
set -euo pipefail
RUNTIME="${RUNTIME:-docker}"

for i in $(seq 1 25); do
  C="clab-century-serf$i"
  if ! $RUNTIME inspect "$C" >/dev/null 2>&1; then
    echo "[$C] not found, skipping"
    continue
  fi
  echo "[$C] patching /opt/serfapp/config_service_discovery.sh"
  $RUNTIME exec -u root "$C" sh -lc '
    f="/opt/serfapp/config_service_discovery.sh"
    if [ ! -f "$f" ]; then
      echo "  [WARN] file not found"; exit 0
    fi
    sed -i "1s|^#!.*|#!/usr/bin/env bash|" "$f" || exit 1
    perl -i -pe "s/\r$//" "$f" || true
    chmod +x "$f"
    head -n 2 "$f"
  '
done

echo "Done. You can now execute the script in each container."
1~#!/usr/bin/env bash
set -euo pipefail
RUNTIME="${RUNTIME:-docker}"

for i in $(seq 1 25); do
  C="clab-century-serf$i"
  if ! $RUNTIME inspect "$C" >/dev/null 2>&1; then
    echo "[$C] not found, skipping"
    continue
  fi
  echo "[$C] patching /opt/serfapp/config_service_discovery.sh"
  $RUNTIME exec -u root "$C" sh -lc '
    f="/opt/serfapp/config_service_discovery.sh"
    if [ ! -f "$f" ]; then
      echo "  [WARN] file not found"; exit 0
    fi
    sed -i "1s|^#!.*|#!/usr/bin/env bash|" "$f" || exit 1
    perl -i -pe "s/\r$//" "$f" || true
    chmod +x "$f"
    head -n 2 "$f"
  '
done

echo "Done. You can now execute the script in each container."
1~#!/usr/bin/env bash
set -euo pipefail
RUNTIME="${RUNTIME:-docker}"

for i in $(seq 1 25); do
  C="clab-century-serf$i"
  if ! $RUNTIME inspect "$C" >/dev/null 2>&1; then
    echo "[$C] not found, skipping"
    continue
  fi
  echo "[$C] patching /opt/serfapp/config_service_discovery.sh"
  $RUNTIME exec -u root "$C" sh -lc '
    f="/opt/serfapp/config_service_discovery.sh"
    if [ ! -f "$f" ]; then
      echo "  [WARN] file not found"; exit 0
    fi
    sed -i "1s|^#!.*|#!/usr/bin/env bash|" "$f" || exit 1
    perl -i -pe "s/\r$//" "$f" || true
    chmod +x "$f"
    head -n 2 "$f"
  '
done

echo "Done. You can now execute the script in each container."
