#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PROFILE_ARGS=()
EXTRA_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --with-iopaint)
      PROFILE_ARGS+=(--profile iopaint)
      ;;
    *)
      EXTRA_ARGS+=("$arg")
      ;;
  esac
done

if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
  echo "[WARN] .env 不存在，已根据 .env.example 创建，请补充 API Key 后重新执行。"
fi

mkdir -p data models

CMD=(docker compose)
if ((${#PROFILE_ARGS[@]} > 0)); then
  CMD+=("${PROFILE_ARGS[@]}")
fi
CMD+=(up -d --build)
if ((${#EXTRA_ARGS[@]} > 0)); then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"

echo
echo "==================== DOCKER STARTED ===================="
echo "Frontend : http://localhost:3000"
echo "Backend  : http://localhost:8000"
if ((${#PROFILE_ARGS[@]} > 0)); then
  echo "IOPaint  : http://localhost:8090"
fi
echo "Stop all : ./stop_docker.sh"
echo "========================================================"
