IMPLEMENTATION ?= java/spring-boot
SCENARIO ?= ping-api
VARIANT ?= jvm-java25

.PHONY: run test-runner test-spring check

run:
	PYTHONPATH=runner uv run --project runner python -m hrw_runner $(IMPLEMENTATION) $(SCENARIO) $(VARIANT)

test-runner:
	PYTHONPATH=runner uv run --project runner python -m unittest discover -s runner/tests

test-spring:
	docker run --rm -u "$$(id -u):$$(id -g)" -e GRADLE_USER_HOME=/workspace/.gradle-cache -v "$$PWD/implementations/java/spring-boot:/workspace" -w /workspace eclipse-temurin:25-jdk ./gradlew test --no-daemon

check: test-runner test-spring
