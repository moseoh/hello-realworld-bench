# Benchmark Dashboard

The dashboard renders compatible benchmark cohorts from the repository's static `benchmark-data` branch. `Service` retains individual trial timelines, while `Cold start` and `Build` render compact summary tables without time-series requests. The dashboard reads service and lifecycle entries from `run-sets/<cohort>/<run-set-id>/run-set.json` and build entries from `build-run-sets/<cohort>/<run-set-id>/build-run-set.json`. It does not require a database or backend API.

## Development

From the repository root:

```bash
make dashboard-dev
```

The development server reads the mutable `benchmark-data` branch by default. Use an exact commit to reproduce a deployed dataset:

```bash
VITE_DATA_COMMIT=<benchmark-data-commit> make dashboard-dev
```

## Verification

```bash
make dashboard-check
```

GitHub Pages builds resolve the current `benchmark-data` branch to an exact commit and inject that immutable revision into the application. The rendered revision is visible in the selection bar.
