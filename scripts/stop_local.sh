#!/usr/bin/env bash
set -Eeuo pipefail

# Get project root (parent of scripts directory)
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="$ROOT_DIR/.runtime/pids"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
IOPAINT_PORT="${IOPAINT_PORT:-8090}"
FORCE_PORT_KILL="${FORCE_PORT_KILL:-0}"

function log() {
  echo "[INFO] $*"
}

function read_pid() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 0
  tr -dc '0-9' < "$pid_file"
}

function is_pid_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

function stop_from_pid_file() {
  local name="$1"
  local pid_file="$2"
  local pid
  pid="$(read_pid "$pid_file")"

  if ! is_pid_running "$pid"; then
    rm -f "$pid_file"
    log "$name is not running."
    return
  fi

  log "Stopping $name (PID=$pid)..."
  kill "$pid" >/dev/null 2>&1 || true

  for _ in {1..10}; do
    if ! is_pid_running "$pid"; then
      rm -f "$pid_file"
      log "$name stopped."
      return
    fi
    sleep 1
  done

  log "$name did not exit in time, force killing..."
  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$pid_file"
}

function kill_port_fallback() {
  local port="$1"
  if ! command -v lsof >/dev/null 2>&1; then
    return
  fi
  local pids
  pids="$(lsof -ti "tcp:${port}" || true)"
  [[ -n "$pids" ]] || return 0
  log "Port $port still in use, killing remaining process(es): $pids"
  kill $pids >/dev/null 2>&1 || true
}

mkdir -p "$PID_DIR"

stop_from_pid_file "Backend" "$PID_DIR/backend.pid"
stop_from_pid_file "Frontend" "$PID_DIR/frontend.pid"
stop_from_pid_file "IOPaint" "$PID_DIR/iopaint.pid"

if [[ "$FORCE_PORT_KILL" == "1" ]]; then
  kill_port_fallback "$BACKEND_PORT"
  kill_port_fallback "$FRONTEND_PORT"
  kill_port_fallback "$IOPAINT_PORT"
fi

echo
echo "All local services are stopped."
