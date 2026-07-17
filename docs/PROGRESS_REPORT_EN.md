# StateBreaker Current Project Status

- **Repository:** [RainyMarks/StateBreaker](https://github.com/RainyMarks/StateBreaker)
- **Core version/API:** `0.1.0` / `0.1`
- **Updated:** 2026-07-17
**Development state:** Capture is merged into `main`; the stepwise CLI and English-default lab UI are in PR #5

## 1. What the project is now

StateBreaker is an extensible framework for business-state vulnerabilities such as concurrent
coupon reuse, double spending, one-time-token replay, cross-user claims, and workflow skipping.

The core CLI and data contracts are scenario-independent. The Lao Wang coupon race is the first
concrete configuration and reference implementation loaded into that framework. In v0.1, the
interfaces are reusable, while the shipped attack algorithms are still intentionally limited to
the milk-tea coupon experiment.

It is no longer an empty interface skeleton, but it is not yet a general-purpose scanner. The
accurate description is:

> A reusable plugin skeleton with a working minimal chain for normal-flow replay, candidate
> invariants, attack-plan generation, real concurrent execution, state-based verification, and PDF
> reporting. The first fully connected implementation is a HAR importer plus the deterministic
> local milk-tea coupon race lab; it is not yet evidence of cross-scenario algorithm coverage.

## 2. Repository contents

```text
src/statebreaker/                   contracts, runtime, discovery, pipeline, CLI
plugins/statebreaker-har-capture/   HAR 1.2 Capture plugin
statebreaker-learner-delta/         normal-state delta Learner
race-generator/                     race AttackPlan Generator
race-executor/                      bounded concurrent Executor
statebreaker-verifier-basic/        state-evidence Verifier
statebreaker-reporter-pdf/          PDF Reporter
plugin-template/                    starter package for team members
labs/coupon-race/                   Lao Wang BUG50 Docker lab
examples/coupon-race/               Workflow, Invariant, AttackPlan examples
docs/                               demo, architecture, contracts, development guides
```

## 3. Six-stage status

```text
capture → learner → generator → executor → verifier → reporter
   ✅        ✅         ✅          ✅         ✅         ✅
```

| Stage | plugin_id | Implemented | Current boundary |
|---|---|---|---|
| Capture | `har.capture` | offline HAR 1.2, same-origin checks, JSON/Form, Cookie/Auth | no automatic dynamic-ID/Extractor inference; one origin |
| Learner | `team.delta-learner` | repeated baseline samples; max-delta/min/transition candidates | observed candidates require human confirmation |
| Generator | `team.race-generator` | concurrent, burst, offset, idempotency and other plans | target recognition remains coupon/race oriented |
| Executor | `team.race-executor` | real HTTP, bounded concurrency, snapshots and timing evidence | no production Last-Byte Gate yet |
| Verifier | `team.basic-verifier` | max-delta, minimum, count, single-use, transition | needs observable business state |
| Reporter | `team.pdf-reporter` | PDF and JSON summary | portable Latin PDF fonts |

`template.dry-run` remains available for plugin-discovery teaching and sends no requests.

## 4. What the core provides

- versioned Pydantic models and exported JSON Schema;
- one isolated `httpx.AsyncClient` and Cookie Jar per named session;
- recursive `${variable}` rendering;
- JSONPath, response-header, and regex extraction;
- redacted event logs for Authorization, Cookie, password, token, and secret fields;
- JSONL events, correlation IDs, request ordinals, and monotonic timing;
- Entry Point discovery, API compatibility checks, and duplicate-ID detection;
- stable exit codes: input 2, plugin 3, runtime 4;
- a stepwise human-inspectable CLI and an automated CI pipeline.

## 5. Stepwise CLI

The deleted interactive wizard has not been restored. The current CLI exposes the actual method:

```text
workflow show/replay
→ invariants show
→ generate
→ plans list/select
→ attack
→ verify
→ bundle build
→ report
```

The `attack` command prints real SEND/DONE relative timing, HTTP statuses, server check/commit
evidence, before/after state, and numeric deltas. `pipeline run` is retained for CI and batch jobs.

See the [Chinese live demo guide](DEMO_GUIDE_ZH.md) and [CLI reference](cli.md).

## 6. Lao Wang coupon-race lab

The lab is one FastAPI container with a native HTML/CSS/JS UI and per-run state isolation. The
redeem handler intentionally contains a 150 ms TOCTOU window:

```text
check coupon_used == false
→ await 150 ms
→ discount += 50
→ coupon_used = true
```

Sequential baseline:

```text
discount_yuan: 0 → 50
successful_redemptions: 0 → 1
```

Two-request race:

```text
checks=2, commits=2, rejections=0
discount_yuan: 0 → 100
successful_redemptions: 0 → 2
```

The invariant permits a maximum delta of 50; the observed delta is 100, so the verifier emits a
`CONFIRMED` Finding.

## 7. Capture review outcome

The initial HAR Capture PR parsed the envelope but removed Cookie/Authorization and rejected all
request bodies, so it could not replay most authenticated POST workflows. Before merge it was
hardened with:

- replayable credentials by default and explicit `strip_credentials`;
- JSON and `application/x-www-form-urlencoded` bodies;
- an authenticated JSON HAR fixture;
- dedicated Python 3.11/3.12 CI;
- 24 passing plugin tests before merge into `main`.

The stepwise CLI later added `workflow import --options` integration coverage, bringing the current
Capture suite to 25 tests.

## 8. Quality status

Verified on the current development branch:

- core/lab suite: 27 passed;
- HAR Capture suite: 25 passed;
- Ruff: passed;
- core and Capture mypy: passed;
- GitHub Actions: Linux 3.11/3.12, Windows 3.11/3.12, Docker lab, and Capture 3.11/3.12 all passed;
- manual eight-stage run: baseline 0→50, race 0→100, confirmed Finding, PDF generated.

## 9. Remaining work

1. infer dynamic IDs, token propagation, Extractors, and dependencies from capture data;
2. add live browser/proxy collection in addition to offline HAR import;
3. generalize generator/executor targeting beyond coupon labels to withdrawal, invitation, token,
   and workflow-order scenarios;
4. implement Last-Byte Gate, HTTP/2 synchronization, minimum-concurrency search, and success rates;
5. improve invariant learning, human confirmation, and HTML timing reports;
6. add four to six distinct business-logic labs and cross-scenario evaluation.

## 10. One-sentence report conclusion

> We have advanced the original plugin skeleton into a working minimal closed loop that can import
> or describe normal traffic, replay baseline state, generate and explicitly select race plans,
> send real concurrent requests, confirm violations from business state, and emit a PDF; the coupon
> lab is the first observable test target, while dynamic capture and cross-domain generality are the
> next priorities.

## 11. Related documentation

- [Chinese live demo guide](DEMO_GUIDE_ZH.md)
- [CLI reference](cli.md)
- [Architecture](architecture.md)
- [Data contracts](contracts.md)
- [Plugin development](plugin-development.md)
- [Chinese progress report](PROGRESS_REPORT_ZH.md)
