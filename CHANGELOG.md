# Changelog

## Unreleased

### Added

- Versioned evaluation suites under `evaluation/suites/`.
- Async evaluation harness with per-case failure isolation and concurrency limits.
- Assertions for intent, agent routing, required/forbidden output, and latency.
- Aggregate quality gates and relative baseline regression checks.
- JSON and Markdown report artifacts.
- Explicit baseline promotion through `python -m evaluation.run_harness --promote`.
- `POST /eval/harness` API endpoint.
- Offline harness unit tests and GitHub Actions workflow.

### Changed

- Rebranded the runtime, containers, monitoring labels, environment variables, and documentation as NovaRelay.
- Replaced prewritten resume metrics with reproducible measurement guidance.
- Stopped the legacy evaluator from overwriting its baseline on every run.

### Security

- Added a sanitized `.env.example` and excluded local secrets from build and source-control workflows.
