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

CMD=(docker compose)
if ((${#PROFILE_ARGS[@]} > 0)); then
  CMD+=("${PROFILE_ARGS[@]}")
fi
CMD+=(down)
if ((${#EXTRA_ARGS[@]} > 0)); then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
