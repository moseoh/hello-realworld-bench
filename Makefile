IMPLEMENTATION ?= java/spring-boot
SCENARIO ?= ping-api
VARIANT ?=
LOAD_PROFILE ?=
ENVIRONMENT_PROFILE ?=
MEASUREMENT_PROTOCOL ?=
BUILD_PROFILE ?=

.PHONY: run summarize summarize-json summarize-latest summarize-latest-json validate-contracts test-runner test-spring check

run:
	PYTHONPATH=runner uv run --project runner python -m hrw_runner $(IMPLEMENTATION) $(SCENARIO) $(if $(strip $(VARIANT)),$(VARIANT)) $(if $(strip $(LOAD_PROFILE)),--load-profile $(LOAD_PROFILE)) $(if $(strip $(ENVIRONMENT_PROFILE)),--environment-profile $(ENVIRONMENT_PROFILE)) $(if $(strip $(MEASUREMENT_PROTOCOL)),--measurement-protocol $(MEASUREMENT_PROTOCOL)) $(if $(strip $(BUILD_PROFILE)),--build-profile $(BUILD_PROFILE))

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

check: validate-contracts test-runner test-spring
