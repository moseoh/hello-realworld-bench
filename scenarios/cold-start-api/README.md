# cold-start-api

## Question

How long does a new application instance take to complete its first valid business response from a controlled image-entrypoint pre-exec boundary?

## Role

`cold-start-api` is a lifecycle evidence family, separate from service load tests. Local Docker Compose runs remain development evidence. The official profile runs five new target containers on the fixed home k3s node with an already-present immutable image.

The official start boundary is a millisecond timestamp emitted by the image entrypoint immediately before it calls `exec` for the application process. Both implementations use the same wrapper logic, while each image retains ownership of its application command. A native sidecar is armed before the target starts and polls `http://127.0.0.1:8080/ping`. The completion boundary is the end of the first response that is HTTP 200 and exactly matches the JSON object `{"message":"pong"}`. Service discovery, EndpointSlice propagation, image transfer, image pulling, and Pod scheduling are outside the measured interval.

The marker runs immediately before, but is not the kernel's exact `execve(2)` timestamp. The timestamp command completes before the interval; marker output and shell-to-JVM `exec` overhead are included. Results describe a new JVM process with warm node and image caches; they do not describe a machine boot.

The runner records node-background snapshots immediately before target creation and immediately after the first valid response. Run-level preflight and postflight checks surround the set. Kubelet sampling cannot resolve every transient inside a sub-second startup interval, so lifecycle evidence does not claim the sustained in-run noise coverage used by service tests.

This scenario does not model serverless platform cold starts.

Framework health endpoints are intentionally not used for this scenario because they can warm different parts of different runtimes before the first business endpoint request.

## What This Measures

- repeated entrypoint-pre-exec-to-first-valid-response timing for `/ping`
- latency of the first successful `/ping` request

## What This Does Not Measure

- sustained throughput
- warm request latency under load
- database performance
- serverless platform cold starts
- machine boot time
- cold filesystem page-cache behavior
- sustained resource usage

## Dependencies

- target HTTP service only

## Variants

- `first-success`: First successful business response after process start.

## Metrics

- entrypoint-pre-exec-to-first-valid-response samples
- first request latency samples
- min, median, and max entrypoint-pre-exec-to-first-valid-response time
- min, median, and max first request latency
- observer attempt count and boundary timestamps for audit
