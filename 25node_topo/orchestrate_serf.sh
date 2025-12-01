#!/usr/bin/env bash
# Orchestrate OVS + Containerlab + Serf containers bootstrap
# (No conf step here)

set -euo pipefail

# ------------------ Defaults (override with flags) ------------------
PREFIX="clab-century-serf"
START=1
END=25
TOPO_FILE="25node_update.yml"
BRIDGE_NAME="switch_a"

SLEEP_AFTER_DEPLOY=8
SLEEP_AFTER_JOIN=5

# Host scripts
SCRIPT_IP="./ipaddressing.sh"
SCRIPT_SERF_START="./serf_agents_start.sh"
SCRIPT_SERF_JOIN="./serf_agents_joining.sh"

# Paths inside container
CONTAINER_APP_DIR="/opt/serfapp"
BIN_PYGO="./pygo"
PY_MEMBER="member.py"

# ------------------ Parse flags ------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2;;
    --start) START="$2"; shift 2;;
    --end) END="$2"; shift 2;;
    --topo|--topo-file) TOPO_FILE="$2"; shift 2;;
    --bridge|--bridge-name) BRIDGE_NAME="$2"; shift 2;;
    --sleep-after-deploy) SLEEP_AFTER_DEPLOY="$2"; shift 2;;
    --sleep-after-join) SLEEP_AFTER_JOIN="$2"; shift 2;;
    --help|-h)
      echo "Usage: $0 [--prefix PFX] [--start N] [--end M] [--topo FILE] [--bridge NAME]"
      exit 0
      ;;
    *) echo "Unknown flag: $1" >&2; exit 1;;
  esac
done

# ------------------ Helpers ------------------
log() { echo -e "[\e[1mINFO\e[0m] $*"; }
warn() { echo -e "[\e[33mWARN\e[0m] $*" >&2; }
err() { echo -e "[\e[31mERROR\e[0m] $*" >&2; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || { err "Required command '$1' not found."; exit 1; }; }

ensure_openvswitch() {
  if command -v ovs-vsctl >/dev/null 2>&1; then
    log "Open vSwitch already installed."
    return
  fi
  log "Installing Open vSwitch (requires sudo apt)..."
  sudo apt-get update -y
  sudo apt-get install -y openvswitch-switch
  sudo systemctl enable --now openvswitch-switch || true
  command -v ovs-vsctl >/dev/null 2>&1 || { err "ovs-vsctl still not found after install."; exit 1; }
  log "Open vSwitch installed."
}

ensure_bridge() {
  local br="$1"
  if sudo ovs-vsctl br-exists "$br"; then
    log "OVS bridge '$br' already exists."
  else
    log "Creating OVS bridge '$br'..."
    sudo ovs-vsctl add-br "$br"
    sudo ip link set "$br" up || true
    log "Bridge '$br' created and set up."
  fi
}

deploy_topology() {
  [[ -f "$TOPO_FILE" ]] || { err "Topology file '$TOPO_FILE' not found."; exit 1; }
  log "Deploying Containerlab topology: $TOPO_FILE"
  sudo clab deploy -t "$TOPO_FILE"
  log "Sleeping ${SLEEP_AFTER_DEPLOY}s after deploy..."
  sleep "$SLEEP_AFTER_DEPLOY"
}

run_host_script() {
  local f="$1"
  [[ -f "$f" ]] || { err "Host script '$f' not found."; exit 1; }
  [[ -x "$f" ]] || { warn "Host script '$f' not executable; chmod +x."; chmod +x "$f"; }
  log "Running host script: $f"
  "$f"
}

container_name() { echo "${PREFIX}$1"; }

wait_for_containers() {
  log "Waiting for containers ${PREFIX}{${START}..${END}} to be up..."
  local max_tries=30
  for i in $(seq "$START" "$END"); do
    local name; name="$(container_name "$i")"
    local tries=0
    until docker ps --format '{{.Names}}' | grep -Fxq "$name"; do
      ((tries++))
      if (( tries > max_tries )); then
        err "Container '$name' not found running after waiting."
        exit 1
      fi
      sleep 1
    done
    log "Found container: $name"
  done
}

exec_in_container() {
  local name="$1"; shift
  docker exec -u root "$name" "$@"
}

# Detached/background execution with logs inside the container.
run_bg_in_container_dir() {
  local name="$1"; shift
  local dir="$1"; shift
  local cmd="$*"
  docker exec -u root "$name" bash -lc "mkdir -p /var/log"
  docker exec -u root -d "$name" bash -lc "cd '$dir' && { $cmd >>/var/log/serfapp_bg.log 2>&1 & }"
}

file_exists_in_container() {
  local name="$1"; local path="$2"
  exec_in_container "$name" bash -lc "[ -e '$path' ]"
}

# ------------------ Start ------------------
#require_cmd docker
#require_cmd clab
#ensure_openvswitch
#ensure_bridge "$BRIDGE_NAME"

deploy_topology

# Host-side scripts
run_host_script "$SCRIPT_IP"
#./link_latency.sh
#./link-latency2.sh
run_host_script "$SCRIPT_SERF_START"
run_host_script "$SCRIPT_SERF_JOIN"
log "Sleeping ${SLEEP_AFTER_JOIN}s after join..."
sleep "$SLEEP_AFTER_JOIN"

# Make sure target containers are running
wait_for_containers

# ---------- Step 1: Only on serf1, run ./pygo (bg) then python3 member.py (bg) ----------
SERF1_NAME="$(container_name "$START")"

if file_exists_in_container "$SERF1_NAME" "${CONTAINER_APP_DIR}/${BIN_PYGO}"; then
  log "On $SERF1_NAME: launching ${BIN_PYGO} in background..."
  run_bg_in_container_dir "$SERF1_NAME" "$CONTAINER_APP_DIR" "${BIN_PYGO}"
else
  warn "Missing ${CONTAINER_APP_DIR}/${BIN_PYGO} in $SERF1_NAME; skipping."
fi

if file_exists_in_container "$SERF1_NAME" "${CONTAINER_APP_DIR}/${PY_MEMBER}"; then
  log "On $SERF1_NAME: launching python3 ${PY_MEMBER} in background..."
  run_bg_in_container_dir "$SERF1_NAME" "$CONTAINER_APP_DIR" "python3 '${PY_MEMBER}'"
else
  warn "Missing ${CONTAINER_APP_DIR}/${PY_MEMBER} in $SERF1_NAME; skipping."
fi

# ---------- Step 2: On ALL containers, run python3 member.py (bg) ----------
for i in $(seq "$START" "$END"); do
  name="$(container_name "$i")"
  if file_exists_in_container "$name" "${CONTAINER_APP_DIR}/${PY_MEMBER}"; then
    log "On $name: launching python3 ${PY_MEMBER} in background..."
    run_bg_in_container_dir "$name" "$CONTAINER_APP_DIR" "python3 '${PY_MEMBER}'"
  else
    warn "Missing ${CONTAINER_APP_DIR}/${PY_MEMBER} in $name; skipping."
  fi
done

log "All done ✅
(Logs for background processes are in /var/log/serfapp_bg.log inside each container.)"

