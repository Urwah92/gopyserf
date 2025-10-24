#!/usr/bin/env bash
# Orchestrate OVS + Containerlab + Serf containers bootstrap
# Default flow:
#  1) Ensure Open vSwitch is installed and bridge "central_switch" exists
#  2) clab deploy -t ceso5node_v1.yml
#  3) Run host scripts: ./ipaddressing.sh, ./setup_nodes.sh, ./serf_agents_start.sh, ./serf_agents_joining.sh
#  4) Wait for containers, then:
#       - On serf1: cd /opt/serfapp && ./pygo, then python3 member.py
#       - On ALL containers serf{START..END}: cd /opt/serfapp && python3 member.py
#  5) Sleep 30s, then on serf{CONF_START..CONF_END}: cd /opt/serfapp && ./conf_ACtop2p.sh
#
# You can adjust the ranges and filenames with flags. Example:
#   sudo ./orchestrate_serf.sh \
#     --prefix clab-century-serf --start 1 --end 8 \
#     --conf-start 2 --conf-end 8 \
#     --topo ceso5node_v1.yml \
#     --sleep-after-deploy 8 \
#     --sleep-before-conf 30

set -euo pipefail

# ------------------ Defaults (override with flags) ------------------
PREFIX="clab-century-serf"        # container name prefix
START=1                           # first index (e.g., serf1)
END=5                             # last index  (e.g., serf5)
CONF_START=2                      # run conf script from this index...
CONF_END=5                        # ...to this index
TOPO_FILE="ceso5node_v1.yml"      # containerlab topology file
BRIDGE_NAME="central_switch"      # OVS bridge name

SLEEP_AFTER_DEPLOY=8              # seconds to wait after clab deploy
SLEEP_AFTER_JOIN=5                # seconds after serf_agents_joining.sh
SLEEP_BEFORE_CONF=30              # seconds before running conf scripts

# Host scripts (must exist in current dir)
SCRIPT_IP="./ipaddressing.sh"
SCRIPT_SERF_START="./serf_agents_start.sh"
SCRIPT_SERF_JOIN="./serf_agents_joining.sh"

# Paths inside container
CONTAINER_APP_DIR="/opt/serfapp"
BIN_PYGO="./pygo"
PY_MEMBER="member.py"
CONF_SCRIPT="./conf_ACtop2p.sh"

# ------------------ Parse flags ------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2;;
    --start) START="$2"; shift 2;;
    --end) END="$2"; shift 2;;
    --conf-start) CONF_START="$2"; shift 2;;
    --conf-end) CONF_END="$2"; shift 2;;
    --topo|--topo-file) TOPO_FILE="$2"; shift 2;;
    --bridge|--bridge-name) BRIDGE_NAME="$2"; shift 2;;
    --sleep-after-deploy) SLEEP_AFTER_DEPLOY="$2"; shift 2;;
    --sleep-after-join) SLEEP_AFTER_JOIN="$2"; shift 2;;
    --sleep-before-conf) SLEEP_BEFORE_CONF="$2"; shift 2;;
    --help|-h)
      echo "Usage: $0 [--prefix PFX] [--start N] [--end M] [--conf-start A] [--conf-end B] [--topo FILE] [--bridge NAME]"
      exit 0
      ;;
    *)
      echo "Unknown flag: $1" >&2; exit 1;;
  esac
done

# ------------------ Helpers ------------------
log() { echo -e "[\e[1mINFO\e[0m] $*"; }
warn() { echo -e "[\e[33mWARN\e[0m] $*" >&2; }
err() { echo -e "[\e[31mERROR\e[0m] $*" >&2; }

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "Required command '$1' not found."
    return 1
  fi
}

ensure_openvswitch() {
  if command -v ovs-vsctl >/dev/null 2>&1; then
    log "Open vSwitch already installed."
    return 0
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
    local name
    name="$(container_name "$i")"
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

run_in_container_dir() {
  local name="$1"; shift
  local dir="$1"; shift
  exec_in_container "$name" bash -lc "cd '$dir' && $*"
}

file_exists_in_container() {
  local name="$1"; local path="$2"
  exec_in_container "$name" bash -lc "[ -e '$path' ]"
}

# ------------------ Start ------------------
require_cmd docker
require_cmd clab
ensure_openvswitch
ensure_bridge "$BRIDGE_NAME"

deploy_topology

# Host-side scripts
run_host_script "$SCRIPT_IP"
run_host_script "$SCRIPT_SERF_START"
run_host_script "$SCRIPT_SERF_JOIN"
log "Sleeping ${SLEEP_AFTER_JOIN}s after join..."
sleep "$SLEEP_AFTER_JOIN"

# Make sure target containers are running
wait_for_containers

# Step 1: Only on serf1 (START index), run ./pygo then python3 member.py
SERF1_NAME="$(container_name "$START")"
log "On $SERF1_NAME: running ${CONTAINER_APP_DIR}/${BIN_PYGO} (if present) ..."
if file_exists_in_container "$SERF1_NAME" "${CONTAINER_APP_DIR}/${BIN_PYGO}"; then
  run_in_container_dir "$SERF1_NAME" "$CONTAINER_APP_DIR" "${BIN_PYGO}"
else
  warn "Missing ${CONTAINER_APP_DIR}/${BIN_PYGO} in $SERF1_NAME; skipping."
fi

log "On $SERF1_NAME: running python3 ${PY_MEMBER} (if present) ..."
if file_exists_in_container "$SERF1_NAME" "${CONTAINER_APP_DIR}/${PY_MEMBER}"; then
  run_in_container_dir "$SERF1_NAME" "$CONTAINER_APP_DIR" "python3 '${PY_MEMBER}' >/var/log/member_py.log 2>&1 & disown"
else
  warn "Missing ${CONTAINER_APP_DIR}/${PY_MEMBER} in $SERF1_NAME; skipping."
fi

# Step 2: On ALL containers serf{START..END}, run python3 member.py
for i in $(seq "$START" "$END"); do
  name="$(container_name "$i")"
  # Skip, we already ran on serf1 above, but run again won't hurt; keep idempotent check:
  if file_exists_in_container "$name" "${CONTAINER_APP_DIR}/${PY_MEMBER}"; then
    log "On $name: launching python3 ${PY_MEMBER} in background..."
    run_in_container_dir "$name" "$CONTAINER_APP_DIR" "python3 '${PY_MEMBER}' >/var/log/member_py.log 2>&1 & disown"
  else
    warn "Missing ${CONTAINER_APP_DIR}/${PY_MEMBER} in $name; skipping."
  fi
done

# Step 3: Pause then run conf script on subset (CONF_START..CONF_END)
log "Sleeping ${SLEEP_BEFORE_CONF}s before running conf scripts..."
sleep "$SLEEP_BEFORE_CONF"

for i in $(seq "$CONF_START" "$CONF_END"); do
  name="$(container_name "$i")"
  if file_exists_in_container "$name" "${CONTAINER_APP_DIR}/${CONF_SCRIPT}"; then
    log "On $name: running ${CONF_SCRIPT}..."
    run_in_container_dir "$name" "$CONTAINER_APP_DIR" "bash '${CONF_SCRIPT}'"
  else
    warn "Missing ${CONTAINER_APP_DIR}/${CONF_SCRIPT} in $name; skipping."
  fi
done

log "All done ✅"
