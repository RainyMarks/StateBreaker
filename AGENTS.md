# AGENTS.md - StateBreaker v0.2

## What This Is

StateBreaker is a trace-driven black-box race condition discovery tool. It takes one
recorded normal flow, learns request dependencies and normal state behavior, then runs
controlled race experiments to produce evidence-backed findings.

## Hard Rules

1. The core package (`src/statebreaker/`) must stay business-agnostic. No target-specific
   terms are allowed there; this is enforced by `tests/models/test_no_business_hardcode.py`.
2. No stubbed findings. Every `CONFIRMED` verdict must trace to real `ExecutionTrial`
   records.
3. Learned invariants and baselines must keep their supporting trial ids.
4. Every feature ships with both a normal-group and an anomaly-group test.
5. Plugin-style extension is allowed only at these boundaries: `CaptureAdapter`,
   `SchedulerBackend`, `ResetStrategy`, `SemanticAdvisor`, `ReportRenderer`.
6. No Web UI before the auto-discovery loop works.

## Layout

- `src/statebreaker/models/` - versioned Pydantic contracts (`schema_version = "0.2"`),
  JSON round-trips, `extra="forbid"`.
- `src/statebreaker/config/` - `project.yaml` loading and scope guard.
- `src/statebreaker/artifacts/` - `.statebreaker/projects/<name>/` JSON store plus SQLite
  index.
- `src/statebreaker/capture/` - HAR, Postman, OpenAPI capture adapters, an HTTP proxy
  recorder, and a CDP browser recorder for HTTPS sites.
- `src/statebreaker/intelligence/` - trace normalization, value lineage, workflow graph,
  dependency validation, probe discovery, and per-session probe cloning.
- `src/statebreaker/baseline/` - normal-effect learning and invariant induction.
- `src/statebreaker/discovery/` and `src/statebreaker/planning/` - candidate scoring,
  pairing, and plan synthesis.
- `src/statebreaker/execution/` - controller, sessions, reset strategies, request rendering,
  and scheduler backends (`async-http`, `http1-last-byte`, `http2-stream-gate`).
- `src/statebreaker/oracle/` - control-vs-attack comparison and verdict generation.
- `src/statebreaker/minimization/` - concurrency, scheduler, workflow shrinking, and
  repeatability statistics.
- `src/statebreaker/reporting/` - executable PoC, JSON bundle, and HTML report.
- `src/statebreaker/semantic/` - optional `SemanticAdvisor` boundary with Noop default.
- `src/statebreaker/orchestration/` - shared stages, scanner orchestration, and checkpoints.
- `labs/` - vulnerable FastAPI teaching targets. These may use business names.
- `tests/` - pytest suite, `asyncio_mode=auto`, `pythonpath = ["src", "tests"]`.

## Quality Gate

Run this before considering a change complete:

```bash
python check.py
```

It runs ruff, mypy strict, and pytest. All three must pass before moving to the next
phase.

## Conventions

- Never hand-author workflows, invariants, or attack plans in the default flow. They are
  learned or inferred and must carry evidence references.
- Wall-clock timestamps are ISO strings. Durations and timelines use integer nanoseconds
  from a monotonic clock.
- Secrets are redacted only at presentation boundaries such as CLI output or reports, never
  in stored evidence.
- Do not commit to git unless explicitly asked.
- Newcomer-facing documentation should keep the end-to-end flow visible: capture ->
  graph -> baseline -> candidate -> plan -> trial -> finding -> report.
