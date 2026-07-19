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

## Browser request Header normalization

The plugin manifest advertises the `browser-header-normalization` capability. The strict
`normalize_browser_headers: bool` option defaults to `True`. Configure it through a JSON or YAML
file passed to the current CLI `--options` argument:

```json
{
  "normalize_browser_headers": true,
  "strip_credentials": false
}
```

After explicit entry exclusion and static-resource filtering, but before a `RequestSpec` is
created, the importer removes request Header fields managed by an HTTP client or browser context.
Name and prefix matching is case-insensitive. Values of retained Headers are not modified.

With normalization enabled, the explicit denylist removes:

- connection and transport fields: `Host`, `Content-Length`, `Transfer-Encoding`, `Connection`,
  `Proxy-Connection`, `Keep-Alive`, `Upgrade`, `TE`, `Trailer`, and `Accept-Encoding`;
- every HTTP/2 or HTTP/3 pseudo-header whose name starts with `:`;
- browser context fields: `User-Agent`, `Priority`, `DNT`, `Sec-GPC`, every `Sec-Fetch-*`,
  every `Sec-CH-*`, and every `Sec-WebSocket-*` Header;
- browser cache noise: `Cache-Control`, `Pragma`, `If-None-Match`, and `If-Modified-Since`.

This is a conservative denylist, not an application Header allowlist. `Content-Type`, `Accept`,
`Accept-Language`, `Origin`, `Referer`, `X-Requested-With`, ordinary custom `X-*` Headers,
`Idempotency-Key`, `If-Match`, `If-Unmodified-Since`, `Range`, and other unlisted application
Headers remain available. `Origin` and `Referer` are currently preserved exactly rather than
rewritten.

`Authorization` and `Cookie` are deliberately absent from the browser denylist.
`normalize_browser_headers` and `strip_credentials` are independent:

| normalize_browser_headers | strip_credentials | Browser noise | Authorization/Cookie |
| --- | --- | --- | --- |
| `true` | `false` | removed | preserved |
| `true` | `true` | removed | removed |
| `false` | `false` | pre-feature behavior | preserved |
| `false` | `true` | pre-feature behavior | removed |

Setting `normalize_browser_headers=False` restores the Header behavior from before this capability.
The legacy normalizer still removes its original transport-managed set (`Host`, `Content-Length`,
`Transfer-Encoding`, `Connection`, and `Proxy-Authorization`); the new browser, cache, prefix, and
pseudo-header rules are disabled. Existing duplicate retained Header handling is unchanged.

Some applications may intentionally depend on `User-Agent` or a `Sec-*` Header. Such recordings can
disable normalization explicitly. Header normalization does not prove that an arbitrary HAR is
replayable, and business-flow selection remains the responsibility of `exclude_entry_indices`,
`setup_entry_indices`, and `state_probe_entry_indices`.

```bash
statebreaker workflow import recording.har \
  --plugin har.capture \
  --options capture-options.json \
  --output workflow.json
```

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

## Explicit entry exclusion

The plugin manifest advertises the `explicit-entry-exclusion` capability. Use
`exclude_entry_indices` for explicit business-flow selection when a recording contains page
loads, polling, repeated setup calls, or other valid requests that do not belong in the target
workflow. Values are original zero-based HAR `log.entries` indices, not positions after static
filtering:

```json
{
  "exclude_entry_indices": [0, 1, 2, 5],
  "setup_entry_indices": [6],
  "state_probe_entry_indices": [7, 9]
}
```

The exclusion list must contain unique, non-negative, in-range integers. It must not overlap
`setup_entry_indices` or `state_probe_entry_indices`; conflicting roles have no implicit
priority. Out-of-range indices fail with the original index and entry count. If explicit
exclusion alone, or exclusion followed by static filtering, leaves no usable entry, capture
fails instead of emitting an empty Workflow.

Exclusion happens before static-resource filtering, origin and base-URL selection, request
normalization, Header or body processing, and response-variable inference. An excluded entry
therefore cannot create a step or Extractor, provide a response value, affect the origin, appear
in dependencies or `state_probe_steps`, or contribute any data to the serialized Workflow. If an
excluded producer's recorded value remains in a retained consumer, the existing conservative
inference rules leave that consumer literal; the plugin does not invent an Extractor.

Explicit exclusion and static-resource filtering are independent. Static filtering classifies
known resource types such as CSS and JavaScript; `exclude_entry_indices` performs user-directed
business-flow selection. Either feature can exclude the same valid original entry, exclusion
still applies when `filter_static_resources=False`, and one feature does not replace the other.
The plugin does not automatically decide which repeated create, state, events, or action calls
belong to the user's intended business flow.

The core CLI reads these options from a JSON or YAML file through the current `--options` path
argument:

```bash
statebreaker workflow import recording.har --plugin har.capture \
  --options capture-options.json --output workflow.json
```

## Explicit step roles

The plugin manifest advertises the `explicit-step-roles` capability. Callers can assign roles with
original zero-based HAR `log.entries` indices:

```json
{
  "setup_entry_indices": [0],
  "state_probe_entry_indices": [1, 3]
}
```

Entries in `setup_entry_indices` become `setup` steps, entries in
`state_probe_entry_indices` become `probe` steps, and all unspecified retained entries remain
`action` steps. The two lists must not overlap. Indices must be non-negative, unique within each
list, in range, and refer to retained entries. Selecting a statically filtered entry fails with its
original index; indices are never remapped after filtering. Setup steps are not added to
`state_probe_steps`.

Roles are only assigned from these explicit options. The plugin does not automatically infer setup
steps from request methods, response status codes, URL names, step position, response IDs, or
generated extractors. For example, the first `POST` remains an `action` unless its original index
is listed in `setup_entry_indices`.

The current core CLI accepts the same mapping from a JSON or YAML file through `--options`:

```bash
statebreaker workflow import recording.har --plugin har.capture \
  --options capture-options.json --output workflow.json
```

For the local coupon-race fixture, the JSON configuration above produces
`setup, probe, action, probe` for entries `0, 1, 2, 3`.

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

This feature does not automatically infer setup roles, authentication variables, CSRF flows,
sessions, origins, or generic dependencies, and it does not prove Runtime replay.

## Local coupon-race replay acceptance

The synthetic `coupon-race-normal.har` fixture is verified to produce a Workflow that replays the
normal create, state, redeem, and state flow against the repository's coupon-race FastAPI app. The
integration test uses `httpx.ASGITransport`, so every request stays in process and no external
network or listening port is used.

This acceptance covers a single origin, one session, and a JSON API. The create step remains an
`action` by default and becomes `setup` only when entry `0` is explicitly configured. Generic
authentication and CSRF inference remain unsupported, and this focused test does not imply that
arbitrary HAR recordings are replayable.

## Options

Direct plugin callers may pass the strict supported options:

```python
workflow = await HarCapturePlugin().capture(
    Path("recording.har"),
    {
        "filter_static_resources": True,
        "infer_response_variables": True,
        "normalize_browser_headers": True,
        "exclude_entry_indices": [2, 4],
        "setup_entry_indices": [0],
        "state_probe_entry_indices": [1],
        "strip_credentials": False,
    },
)
```

Set `filter_static_resources=False` through the direct plugin API to retain every HAR entry.
Set `infer_response_variables=False` to keep normalized requests literal and emit no inferred
extractors. Set `normalize_browser_headers=False` to restore the pre-capability Header behavior
described above. These three options are strict booleans and default to `True` when the options
mapping is empty. `strip_credentials` is a separate strict boolean that defaults to `False`.

Indices are zero-based positions in the original `log.entries` array. Each list must contain
unique, non-negative, in-range integers. Excluded indices must not overlap setup or probe indices,
and setup and probe indices must not overlap each other. Setup and probe indices must refer to a
generated step. Only selected probe steps are listed in `state_probe_steps`. Selecting an entry removed by
static-resource filtering is an explicit error and is never silently ignored or remapped.

The core CLI accepts the same mapping from a JSON or YAML file:

```bash
statebreaker workflow import recording.har --plugin har.capture \
  --options capture-options.json --output workflow.json
```
