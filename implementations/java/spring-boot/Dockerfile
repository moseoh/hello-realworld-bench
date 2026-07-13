FROM eclipse-temurin:25-jre@sha256:d0eb1b9018b3044da1b7346f39e945f71095749853d69a3aa16b8c99dad9bb45

LABEL org.opencontainers.image.source="https://github.com/moseoh/hello-realworld-bench"

WORKDIR /app
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app
COPY --chown=10001:10001 build/libs/*.jar /app/app.jar
COPY --chmod=755 --chown=10001:10001 benchmark-entrypoint.sh /app/benchmark-entrypoint.sh
USER 10001

EXPOSE 8080

ENTRYPOINT ["/app/benchmark-entrypoint.sh"]
