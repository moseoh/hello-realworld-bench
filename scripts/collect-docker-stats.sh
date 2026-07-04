#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <result-dir> [container-name]" >&2
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit 2
fi

RESULT_DIR="$1"
CONTAINER_NAME="${2:-hrw-target}"
mkdir -p "$RESULT_DIR"

if docker stats --no-stream --format '{{json .}}' "$CONTAINER_NAME" > "$RESULT_DIR/docker-stats.json"; then
  if [[ ! -s "$RESULT_DIR/docker-stats.json" ]]; then
    echo '{"error":"docker stats returned no data"}' > "$RESULT_DIR/docker-stats.json"
  fi
else
  echo '{"error":"docker stats failed"}' > "$RESULT_DIR/docker-stats.json"
fi
