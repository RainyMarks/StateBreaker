# StateBreaker HAR Capture

## Plugin purpose

`statebreaker-har-capture` converts authorized HAR 1.2 traffic into a StateBreaker `Workflow`. It parses a
HAR without network access, conservatively filters known static resources, removes
transport-managed request headers, and produces a deterministic StateBreaker `Workflow`
with a linear dependency chain.

## Install and use

From the StateBreaker repository root:

```bash
python -m pip install -e plugins/statebreaker-har-capture
statebreaker workflow import recording.har --plugin har.capture \
  --options capture-options.json --output workflow.json
statebreaker workflow validate workflow.json
```

`--options` accepts a JSON or YAML file path.

## Supported scope

The importer supports:

- HTTP and HTTPS requests from one origin;
- one StateBreaker session;
- `GET`, `POST`, `PUT`, `PATCH`, and `DELETE`;
- query parameters and repeated query values;
- JSON request bodies;
- `application/x-www-form-urlencoded` request bodies;
- request Headers;
- conservative JSON response Extractors;
- configuration by original HAR entry index;
- browser Header normalization;
- explicit setup and probe roles;
- replayable credentials, preserved by default unless explicitly stripped.

Other raw request body formats are rejected. This supported scope does not imply that an arbitrary
HAR can be imported and replayed without explicit business-flow selection and review.

## Processing pipeline

Processing occurs in this order:

1. HAR parse;
2. option and original-index validation;
3. explicit exclusion;
4. static-resource filtering;
5. request normalization;
6. explicit role assignment;
7. required response body validation;
8. response variable inference;
9. step ID stabilization;
10. `state_probe_steps` construction;
11. core `Workflow` validation.

Exclusion therefore happens before origin selection and normalization. Inference and step ID
stabilization preserve explicit roles and atomically remap dependencies and probe references.

## Safety boundary

- Authorization and Cookie Headers are preserved by default so an authorized workflow can remain
  replayable. Treat exported Workflows as sensitive data.
- Set `strip_credentials=true` to remove those credential Headers.
- Safe errors report an original entry index and a reason category without echoing request URLs,
  Header values, request or response bodies, or credential values.
- Required response body validation is an opt-in recording-integrity assertion, not automatic
  producer detection.
- The plugin does not guess a dynamic ID when a producer response is missing.
- A parseable body does not guarantee that inference will produce an Extractor.
- Do not commit an unreviewed real HAR; repository fixtures must remain synthetic and sanitized.

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

At Runtime, `httpx` may independently generate transport Headers such as `Host`, `Content-Length`,
`User-Agent`, and `Accept-Encoding`. Those generated fields are not fixed Headers stored by the
Capture plugin.

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

## Required response body validation

The plugin manifest advertises the `required-response-body-validation` capability. The strict
`required_response_body_entry_indices: list[int]` option defaults to `[]`. Each value is an
original zero-based HAR `log.entries` index and explicitly asserts that the retained entry has an
untruncated, JSON-compatible response body that the current response-variable inference decoder can
parse.

This is a recording-completeness assertion, not automatic producer detection. The plugin does not
select entries from URL names, methods, status codes, identifier formats, length, or entropy.
`setup_entry_indices` and `state_probe_entry_indices` do not automatically become required, and a
required index may overlap either role list. A required index must not overlap
`exclude_entry_indices`; if static-resource filtering removes it, capture fails with the original
index. Validation still runs when `infer_response_variables=False`.

A required response must use `application/json` or an `application/*+json` subtype; MIME parameters
and case differences are supported. Plain JSON text and strict base64-encoded UTF-8 JSON are
accepted. Missing response/content/text, empty or malformed JSON, unsupported encoding, explicit
response/content truncation, and status 204 fail with a safe entry-index error. Other status codes
do not fail solely because of their status.

Any valid JSON value passes, including objects, arrays, strings, numbers, booleans, and null. A
parseable body does not guarantee a candidate, consumer, variable, or Extractor; it only proves the
selected response body was recorded in a form the inference decoder can read.

Configure the assertion through the CLI `--options` JSON/YAML file:

```json
{
  "exclude_entry_indices": [0, 1, 2],
  "setup_entry_indices": [3],
  "state_probe_entry_indices": [4, 6],
  "required_response_body_entry_indices": [3],
  "strip_credentials": false,
  "filter_static_resources": true,
  "infer_response_variables": true,
  "normalize_browser_headers": true
}
```

```bash
statebreaker workflow import recording.har \
  --plugin har.capture \
  --options capture-options.json \
  --output workflow.json
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

Sensitive Token, Cookie, Authorization, session, CSRF/XSRF, JWT, bearer, and private-key values are
excluded by field-path and credential-shape checks rather than inferred as replay variables.

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

## Current limitations

- No automatic business-flow selection.
- No automatic setup/probe role inference.
- No generated login flow.
- No automatic CSRF acquisition or refresh.
- No multi-session Workflow construction.
- No multi-origin Workflow construction.
- No multipart or file-upload request body support.
- No WebSocket capture.
- No guaranteed recovery when a producer response body is missing.
- No inference from dynamic Header values.
- No guarantee that an arbitrary browser HAR can replay without review and explicit options.

## Verified chain

Validation uses synthetic and sanitized artifacts only:

- a sanitized Chrome-shaped HAR fixture;
- Capture import and Workflow validation;
- real `ExecutionRuntime` replay against the coupon-race lab;
- Delta Learner probe sampling.

The repository integration test uses `httpx.ASGITransport`, so it requires neither Docker nor a
listening port. These checks cover one origin, one session, and a JSON API. They do not describe the
synthetic fixture as a production recording or guarantee universal HAR replayability.

## Complete options reference

All options are strict. Booleans are not coerced from strings or integers, and index lists accept
integers only.

| Option | Type | Default |
| --- | --- | --- |
| `state_probe_entry_indices` | `list[int]` | `[]` |
| `setup_entry_indices` | `list[int]` | `[]` |
| `exclude_entry_indices` | `list[int]` | `[]` |
| `required_response_body_entry_indices` | `list[int]` | `[]` |
| `strip_credentials` | `bool` | `false` |
| `filter_static_resources` | `bool` | `true` |
| `infer_response_variables` | `bool` | `true` |
| `normalize_browser_headers` | `bool` | `true` |

```json
{
  "exclude_entry_indices": [0, 1, 2],
  "setup_entry_indices": [3],
  "state_probe_entry_indices": [4, 6],
  "required_response_body_entry_indices": [3],
  "strip_credentials": false,
  "filter_static_resources": true,
  "infer_response_variables": true,
  "normalize_browser_headers": true
}
```

Every configured index is an original zero-based position in the HAR `log.entries` array. Each
list must contain unique, non-negative, in-range integers. Excluded indices must not overlap setup,
probe, or required-response indices; setup and probe indices must not overlap. Required-response
indices may overlap setup or probe indices because recording completeness and execution roles are
independent. Setup, probe, and required-response indices must refer to retained entries. Only
selected probe steps are listed in `state_probe_steps`.

Set `filter_static_resources=False` through the direct plugin API to retain every HAR entry. Set
`infer_response_variables=False` to keep normalized requests literal and emit no inferred
Extractors. Set `normalize_browser_headers=False` to restore the compatibility behavior described
above. These options default to `True`; `strip_credentials` defaults to `False`.

The core CLI accepts the same mapping from a JSON or YAML file:

```bash
statebreaker workflow import recording.har --plugin har.capture \
  --options capture-options.json --output workflow.json
```

## Manifest capabilities

The manifest declares these implemented and tested capabilities:

- `har-1.2`;
- `deterministic-workflow`;
- `offline-import`;
- `json-body`;
- `form-body`;
- `replayable-credentials`;
- `static-resource-filtering`;
- `explicit-entry-exclusion`;
- `browser-header-normalization`;
- `required-response-body-validation`;
- `json-response-extractors`;
- `explicit-step-roles`.
