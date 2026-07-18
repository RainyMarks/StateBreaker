# StateBreaker HAR Capture

`statebreaker-har-capture` is an offline importer for minimal HAR 1.2 files. It parses a
HAR without network access, conservatively filters known static resources, removes
transport-managed request headers, and produces a deterministic StateBreaker `Workflow`
with a linear dependency chain.

## Install and use

From the StateBreaker repository root:

```bash
python -m pip install -e plugins/statebreaker-har-capture
statebreaker workflow import recording.har --plugin har.capture --output workflow.json
statebreaker workflow validate workflow.json
```

The importer accepts `GET`, `POST`, `PUT`, `PATCH`, and `DELETE` requests. JSON bodies and
`application/x-www-form-urlencoded` bodies are normalized into the core `json_body` and
`form_body` contracts. Other raw body formats are rejected with a clear error. Conservative
response-variable inference is available for replay-relevant JSON scalar values.

Authorization and Cookie headers are preserved by default because a captured authenticated
workflow must remain replayable. Treat exported Workflow files as sensitive data. Direct API
callers can set `strip_credentials=True` when they need a shareable redacted artifact.

## Static-resource filtering

The plugin manifest advertises the `static-resource-filtering` capability.

Filtering is enabled by default. The importer first keeps entries explicitly marked as `fetch`
or `xhr`, plus responses with `application/json` or a `+json` subtype. It then filters known
static resource types, known static MIME types, and finally exact static extensions from the URL
path. Query strings and fragments do not participate in extension matching.

Entries with unknown or missing metadata, HTML/documents, and other ambiguous types remain in the
workflow unless their URL path has a listed static extension. Request method alone never identifies
a static resource. Filtering preserves the relative request order and uses
each retained entry's original zero-based HAR index in its step ID.

If a state probe selects a filtered entry, capture fails with the original index and a safe reason
category. If every entry is filtered, capture fails before a Workflow is constructed. These errors
do not include request URLs, headers, cookies, authorization values, or bodies.

## JSON response extractors

The plugin manifest advertises the `json-response-extractors` capability. Inference is enabled by
default and only reads valid JSON response text from retained producer entries that appear before a
consumer. Strict UTF-8 base64 content is supported. Responses or content explicitly marked with
`_truncated=true` or `truncated=true` are skipped, as are missing, non-JSON, malformed, and
unknown-encoding bodies. A semantically incomplete response that has no truncation marker and still
forms valid JSON cannot be identified reliably as truncated.

Eligible leaves are non-sensitive strings of at least eight characters and integers with an
absolute value of at least 1000. Booleans, nulls, floats, common status values, short business
constants, credential-shaped strings, and sensitive JSON field paths are excluded.

A value is inferred only when exactly one prior `(producer step, JSONPath)` explains its first
complete-value use. Ambiguous values are left literal. Generated JSONPath extractors are required
and are added to the producer; consumers retain the linear dependency and explicitly depend on the
producer.

Replacement is limited to complete path segments, complete query values or list elements, JSON
body string/integer leaves, and form values or list elements. Headers, Authorization, Cookie, URL
host/scheme, response headers, dictionary keys, encoded path segments, and composite strings are
never changed.

After response inference, step IDs are regenerated from each request's templated canonical path.
Successfully inferred dynamic path values therefore do not remain in step IDs, dependencies, or
state-probe references. The deterministic hash also uses the complete templated path, while the
human-readable slug renders a template such as `${run_id}` as `run-id`.

Dynamic values that cannot be inferred reliably remain literal and may still appear in requests or
step metadata. The plugin does not provide general anonymization for unknown identifiers.

This feature does not infer setup roles, authentication variables, CSRF flows, sessions, origins,
or generic dependencies, and it does not prove Runtime replay.

## Local coupon-race replay acceptance

The synthetic `coupon-race-normal.har` fixture is verified to produce a Workflow that replays the
normal create, state, redeem, and state flow against the repository's coupon-race FastAPI app. The
integration test uses `httpx.ASGITransport`, so every request stays in process and no external
network or listening port is used.

This acceptance covers a single origin, one session, and a JSON API. The create step remains an
`action`; capture does not infer a `setup` role. Generic authentication and CSRF inference remain
unsupported, and this focused test does not imply that arbitrary HAR recordings are replayable.

## Options

Direct plugin callers may pass the strict supported options:

```python
workflow = await HarCapturePlugin().capture(
    Path("recording.har"),
    {
        "filter_static_resources": True,
        "infer_response_variables": True,
        "state_probe_entry_indices": [1],
        "strip_credentials": False,
    },
)
```

Set `filter_static_resources=False` through the direct plugin API to retain every HAR entry.
Set `infer_response_variables=False` to keep normalized requests literal and emit no inferred
extractors. Both conservative enhancements default to `True` when the options mapping is empty.

Indices are zero-based positions in the original `log.entries` array. They must be unique,
non-negative, in range, and refer to a generated step. Selected steps use the `probe` role
and are listed in `state_probe_steps`. Selecting an entry removed by static-resource filtering
is an explicit error and is never silently ignored or remapped.

The core CLI accepts the same mapping from a JSON or YAML file:

```bash
statebreaker workflow import recording.har --plugin har.capture \
  --options capture-options.yaml --output workflow.json
```
