IMPLEMENTATION ?= java/spring-boot
SCENARIO ?= ping-api
VARIANT ?=
LOAD_PROFILE ?=
ENVIRONMENT_PROFILE ?=
MEASUREMENT_PROTOCOL ?=
BUILD_PROFILE ?=
IMAGE_DISTRIBUTION ?= push
TARGET_IMAGE ?=
TARGET_IMAGE_ARCHIVE ?=
RUN_SET_DIR ?=
DATASET_DIR ?=
SOURCE_COMMIT ?=
WORKFLOW_URL ?=
RAW_ARTIFACT_URL ?=
RAW_ARTIFACT_SHA256 ?=

.PHONY: run run-set build-set publish summarize summarize-json summarize-latest summarize-latest-json validate-contracts test-runner test-spring test-quarkus dashboard-dev dashboard-check check

run:
	PYTHONPATH=runner uv run --project runner python -m hrw_runner $(IMPLEMENTATION) $(SCENARIO) $(if $(strip $(VARIANT)),$(VARIANT)) $(if $(strip $(LOAD_PROFILE)),--load-profile $(LOAD_PROFILE)) $(if $(strip $(ENVIRONMENT_PROFILE)),--environment-profile $(ENVIRONMENT_PROFILE)) $(if $(strip $(MEASUREMENT_PROTOCOL)),--measurement-protocol $(MEASUREMENT_PROTOCOL)) $(if $(strip $(BUILD_PROFILE)),--build-profile $(BUILD_PROFILE))

run-set:
	HRW_IMAGE_DISTRIBUTION=$(IMAGE_DISTRIBUTION) HRW_TARGET_IMAGE=$(TARGET_IMAGE) HRW_TARGET_IMAGE_ARCHIVE=$(TARGET_IMAGE_ARCHIVE) PYTHONPATH=runner uv run --project runner python -m hrw_runner run-set $(IMPLEMENTATION) $(SCENARIO) $(if $(strip $(VARIANT)),$(VARIANT)) $(if $(strip $(LOAD_PROFILE)),--load-profile $(LOAD_PROFILE)) $(if $(strip $(ENVIRONMENT_PROFILE)),--environment-profile $(ENVIRONMENT_PROFILE)) $(if $(strip $(MEASUREMENT_PROTOCOL)),--measurement-protocol $(MEASUREMENT_PROTOCOL)) $(if $(strip $(BUILD_PROFILE)),--build-profile $(BUILD_PROFILE))

build-set:
	PYTHONPATH=runner uv run --project runner python -m hrw_runner build-set $(IMPLEMENTATION) $(if $(strip $(VARIANT)),$(VARIANT)) --environment-profile home-build-v1 --measurement-protocol official-build-v1 --build-profile official-gradle-docker-v1

publish:
	@test -n "$(RUN_SET_DIR)" -a -n "$(DATASET_DIR)" -a -n "$(SOURCE_COMMIT)"
	PYTHONPATH=runner uv run --project runner python -m hrw_runner publish $(RUN_SET_DIR) $(DATASET_DIR) --source-commit $(SOURCE_COMMIT) $(if $(strip $(WORKFLOW_URL)),--workflow-url $(WORKFLOW_URL)) $(if $(strip $(RAW_ARTIFACT_URL)),--raw-artifact-url $(RAW_ARTIFACT_URL) --raw-artifact-sha256 $(RAW_ARTIFACT_SHA256))

summarize:
	@PYTHONPATH=runner uv run --project runner python -m hrw_runner summarize

summarize-json:
	@PYTHONPATH=runner uv run --project runner python -m hrw_runner summarize --json

summarize-latest:
	@PYTHONPATH=runner uv run --project runner python -m hrw_runner summarize --latest-only

summarize-latest-json:
	@PYTHONPATH=runner uv run --project runner python -m hrw_runner summarize --latest-only --json

validate-contracts:
	@PYTHONPATH=runner uv run --project runner python -m hrw_runner validate

test-runner:
	PYTHONPATH=runner uv run --project runner python -m unittest discover -s runner/tests

test-spring:
	docker run --rm -u "$$(id -u):$$(id -g)" -e GRADLE_USER_HOME=/workspace/.gradle-cache -v "$$PWD/implementations/java/spring-boot:/workspace" -w /workspace eclipse-temurin:25-jdk ./gradlew test --no-daemon

test-quarkus:
	cd implementations/java/quarkus && ./gradlew test --no-daemon

dashboard-dev:
	cd dashboard && npm run dev

dashboard-check:
	cd dashboard && npm test && npm run lint && npm run build

check: validate-contracts test-runner test-spring test-quarkus dashboard-check
