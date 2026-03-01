#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
./start_local.sh "$@"

echo
read -r -n 1 -p "Press any key to close..."
echo
