# HTTP Baseline

HTTP scenarios must document both sides of the HTTP path:

- the target implementation HTTP client
- the mock upstream service

These settings are part of the benchmark contract. New implementations should use equivalent settings unless a scenario explicitly defines a variant.

## Target Outbound HTTP Client

For `io-aggregation-api` and `io-aggregation-timeout-api`, each implementation should use a production-style HTTP client with connection pooling.

Baseline settings:

| Setting | Value |
| --- | --- |
| Connection pooling | enabled |
| Max total connections | `128` |
| Max connections per upstream route | `128` |
| Connect timeout | `500ms` |
| Connection acquisition timeout | `500ms` |
| Response timeout, `io-aggregation-api` | `1000ms` |
| Response timeout, `io-aggregation-timeout-api` | `350ms` |
| Retries | disabled unless a scenario explicitly measures retries |
| Circuit breaker | disabled unless a scenario explicitly measures circuit breakers |

The Spring Boot implementation uses Apache HttpClient 5 through Spring `RestClient`.

Equivalent settings for future implementations do not need identical configuration names, but they must preserve the same behavior: pooled connections, the same effective timeout values, no retry behavior, and no circuit breaker behavior.

## Mock Upstream

The MVP mock upstream is WireMock running in Docker Compose.

Baseline settings:

| Setting | Value |
| --- | --- |
| Image | `wiremock/wiremock:3.13.2` |
| CPU limit | `1.0` |
| Memory limit | `512m` |
| Request journal | disabled |
| Container threads | `32` |
| Jetty accept queue size | `200` |

The request journal is disabled because these scenarios do not verify received requests through WireMock admin APIs, and recording every request adds memory and synchronization overhead that is not part of the service pattern being measured.

## Scenario Delays

`io-aggregation-api` uses fixed upstream delays to model normal I/O aggregation.

`io-aggregation-timeout-api` keeps the fast upstreams below the target response timeout and sets the inventory upstream above the timeout so the target returns a fallback response.

Do not change upstream delay values to improve a runtime result. Delay changes define a new scenario condition and should be documented as a separate scenario or variant.
