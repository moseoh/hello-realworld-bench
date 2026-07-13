FROM eclipse-temurin:25-jre

LABEL org.opencontainers.image.source="https://github.com/moseoh/hello-realworld-bench"

WORKDIR /app
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app
COPY --chown=10001:10001 build/quarkus-app/ /app/
USER 10001

EXPOSE 8080

ENTRYPOINT ["java", "-jar", "/app/quarkus-run.jar"]
