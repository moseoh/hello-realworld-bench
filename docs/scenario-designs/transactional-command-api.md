# transactional-command-api Design

## Status

Design draft for Phase 1. This is not a runnable scenario yet.

## Question

How does a runtime behave when handling a realistic command request that performs validation, domain logic, one database transaction, and an outbox insert?

## Scenario Role

`transactional-command-api` is the first stateful service-pattern benchmark. It should move the project beyond runner validation and startup timing without mixing unrelated bottlenecks.

The scenario should model a common backend write path:

```text
HTTP command
  -> request validation
  -> domain logic
  -> PostgreSQL transaction
  -> primary table insert/update
  -> outbox table insert
  -> response
```

It should not add Redis, Kafka, external HTTP calls, file IO, OpenTelemetry, or Kubernetes in the first version.

## Endpoint Contract

Initial endpoint:

```http
POST /orders
Content-Type: application/json
```

Request:

```json
{
  "customerId": "customer-123",
  "items": [
    {
      "sku": "SKU-001",
      "quantity": 2,
      "unitPriceCents": 1299
    }
  ]
}
```

Successful response:

```json
{
  "orderId": "generated-id",
  "status": "accepted",
  "totalCents": 2598
}
```

The response should be deterministic in shape but not in generated IDs.

## Database Contract

Use PostgreSQL through Docker Compose.

Minimum tables:

```sql
orders(
  id text primary key,
  customer_id text not null,
  status text not null,
  total_cents integer not null,
  created_at timestamptz not null
)
```

```sql
order_items(
  id text primary key,
  order_id text not null references orders(id),
  sku text not null,
  quantity integer not null,
  unit_price_cents integer not null
)
```

```sql
outbox_events(
  id text primary key,
  aggregate_type text not null,
  aggregate_id text not null,
  event_type text not null,
  payload_json jsonb not null,
  created_at timestamptz not null,
  published_at timestamptz null
)
```

The order insert, item inserts, and outbox insert must happen in the same transaction.

## What This Measures

- HTTP request parsing and validation
- domain calculation for a small command payload
- database connection pool behavior
- one PostgreSQL transaction per request
- insert workload across multiple tables
- outbox write overhead
- tail latency under write contention
- error rate under constrained CPU and memory

## What This Does Not Measure

- cache behavior
- read-heavy query performance
- Kafka or message broker throughput
- outbox publishing
- distributed transactions
- external service aggregation
- file streaming
- observability overhead
- Kubernetes scheduling or networking

## Dependencies

- target HTTP service
- PostgreSQL container

No other service dependency should be included in the first version.

## Load Profile

Initial profile should stay short and practical:

```yaml
load:
  tool: k6
  script: "scenarios/transactional-command-api/k6.js"
  warmup_duration: "15s"
  test_duration: "45s"
  vus: 25
```

The lower VU count is intentional. This scenario performs writes and should avoid turning the first version into a pure database saturation test.

## Metrics

Collect the existing common metrics:

- build time
- Docker image build time
- startup ready time
- first request latency
- request rate
- p50 latency
- p95 latency
- p99 latency
- error rate
- CPU snapshot
- memory snapshot

Add scenario-specific raw counters later only if they can be collected consistently across implementations.

Candidate future counters:

- successful order count
- failed command count
- database row counts after the run
- transaction rollback count

These should not be required for the first implementation unless they are cheap and reliable.

## Implementation Notes

Spring Boot should use boring framework defaults:

- Spring Web MVC
- `JdbcTemplate`
- PostgreSQL driver
- HikariCP through Spring Boot defaults
- Flyway for schema migrations

Avoid JPA for the first version unless there is a clear reason. The scenario should measure the command pattern, not ORM mapping complexity.

Generated IDs should be UUID strings created by the application. This keeps the endpoint contract portable across implementations and avoids depending on PostgreSQL-specific ID generation for the first version.

The runner should use a fresh Docker Compose volume per run through the existing cleanup flow. The database should start empty for every benchmark run.

The k6 script should use a fixed bounded set of customer IDs and SKUs with randomized selection. This prevents every request from being identical while keeping the dataset predictable and implementation-neutral.

## Fairness Rules

- Same endpoint contract across implementations
- Same request and response structure
- Same PostgreSQL schema
- Same transaction semantics
- Same outbox event semantics
- Same Docker Compose resource constraints where practical
- Same k6 load script
- No implementation may skip the outbox insert
- No implementation may precompute responses or bypass persistence

## Result Interpretation

Results should be phrased as trade-offs under the exact scenario conditions. For example:

```text
Under transactional-command-api with PostgreSQL on the same Docker Compose host and 25 VUs, implementation A showed lower p95 latency while implementation B used less memory.
```

Do not use this scenario to claim universal database or framework performance.

## Implementation Decisions

- Use Flyway for schema setup.
- Use application-generated UUID strings for IDs.
- Start each benchmark run with a fresh Compose volume.
- Use a fixed bounded set of customer IDs and SKUs in k6, selected randomly per request.
- Use Spring `JdbcTemplate` for the first Spring Boot implementation.
