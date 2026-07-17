# StateBreaker HAR Capture

`statebreaker-har-capture` is an offline importer for minimal HAR 1.2 files. It parses a
HAR without network access, removes transport-managed request headers, and
produces a deterministic StateBreaker `Workflow` with a linear dependency chain.

## Install and use

From the StateBreaker repository root:

```bash
python -m pip install -e plugins/statebreaker-har-capture
statebreaker workflow import recording.har --plugin har.capture --output workflow.json
statebreaker workflow validate workflow.json
```

The importer accepts `GET`, `POST`, `PUT`, `PATCH`, and `DELETE` requests. JSON bodies and
`application/x-www-form-urlencoded` bodies are normalized into the core `json_body` and
`form_body` contracts. Other raw body formats are rejected with a clear error. Static-resource
filtering and dynamic-variable inference are not implemented yet.

Authorization and Cookie headers are preserved by default because a captured authenticated
workflow must remain replayable. Treat exported Workflow files as sensitive data. Direct API
callers can set `strip_credentials=True` when they need a shareable redacted artifact.

## Options

Direct plugin callers may pass the only supported option:

```python
workflow = await HarCapturePlugin().capture(
    Path("recording.har"),
    {"state_probe_entry_indices": [1], "strip_credentials": False},
)
```

Indices are zero-based positions in the original `log.entries` array. They must be unique,
non-negative, in range, and refer to a generated step. Selected steps use the `probe` role
and are listed in `state_probe_steps`.

The core CLI accepts the same mapping from a JSON or YAML file:

```bash
statebreaker workflow import recording.har --plugin har.capture \
  --options capture-options.yaml --output workflow.json
```
