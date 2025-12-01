#!/usr/bin/env bash
# Run buyer configuration inside one or more containers:
#   1) ./config_buyer.sh
#   2) (after 5 seconds) ./config_service_discovery_v2.sh
# Both run detached, logs -> /var/log/serfapp_bg.log inside each container.

set -euo pipefail

# -------- Defaults (change via flags) --------
PREFIX="clab-century-serf"              # container name prefix (e.g., clab-century-buyer1)
START=1                                  # first index
END=10                                    # last index (START==END => single container)
CONTAINER_APP_DIR="/opt/serfapp"         # where scripts live inside container
BUYER_SCRIPT="config_buyer.sh"
DISC_SCRIPT="config_service_discovery.sh"
DELAY_SECS=5                             # wait between buyer script and discovery

# -------- Flags --------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2;;
    --start) START="$2"; shift 2;;
    --end) END="$2"; shift 2;;
    --dir|--container-dir) CONTAINER_APP_DIR="$2"; shift 2;;
    --buyer-script) BUYER_SCRIPT="$2"; shift 2;;
    --discovery-script|--disc-script) DISC_SCRIPT="$2"; shift 2;;
    --delay|--sleep) DELAY_SECS="$2"; shift 2;;
    --help|-h)
      cat <<USAGE
Usage: $0 [--prefix PFX] [--start N] [--end M]
          [--container-dir /opt/serfapp]
          [--buyer-script ./config_buyer.sh]
          [--disc-script ./config_service_discovery_v2.sh]
          [--delay 5]

Examples:
  $0                                  # runs in clab-century-buyer1
  $0 --start 1 --end 3                # runs in clab-century-buyer1..3
  $0 --prefix my-buyer- --start 2 --end 5
USAGE
      exit 0;;
    *)
      echo "Unknown flag: $1" >&2; exit 1;;
  esac
done

log()  { echo -e "[\e[1mBUYER\e[0m] $*"; }
warn() { echo -e "[\e[33mWARN\e[0m] $*" >&2; }
err()  { echo -e "[\e[31mERROR\e[0m] $*" >&2; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || { err "Required command '$1' not found."; exit 1; }; }

require_cmd docker

container_name() { echo "${PREFIX}$1"; }

file_exists_in_container() {
  local name="$1"; local path="$2"
  docker exec -u root "$name" bash -lc "[ -e '$path' ]"
}

run_bg_in_container_dir() {
  local name="$1"; shift
  local dir="$1"; shift
  local cmd="$*"
  docker exec -u root "$name" bash -lc "mkdir -p /var/log"
  # -d detaches the exec; we also background in-shell to be safe
  docker exec -u root -d "$name" bash -lc "cd '$dir' && { $cmd >>/var/log/serfapp_bg.log 2>&1 & }"
}

for i in $(seq "$START" "$END"); do
  name="$(container_name "$i")"
  log "Target: $name"

  # Buyer script
  if file_exists_in_container "$name" "${CONTAINER_APP_DIR}/${BUYER_SCRIPT}"; then
    log "Launching $BUYER_SCRIPT in $name (detached)..."
    run_bg_in_container_dir "$name" "$CONTAINER_APP_DIR" "bash '${BUYER_SCRIPT}'"
  else
    warn "Missing ${CONTAINER_APP_DIR}/${BUYER_SCRIPT} in $name; skipping."
  fi

  # Delay
  log "Sleeping ${DELAY_SECS}s before discovery script in $name..."
  sleep "${DELAY_SECS}"

  # Discovery v2 script
  if file_exists_in_container "$name" "${CONTAINER_APP_DIR}/${DISC_SCRIPT}"; then
    log "Launching $DISC_SCRIPT in $name (detached)..."
    run_bg_in_container_dir "$name" "$CONTAINER_APP_DIR" "bash '${DISC_SCRIPT}'"
  else
    warn "Missing ${CONTAINER_APP_DIR}/${DISC_SCRIPT} in $name; skipping."
  fi

  log "Done with $name. Tail logs using: docker exec -it $name tail -f /var/log/serfapp_bg.log"
done

log "All buyer scripts launched."
