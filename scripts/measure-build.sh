#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <implementation> <result-dir> <image-tag>" >&2
}

if [[ $# -ne 3 ]]; then
  usage
  exit 2
fi

IMPLEMENTATION="$1"
RESULT_DIR="$2"
IMAGE_TAG="$3"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/implementations/$IMPLEMENTATION"

if [[ "$IMPLEMENTATION" != "spring-boot" ]]; then
  echo "Unsupported implementation: $IMPLEMENTATION" >&2
  exit 2
fi

now_ms() {
  perl -MTime::HiRes=time -e 'printf "%.0f\n", time() * 1000'
}

measure_ms() {
  local start
  local end
  start="$(now_ms)"
  "$@" >&2
  end="$(now_ms)"
  echo $((end - start))
}

run_gradle_build() {
  if [[ -x "$APP_DIR/gradlew" ]]; then
    (cd "$APP_DIR" && ./gradlew clean build --no-daemon)
  elif command -v gradle >/dev/null 2>&1; then
    (cd "$APP_DIR" && gradle clean build --no-daemon)
  else
    docker run --rm \
      -u "$(id -u):$(id -g)" \
      -e GRADLE_USER_HOME=/workspace/.gradle-cache \
      -v "$APP_DIR:/workspace" \
      -w /workspace \
      gradle:8.10.2-jdk21 \
      gradle clean build --no-daemon
  fi
}

mkdir -p "$RESULT_DIR"

CLEAN_BUILD_MS="$(measure_ms run_gradle_build)"
DOCKER_BUILD_MS="$(measure_ms docker build -t "$IMAGE_TAG" "$APP_DIR")"
IMAGE_SIZE_BYTES="$(docker image inspect "$IMAGE_TAG" --format '{{.Size}}')"
IMAGE_SIZE_MB="$(awk "BEGIN { printf \"%.2f\", $IMAGE_SIZE_BYTES / 1024 / 1024 }")"

cat > "$RESULT_DIR/build.json" <<JSON
{
  "clean_build_ms": $CLEAN_BUILD_MS,
  "docker_build_ms": $DOCKER_BUILD_MS,
  "image_size_mb": $IMAGE_SIZE_MB
}
JSON
