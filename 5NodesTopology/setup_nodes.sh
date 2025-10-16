#!/bin/bash
set -euo pipefail

# ---------- CONFIG ----------
# Containers to target
containers=()
for i in {1..5}; do
  containers+=(clab-century-serf$i)
done

# Local source dir (host)
SERFAPP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/serfapp"

# Remote destination dir (in containers)
destination_dir="/opt/serfapp"
# ----------------------------

require() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1"; exit 1; }; }
require docker
require tar

setup_ubuntu_nodes() {
  for container in "${containers[@]}"; do
    # Check running
    if ! docker ps --format '{{.Names}}' | grep -qx "$container"; then
      echo "Container $container is not running, skipping..."
      continue
    fi
    echo "Setting up $container..."

    # Get eth1 IPv4
    ip_address="$(docker exec "$container" sh -lc "ip -4 addr show eth1 | awk '/inet /{print \$2}' | cut -d/ -f1" || true)"
    if [[ -z "${ip_address}" ]]; then
      echo "Failed to retrieve eth1 IP for $container, skipping..."
      continue
    fi
    echo "IP address for $container (eth1): $ip_address"

    # Create destination dir
    docker exec "$container" mkdir -p "$destination_dir"

    # Generate node.json (fresh per container)
    tmp_json="$(mktemp)"
    cat > "$tmp_json" <<EOF
{
  "node_name": "$container",
  "bind": "0.0.0.0:7946",
  "advertise": "$ip_address:7946",
  "rpc_addr": "0.0.0.0:7373"
}
EOF
    docker cp "$tmp_json" "$container":"$destination_dir/node.json"
    rm -f "$tmp_json"

    # Copy serfapp/ contents, excluding __pycache__ and *.log
    (
      cd "$SERFAPP_DIR"
      tar \
        --exclude='__pycache__' \
        --exclude='*/__pycache__' \
        --exclude='*.log' \
        --exclude='*.log.*' \
        -czf - .
    ) | docker exec -i "$container" tar -C "$destination_dir" -xzf -

    # Ensure exec bits on expected scripts/binaries (best-effort)
    docker exec "$container" sh -lc "
      # Make top-level .sh and .py executable
      find '$destination_dir' -maxdepth 1 -type f -name '*.sh' -exec chmod +x {} + 2>/dev/null || true
      find '$destination_dir' -maxdepth 1 -type f -name '*.py' -exec chmod +x {} + 2>/dev/null || true
      # Make any start_*.sh anywhere executable
      find '$destination_dir' -type f -name 'start_*.sh' -exec chmod +x {} + 2>/dev/null || true
      # Make serf binary executable if present (now inside serfapp/)
      [ -f '$destination_dir/serf' ] && chmod +x '$destination_dir/serf' || true
    "

    echo "$container setup complete."
  done
}

# Main
setup_ubuntu_nodes

