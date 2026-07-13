# Scenarios

Scenarios are service-pattern based. They are not named after a single technology.

Each scenario lives under `scenarios/<scenario-name>/` and should include:

```text
README.md
scenario.yaml
scenario-local assets, such as k6.js when needed
```

The runner reads `scenario.yaml`. The scenario `README.md` explains the benchmark question, scope, dependencies, variants, and metrics for humans.

## Scenario Contract

Every scenario README must define:

- name
- question
- what this measures
- what this does not measure
- dependencies
- variants
- metrics

Scenario names should describe service patterns, not technologies. For example, prefer `transactional-command-api` over `db-write`.

## Current Scenarios

- [`ping-api`](../scenarios/ping-api/README.md): runner validation scenario for build, startup, k6, Docker stats, and result JSON automation.
- [`cold-start-api`](../scenarios/cold-start-api/README.md): startup-focused scenario for first successful business endpoint response after app start.
- [`transactional-command-api`](../scenarios/transactional-command-api/README.md): stateful command API scenario with PostgreSQL, JPA, one transaction, and an outbox insert.
- [`read-heavy-query-api`](../scenarios/read-heavy-query-api/README.md): immutable PostgreSQL catalog queries with fixed selectivity, keyset pagination, and bounded responses.
- [`io-aggregation-api`](../scenarios/io-aggregation-api/README.md): HTTP fan-out scenario with a mock upstream service and response aggregation.
- [`io-aggregation-timeout-api`](../scenarios/io-aggregation-timeout-api/README.md): HTTP fan-out scenario where one slow upstream should time out and fall back.

## Future Scenario Notes

Future scenarios may include:

- `event-processing-worker`
- `file-streaming-api`
- `observability-overhead-api`
