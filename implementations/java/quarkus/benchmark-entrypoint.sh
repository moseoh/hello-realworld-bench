#!/bin/sh
set -eu

started_ns="$(date +%s%N)"
started="$((started_ns / 1000000))"
printf 'HRW_ENTRYPOINT_PRE_EXEC_EPOCH_MS=%s\n' "$started"
exec java -jar /app/quarkus-run.jar
