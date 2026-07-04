#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <implementation> <scenario>" >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

IMPLEMENTATION="$1"
SCENARIO="$2"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIO_DIR="$ROOT_DIR/scenarios/$SCENARIO"
APP_DIR="$ROOT_DIR/implementations/$IMPLEMENTATION"
COMPOSE_FILES=(-f "$ROOT_DIR/infra/docker-compose.base.yml" -f "$ROOT_DIR/infra/docker-compose.$IMPLEMENTATION.yml")
IMAGE_TAG="hello-realworld/$IMPLEMENTATION:local"
BASE_URL="http://localhost:8080"
HEALTH_PATH="/actuator/health"
ENDPOINT="/ping"
WARMUP_DURATION="10s"
TEST_DURATION="30s"
VUS="50"

if [[ "$IMPLEMENTATION" != "spring-boot" ]]; then
  echo "Unsupported implementation: $IMPLEMENTATION" >&2
  exit 2
fi

if [[ "$SCENARIO" != "ping-api" ]]; then
  echo "Unsupported scenario: $SCENARIO" >&2
  exit 2
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "Implementation directory not found: $APP_DIR" >&2
  exit 2
fi

if [[ ! -f "$SCENARIO_DIR/k6.js" ]]; then
  echo "Scenario k6 script not found: $SCENARIO_DIR/k6.js" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required." >&2
  exit 2
fi

timestamp() {
  date -u +"%Y-%m-%dT%H-%M-%S"
}

json_string() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

extract_json_number() {
  local file="$1"
  local key="$2"
  sed -n "s/.*\"$key\"[[:space:]]*:[[:space:]]*\\([^,}]*\\).*/\\1/p" "$file" | head -n 1
}

container_path() {
  local path="$1"
  printf '/work%s' "${path#"$ROOT_DIR"}"
}

run_k6() {
  local duration="$1"
  local summary_file="$2"
  local script_file="$3"

  if command -v k6 >/dev/null 2>&1; then
    BASE_URL="$BASE_URL" VUS="$VUS" DURATION="$duration" \
      k6 run --summary-export "$summary_file" "$script_file"
  else
    docker run --rm \
      --add-host host.docker.internal:host-gateway \
      -e BASE_URL="http://host.docker.internal:8080" \
      -e VUS="$VUS" \
      -e DURATION="$duration" \
      -v "$ROOT_DIR:/work" \
      -w /work \
      grafana/k6:0.54.0 \
      run --summary-export "$(container_path "$summary_file")" "$(container_path "$script_file")"
  fi
}

RUN_ID="$(timestamp)_${IMPLEMENTATION}_${SCENARIO}"
RESULT_DIR="$ROOT_DIR/results/$RUN_ID"
RUN_LOG="$RESULT_DIR/run.log"
mkdir -p "$RESULT_DIR"
touch "$RUN_LOG"

exec > >(tee -a "$RUN_LOG") 2>&1

cleanup() {
  docker compose "${COMPOSE_FILES[@]}" logs target > "$RESULT_DIR/target.log" 2>&1 || true
  docker compose "${COMPOSE_FILES[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

OS_NAME="$(uname -s 2>/dev/null || echo unknown)"
CPU_NAME="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || lscpu 2>/dev/null | sed -n 's/^Model name:[[:space:]]*//p' | head -n 1 || echo unknown)"
MEMORY_GB="$(awk 'BEGIN { printf "unknown" }')"
if command -v sysctl >/dev/null 2>&1; then
  MEMORY_BYTES="$(sysctl -n hw.memsize 2>/dev/null || true)"
  if [[ -n "${MEMORY_BYTES:-}" ]]; then
    MEMORY_GB="$(awk "BEGIN { printf \"%.2f\", $MEMORY_BYTES / 1024 / 1024 / 1024 }")"
  fi
fi

cat > "$RESULT_DIR/metadata.json" <<JSON
{
  "run_id": "$(json_string "$RUN_ID")",
  "project": "hello-realworld-bench",
  "scenario": "$(json_string "$SCENARIO")",
  "implementation": "$(json_string "$IMPLEMENTATION")",
  "runtime": {
    "language": "java",
    "java_version": "21",
    "framework": "spring-boot",
    "virtual_threads": false,
    "otel": false
  },
  "environment": {
    "os": "$(json_string "$OS_NAME")",
    "cpu": "$(json_string "$CPU_NAME")",
    "memory_gb": "$(json_string "$MEMORY_GB")",
    "docker": true,
    "load_generator": "same-host"
  }
}
JSON

echo "Run ID: $RUN_ID"
echo "Cleaning previous containers..."
docker compose "${COMPOSE_FILES[@]}" down -v --remove-orphans || true

echo "Measuring build..."
"$ROOT_DIR/scripts/measure-build.sh" "$IMPLEMENTATION" "$RESULT_DIR" "$IMAGE_TAG"

echo "Measuring startup..."
"$ROOT_DIR/scripts/measure-startup.sh" "$IMPLEMENTATION" "$RESULT_DIR" "$BASE_URL" "$HEALTH_PATH" "$ENDPOINT"

echo "Running warmup..."
run_k6 "$WARMUP_DURATION" "$RESULT_DIR/k6-warmup-summary.json" "$SCENARIO_DIR/k6.js"

echo "Running benchmark..."
run_k6 "$TEST_DURATION" "$RESULT_DIR/k6-summary.json" "$SCENARIO_DIR/k6.js"

echo "Collecting Docker stats..."
"$ROOT_DIR/scripts/collect-docker-stats.sh" "$RESULT_DIR" hrw-target

BUILD_CLEAN_MS="$(extract_json_number "$RESULT_DIR/build.json" clean_build_ms || true)"
BUILD_DOCKER_MS="$(extract_json_number "$RESULT_DIR/build.json" docker_build_ms || true)"
IMAGE_SIZE_MB="$(extract_json_number "$RESULT_DIR/build.json" image_size_mb || true)"
STARTUP_READY_MS="$(extract_json_number "$RESULT_DIR/startup.json" ready_ms || true)"
FIRST_REQUEST_MS="$(extract_json_number "$RESULT_DIR/startup.json" first_request_ms || true)"

cat > "$RESULT_DIR/result.json" <<JSON
{
  "run_id": "$(json_string "$RUN_ID")",
  "project": "hello-realworld-bench",
  "scenario": "$(json_string "$SCENARIO")",
  "implementation": "$(json_string "$IMPLEMENTATION")",
  "runtime": {
    "language": "java",
    "java_version": "21",
    "framework": "spring-boot",
    "virtual_threads": false,
    "otel": false
  },
  "environment": {
    "os": "$(json_string "$OS_NAME")",
    "cpu": "$(json_string "$CPU_NAME")",
    "memory_gb": "$(json_string "$MEMORY_GB")",
    "docker": true,
    "load_generator": "same-host"
  },
  "build": {
    "clean_build_ms": ${BUILD_CLEAN_MS:-null},
    "docker_build_ms": ${BUILD_DOCKER_MS:-null},
    "image_size_mb": ${IMAGE_SIZE_MB:-null}
  },
  "startup": {
    "ready_ms": ${STARTUP_READY_MS:-null},
    "first_request_ms": ${FIRST_REQUEST_MS:-null}
  },
  "runtime_metrics": {
    "rps": null,
    "p50_ms": null,
    "p95_ms": null,
    "p99_ms": null,
    "error_rate": null,
    "cpu": null,
    "memory": null
  }
}
JSON

echo "Result written to: $RESULT_DIR"
"$ROOT_DIR/scripts/summarize.sh" "$RESULT_DIR"
