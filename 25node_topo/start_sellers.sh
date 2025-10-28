#!/usr/bin/env bash
# Launch a conf script inside selected containers, detached, with logs.

set -euo pipefail

PREFIX="clab-century-serf"
START=11
END=25
CONTAINER_APP_DIR="/opt/serfapp"
CONF_SCRIPT="./config_ACtop2p.sh"

log() { echo -e "[\e[1mCONF\e[0m] $*"; }
warn() { echo -e "[\e[33mWARN\e[0m] $*" >&2; }
err() { echo -e "[\e[31mERROR\e[0m] $*" >&2; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || { err "Required command '$1' not found."; exit 1; }; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2;;
    --start) START="$2"; shift 2;;
    --end) END="$2"; shift 2;;
    --dir|--container-dir) CONTAINER_APP_DIR="$2"; shift 2;;
    --script) CONF_SCRIPT="$2"; shift 2;;
    --help|-h)
      cat <<USAGE
Usage: $0 [--prefix PFX] [--start N] [--end M] [--dir /opt/serfapp] [--script ./config_ACtop2p.sh]
USAGE
      exit 0
      ;;
    *) echo "Unknown flag: $1" >&2; exit 1;;
  esac
done

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
  docker exec -u root -d "$name" bash -lc "cd '$dir' && { $cmd >>/var/log/serfapp_bg.log 2>&1 & }"
}

for i in $(seq "$START" "$END"); do
  name="$(container_name "$i")"
  if file_exists_in_container "$name" "${CONTAINER_APP_DIR}/${CONF_SCRIPT}"; then
    log "Launching ${CONF_SCRIPT} in $name (detached)..."
    run_bg_in_container_dir "$name" "$CONTAINER_APP_DIR" "bash '${CONF_SCRIPT}'"
  else
    warn "Missing ${CONTAINER_APP_DIR}/${CONF_SCRIPT} in $name; skipping."
  fi
done

log "Done. Tail logs with: docker exec -it ${PREFIX}${START} tail -f /var/log/serfapp_bg.log"
