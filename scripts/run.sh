#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <implementation> <scenario> [variant]" >&2
  echo "Examples:" >&2
  echo "  $0 spring-boot ping-api" >&2
  echo "  $0 java/spring-boot ping-api jvm-java25" >&2
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage
  exit 2
fi

REQUESTED_IMPLEMENTATION="$1"
SCENARIO="$2"
VARIANT="${3:-jvm-java25}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIO_DIR="$ROOT_DIR/scenarios/$SCENARIO"
BASE_URL="http://localhost:8080"
HEALTH_PATH="/actuator/health"
ENDPOINT="/ping"
WARMUP_DURATION="10s"
TEST_DURATION="30s"
VUS="50"

case "$REQUESTED_IMPLEMENTATION" in
  spring-boot)
    IMPLEMENTATION="java/spring-boot"
    ;;
  java/spring-boot)
    IMPLEMENTATION="$REQUESTED_IMPLEMENTATION"
    ;;
  *)
    echo "Unsupported implementation: $REQUESTED_IMPLEMENTATION" >&2
    exit 2
    ;;
esac

LANGUAGE="${IMPLEMENTATION%%/*}"
FRAMEWORK="${IMPLEMENTATION#*/}"
APP_DIR="$ROOT_DIR/implementations/$IMPLEMENTATION"
VARIANT_FILE="$APP_DIR/variants/$VARIANT.yaml"
COMPOSE_PROFILE="$FRAMEWORK"
COMPOSE_FILES=(-f "$ROOT_DIR/infra/docker-compose.base.yml" -f "$ROOT_DIR/infra/docker-compose.$COMPOSE_PROFILE.yml")

if [[ "$IMPLEMENTATION" != "java/spring-boot" ]]; then
  echo "Unsupported implementation: $IMPLEMENTATION" >&2
  exit 2
fi

if [[ "$VARIANT" != "jvm-java25" ]]; then
  echo "Unsupported variant for $IMPLEMENTATION: $VARIANT" >&2
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

if [[ ! -f "$VARIANT_FILE" ]]; then
  echo "Variant file not found: $VARIANT_FILE" >&2
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

IMAGE_TAG="hello-realworld/${LANGUAGE}-${FRAMEWORK}-${VARIANT}:local"
RUN_ID="$(timestamp)_${LANGUAGE}_${FRAMEWORK}_${VARIANT}_${SCENARIO}"
RESULT_DIR="$ROOT_DIR/results/$LANGUAGE/$FRAMEWORK/$VARIANT/$SCENARIO/$RUN_ID"
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
  "variant": "$(json_string "$VARIANT")",
  "runtime": {
    "language": "$(json_string "$LANGUAGE")",
    "java_version": "25",
    "framework": "$(json_string "$FRAMEWORK")",
    "spring_boot_version": "4.1.0",
    "build_mode": "jvm",
    "native_image": false,
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
"$ROOT_DIR/scripts/measure-startup.sh" "$COMPOSE_PROFILE" "$RESULT_DIR" "$BASE_URL" "$HEALTH_PATH" "$ENDPOINT"

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
  "variant": "$(json_string "$VARIANT")",
  "runtime": {
    "language": "$(json_string "$LANGUAGE")",
    "java_version": "25",
    "framework": "$(json_string "$FRAMEWORK")",
    "spring_boot_version": "4.1.0",
    "build_mode": "jvm",
    "native_image": false,
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
