# AGENTS.md

Guidance for future AI coding agents working on Hello Real World Bench.

## Project Rules

- Keep the benchmark small and reproducible.
- For every implementation project, prefer the official provider generator or installer first, such as Spring Initializr for Spring Boot.
- Before adding or updating an implementation, search official provider/runtime sources and use the latest LTS/runtime version available for that implementation.
- If a provider generator exists, start from its generated output before adding benchmark-specific code.
- Implementation source should live under `implementations/<language>/<framework>`, such as `implementations/java/spring-boot`.
- Results should mirror the implementation structure under `results/<language>/<framework>/<variant>/<scenario>/<run_id>`.
- Each implementation should include its own `.gitignore`.
- Use the matching template from `github/gitignore` for implementation-level `.gitignore` files when one exists. Do not hand-write one unless no suitable upstream template exists.
- Do not include temporary superpowers planning docs in pull requests. Remove `docs/superpowers` artifacts after use.
- Do not add new runtimes before the runner is stable.
- Do not add Redis, Kafka, Kubernetes, or OpenTelemetry until the roadmap phase that calls for them.
- Do not make universal performance claims.
- Public docs should be written in English.
- Avoid over-engineering.
- Prefer boring, explicit Python runner modules over complex abstractions in early phases.

## Scenario Rules

Every new scenario must include:

- `question`
- `measures`
- `does_not_measure`
- `dependencies`
- `variants`
- `metrics`

Scenario names must describe service patterns, not technologies. For example, prefer `transactional-command-api` over `db-write`.

## Implementation Rules

- Match the existing project style.
- Keep changes surgical.
- Do not add infrastructure that is outside the current phase.
- Record benchmark outputs as machine-readable files where practical.
- Treat results as trade-offs under specific conditions.
- Never register a self-hosted runner directly to this public repository. Trusted
  home-k3s scheduling and runner registration belong to the private
  `hello-realworld-bench-ops` control repository.
- Keep publication credentials out of benchmark jobs and workloads. They may be
  used only by the GitHub-hosted publication job after measurement succeeds.
- When opening a pull request, use `.github/pull_request_template.md` and keep the description concise.
