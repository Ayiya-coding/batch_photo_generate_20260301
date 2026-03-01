#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
PID_DIR="$RUNTIME_DIR/pids"
LOG_DIR="$RUNTIME_DIR/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE="$ROOT_DIR/.env.example"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
IOPAINT_PORT="${IOPAINT_PORT:-8090}"

USE_IOPAINT="${USE_IOPAINT:-auto}" # auto | 1 | 0
NO_INSTALL=0

BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"
IOPAINT_PID_FILE="$PID_DIR/iopaint.pid"

BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
IOPAINT_LOG="$LOG_DIR/iopaint.log"

function usage() {
  cat <<'EOF'
Usage: ./start_local.sh [options]

Options:
  --with-iopaint      Force start local IOPaint service.
  --without-iopaint   Do not start IOPaint service.
  --no-install        Skip dependency installation.
  -h, --help          Show this help.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --with-iopaint) USE_IOPAINT=1 ;;
    --without-iopaint) USE_IOPAINT=0 ;;
    --no-install) NO_INSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[ERROR] Unknown option: $arg"
      usage
      exit 1
      ;;
  esac
done

function log() {
  echo "[INFO] $*"
}

function warn() {
  echo "[WARN] $*" >&2
}

function die() {
  echo "[ERROR] $*" >&2
  exit 1
}

function need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

function is_pid_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

function read_pid() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 0
  tr -dc '0-9' < "$pid_file"
}

function maybe_skip_running() {
  local name="$1"
  local pid_file="$2"
  local pid
  pid="$(read_pid "$pid_file")"
  if is_pid_running "$pid"; then
    log "$name already running (PID=$pid)"
    return 0
  fi
  rm -f "$pid_file"
  return 1
}

function wait_http() {
  local name="$1"
  local url="$2"
  local timeout_sec="$3"
  local checker="$ROOT_DIR/.venv/bin/python"
  if [[ ! -x "$checker" ]]; then
    checker="$(command -v python3)"
  fi

  if "$checker" - "$url" "$timeout_sec" <<'PY'
import sys
import time
import urllib.request

url = sys.argv[1]
timeout = int(sys.argv[2])
deadline = time.time() + timeout

while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            if 200 <= resp.status < 500:
                sys.exit(0)
    except Exception:
        pass
    time.sleep(1)

sys.exit(1)
PY
  then
    log "$name is ready: $url"
  else
    return 1
  fi
}

function read_env_value() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  local line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%\"}"
  line="${line#\"}"
  line="${line%\'}"
  line="${line#\'}"
  printf "%s" "$line"
}

function is_real_value() {
  local value="${1:-}"
  [[ -n "$value" ]] || return 1
  case "$value" in
    your_*|YOUR_*|changeme|CHANGEME|null|NULL|none|NONE)
      return 1
      ;;
  esac
  return 0
}

function ensure_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    return
  fi
  if [[ -f "$ENV_EXAMPLE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    warn ".env not found. Created from .env.example. Please fill API keys in .env."
  else
    die ".env and .env.example are both missing."
  fi
}

function apply_access_keys_if_present() {
  local access_file="$ROOT_DIR/可行性分析/AccessKey.txt"
  local helper="$ROOT_DIR/scripts/apply_access_keys.py"
  if [[ ! -f "$helper" ]]; then
    return
  fi
  if [[ ! -f "$access_file" ]]; then
    return
  fi

  log "Applying keys from 可行性分析/AccessKey.txt to .env ..."
  python3 "$helper" \
    --access-key-file "$access_file" \
    --env-file "$ENV_FILE" \
    --env-example "$ENV_EXAMPLE" \
    --quiet || warn "apply_access_keys.py failed, continue with existing .env"
}

function normalize_debug_env() {
  local current="${DEBUG:-}"
  [[ -n "$current" ]] || return 0
  local normalized
  normalized="$(echo "$current" | tr '[:upper:]' '[:lower:]' | xargs)"
  case "$normalized" in
    1|0|true|false|yes|no|on|off|debug|dev|development|release|prod|production)
      return 0
      ;;
    *)
      warn "Detected unsupported DEBUG value from shell env: '$current'. Fallback to DEBUG=false for local startup."
      export DEBUG=false
      ;;
  esac
}

function ensure_backend_deps() {
  need_cmd python3
  if [[ ! -x "$ROOT_DIR/.venv/bin/python" && "$NO_INSTALL" -eq 1 ]]; then
    die "--no-install is set but .venv is missing. Run without --no-install first."
  fi

  if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
    log "Creating backend virtualenv (.venv)..."
    python3 -m venv "$ROOT_DIR/.venv"
  fi

  if [[ "$NO_INSTALL" -eq 0 ]]; then
    log "Installing backend dependencies..."
    "$ROOT_DIR/.venv/bin/python" -m pip install --upgrade pip >/dev/null
    "$ROOT_DIR/.venv/bin/pip" install -r "$ROOT_DIR/backend/requirements.txt"
  else
    "$ROOT_DIR/.venv/bin/python" - <<'PY' >/dev/null 2>&1 || {
import sqlalchemy  # noqa: F401
import fastapi  # noqa: F401
import uvicorn  # noqa: F401
PY
      die "--no-install is set but backend dependencies are incomplete. Run without --no-install first."
    }
  fi
}

function ensure_frontend_deps() {
  need_cmd npm
  if [[ "$NO_INSTALL" -eq 1 && ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
    die "--no-install is set but frontend/node_modules is missing. Run without --no-install first."
  fi

  if [[ "$NO_INSTALL" -eq 0 && ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
    log "Installing frontend dependencies..."
    (cd "$ROOT_DIR/frontend" && npm ci)
  fi
}

function ensure_iopaint_deps() {
  need_cmd python3
  if [[ ! -x "$ROOT_DIR/.venv-iopaint/bin/python" && "$NO_INSTALL" -eq 1 ]]; then
    die "--no-install is set but .venv-iopaint is missing. Run without --no-install first."
  fi

  if [[ ! -x "$ROOT_DIR/.venv-iopaint/bin/python" ]]; then
    log "Creating iopaint virtualenv (.venv-iopaint)..."
    python3 -m venv "$ROOT_DIR/.venv-iopaint"
  fi

  if [[ "$NO_INSTALL" -eq 0 ]]; then
    log "Installing iopaint dependencies..."
    "$ROOT_DIR/.venv-iopaint/bin/python" -m pip install --upgrade pip >/dev/null
    "$ROOT_DIR/.venv-iopaint/bin/pip" install -r "$ROOT_DIR/iopaint_service/requirements.txt"
  fi
}

function start_backend() {
  if maybe_skip_running "Backend" "$BACKEND_PID_FILE"; then
    return
  fi
  log "Starting backend on port $BACKEND_PORT..."
  (
    cd "$ROOT_DIR/backend"
    nohup "$ROOT_DIR/.venv/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" >"$BACKEND_LOG" 2>&1 &
    echo "$!" > "$BACKEND_PID_FILE"
  )
  local pid
  pid="$(read_pid "$BACKEND_PID_FILE")"
  is_pid_running "$pid" || die "Backend failed to start. Check $BACKEND_LOG"
}

function start_frontend() {
  if maybe_skip_running "Frontend" "$FRONTEND_PID_FILE"; then
    return
  fi
  log "Starting frontend on port $FRONTEND_PORT..."
  (
    cd "$ROOT_DIR/frontend"
    nohup npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" >"$FRONTEND_LOG" 2>&1 &
    echo "$!" > "$FRONTEND_PID_FILE"
  )
  local pid
  pid="$(read_pid "$FRONTEND_PID_FILE")"
  is_pid_running "$pid" || die "Frontend failed to start. Check $FRONTEND_LOG"
}

function start_iopaint() {
  if maybe_skip_running "IOPaint" "$IOPAINT_PID_FILE"; then
    return
  fi
  log "Starting iopaint on port $IOPAINT_PORT (CPU mode)..."
  (
    cd "$ROOT_DIR/iopaint_service"
    nohup "$ROOT_DIR/.venv-iopaint/bin/python" -m iopaint start --model=lama --device=cpu --port="$IOPAINT_PORT" >"$IOPAINT_LOG" 2>&1 &
    echo "$!" > "$IOPAINT_PID_FILE"
  )
  local pid
  pid="$(read_pid "$IOPAINT_PID_FILE")"
  is_pid_running "$pid" || die "IOPaint failed to start. Check $IOPAINT_LOG"
}

function decide_iopaint_mode() {
  if [[ "$USE_IOPAINT" == "1" || "$USE_IOPAINT" == "0" ]]; then
    return
  fi

  local volc_ak volc_sk
  volc_ak="$(read_env_value VOLC_ACCESS_KEY_ID)"
  volc_sk="$(read_env_value VOLC_SECRET_ACCESS_KEY)"

  if is_real_value "$volc_ak" && is_real_value "$volc_sk"; then
    USE_IOPAINT=0
    log "Detected Volc credentials in .env, skip local iopaint."
  else
    USE_IOPAINT=1
    log "Volc credentials not found, local iopaint will be started."
  fi
}

ensure_env_file
apply_access_keys_if_present
normalize_debug_env
ensure_backend_deps
ensure_frontend_deps
decide_iopaint_mode

if [[ "$USE_IOPAINT" == "1" ]]; then
  ensure_iopaint_deps
fi

start_backend
wait_http "Backend" "http://127.0.0.1:${BACKEND_PORT}/health" 60 || {
  tail -n 50 "$BACKEND_LOG" || true
  die "Backend health check failed."
}

start_frontend
wait_http "Frontend" "http://127.0.0.1:${FRONTEND_PORT}" 60 || {
  tail -n 50 "$FRONTEND_LOG" || true
  die "Frontend health check failed."
}

if [[ "$USE_IOPAINT" == "1" ]]; then
  start_iopaint
  wait_http "IOPaint" "http://127.0.0.1:${IOPAINT_PORT}/api/v1/server-config" 90 || {
    tail -n 50 "$IOPAINT_LOG" || true
    die "IOPaint health check failed."
  }
fi

echo
echo "==================== STARTED ===================="
echo "Frontend : http://localhost:${FRONTEND_PORT}"
echo "Backend  : http://localhost:${BACKEND_PORT}"
if [[ "$USE_IOPAINT" == "1" ]]; then
  echo "IOPaint  : http://localhost:${IOPAINT_PORT}"
fi
echo "Logs     : $LOG_DIR"
echo "Stop all : ./stop_local.sh"
echo "================================================="
