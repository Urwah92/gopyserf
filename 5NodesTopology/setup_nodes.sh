#!/bin/bash
set -euo pipefail

# ---------- CONFIG ----------
# List of containers (your Serf nodes)
containers=()
for i in {1..5}; do
  containers+=(clab-century-serf$i)
done

# Local source directory containing serf binary, scripts, etc.
SERFAPP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/serfapp"

# Remote destination directory inside containers
destination_dir="/opt/serfapp"
# ----------------------------

# --- Ensure required commands exist ---
require() { command -v "$1" >/dev/null 2>&1 || { echo "❌ Missing: $1"; exit 1; }; }
require docker
require tar

# --- Main setup function ---
setup_ubuntu_nodes() {
  echo "=== Setting up Serf Nodes ==="

  for container in "${containers[@]}"; do
    if ! docker ps --format '{{.Names}}' | grep -qx "$container"; then
      echo "⚠️  $container is not running, skipping..."
      continue
    fi

    echo
    echo "➡️  Setting up $container ..."

    # Get eth1 IPv4 address
    ip_address="$(docker exec "$container" sh -lc "ip -4 addr show eth1 | awk '/inet /{print \$2}' | cut -d/ -f1" || true)"
    if [[ -z "${ip_address}" ]]; then
      echo "❌ Failed to retrieve eth1 IP for $container, skipping..."
      continue
    fi
    echo "   ➜ eth1 IP = $ip_address"

    # Create destination directory inside container
    docker exec "$container" mkdir -p "$destination_dir"

    # Copy serfapp/ contents — EXCLUDING node.json, logs, and __pycache__
    (
      cd "$SERFAPP_DIR"
      tar \
        --exclude='node.json' \
        --exclude='__pycache__' \
        --exclude='*/__pycache__' \
        --exclude='*.log' \
        --exclude='*.log.*' \
        -czf - .
    ) | docker exec -i "$container" tar -C "$destination_dir" -xzf -

    # Create a unique node.json for each container
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

    # Ensure all binaries & scripts are executable
    docker exec "$container" sh -lc "
      find '$destination_dir' -type f \( -name '*.sh' -o -name '*.py' \) -exec chmod +x {} + 2>/dev/null || true
      [ -f '$destination_dir/serf' ] && chmod +x '$destination_dir/serf' || true
    "

    echo "✅ Finished $container setup."
  done

  echo
  echo "=== ✅ All nodes configured successfully ==="
}

# --- Run setup ---
setup_ubuntu_nodes
 


