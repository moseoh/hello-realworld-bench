#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <result-dir>" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

RESULT_DIR="$1"

echo "Result directory: $RESULT_DIR"
echo "Files:"
find "$RESULT_DIR" -maxdepth 1 -type f -print | sort
