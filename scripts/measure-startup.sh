#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <compose-profile> <result-dir> <base-url> <health-path> <endpoint>" >&2
}

if [[ $# -ne 5 ]]; then
  usage
  exit 2
fi

COMPOSE_PROFILE="$1"
RESULT_DIR="$2"
BASE_URL="$3"
HEALTH_PATH="$4"
ENDPOINT="$5"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$COMPOSE_PROFILE" != "spring-boot" ]]; then
  echo "Unsupported compose profile: $COMPOSE_PROFILE" >&2
  exit 2
fi

now_ms() {
  perl -MTime::HiRes=time -e 'printf "%.0f\n", time() * 1000'
}

COMPOSE_FILES=(-f "$ROOT_DIR/infra/docker-compose.base.yml" -f "$ROOT_DIR/infra/docker-compose.$COMPOSE_PROFILE.yml")

mkdir -p "$RESULT_DIR"

START_MS="$(now_ms)"
docker compose "${COMPOSE_FILES[@]}" up -d target

READY_MS=""
for _ in $(seq 1 120); do
  if curl -fsS "$BASE_URL$HEALTH_PATH" >/dev/null 2>&1; then
    READY_MS="$(($(now_ms) - START_MS))"
    break
  fi
  sleep 1
done

if [[ -z "$READY_MS" ]]; then
  docker compose "${COMPOSE_FILES[@]}" logs target > "$RESULT_DIR/startup-failure.log" || true
  echo "Target did not become healthy within 120 seconds." >&2
  exit 1
fi

FIRST_REQUEST_SECONDS="$(curl -fsS -o /dev/null -w '%{time_total}' "$BASE_URL$ENDPOINT")"
FIRST_REQUEST_MS="$(awk "BEGIN { printf \"%.0f\", $FIRST_REQUEST_SECONDS * 1000 }")"

cat > "$RESULT_DIR/startup.json" <<JSON
{
  "ready_ms": $READY_MS,
  "first_request_ms": $FIRST_REQUEST_MS
}
JSON
