FROM eclipse-temurin:25-jdk AS builder

WORKDIR /workspace
COPY gradlew gradlew.bat settings.gradle build.gradle ./
COPY gradle ./gradle
COPY src ./src
RUN ./gradlew clean bootJar --no-daemon

FROM eclipse-temurin:25-jre

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /workspace/build/libs/*.jar /app/app.jar

EXPOSE 8080

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
