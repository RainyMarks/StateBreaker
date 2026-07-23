# ruff: noqa: E501
"""Render browser-context executors for authenticated race reproduction.

The core runner stays target-agnostic. A plan file supplies selectors, field
names, and payload metadata for a specific authorized test environment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from statebreaker.capture.har import load_har
from statebreaker.errors import StateBreakerError
from statebreaker.models.capture import HttpExchange

JsonObject = dict[str, Any]


def _read_json(path: Path) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise StateBreakerError(f"cannot read browser-context plan {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StateBreakerError(f"invalid browser-context plan JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StateBreakerError("browser-context plan must be a JSON object")
    return value


def _require_mapping(parent: JsonObject, key: str) -> JsonObject:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise StateBreakerError(f"browser-context plan field {key!r} must be an object")
    return value


def _require_string(parent: JsonObject, key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value:
        raise StateBreakerError(f"browser-context plan field {key!r} must be a non-empty string")
    return value


def _load_embedded_code(plan: JsonObject, plan_path: Path) -> str:
    runner = _require_mapping(plan, "runner")
    inline = runner.get("code")
    if isinstance(inline, str) and inline:
        return inline

    code_file = runner.get("code_file")
    if not isinstance(code_file, str) or not code_file:
        raise StateBreakerError("browser-context plan needs runner.code or runner.code_file")

    path = Path(code_file)
    if not path.is_absolute():
        path = plan_path.parent / path
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise StateBreakerError(f"cannot read runner.code_file {path}: {exc}") from exc

    json_field = runner.get("code_json_field")
    if json_field is None:
        return text
    if not isinstance(json_field, str) or not json_field:
        raise StateBreakerError("runner.code_json_field must be a non-empty string")
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StateBreakerError(f"runner.code_file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(loaded, dict) or not isinstance(loaded.get(json_field), str):
        raise StateBreakerError(f"runner.code_file JSON must contain string field {json_field!r}")
    code = loaded[json_field]
    if not isinstance(code, str):
        raise StateBreakerError(f"runner.code_file JSON must contain string field {json_field!r}")
    return code


def _form_body(exchange: HttpExchange) -> dict[str, Any] | None:
    if exchange.request_body_encoding != "form" or not isinstance(exchange.request_body, dict):
        return None
    return exchange.request_body


def _select_har_template_exchange(trace_exchanges: list[HttpExchange], runner: JsonObject) -> HttpExchange:
    code_field = str(runner.get("code_field") or "code")
    payload_field = str(runner.get("payload_field") or "post_data")
    for exchange in trace_exchanges:
        body = _form_body(exchange)
        if exchange.method == "POST" and body and code_field in body and payload_field in body:
            return exchange
    for exchange in trace_exchanges:
        body = _form_body(exchange)
        if exchange.method == "POST" and body and code_field in body:
            return exchange
    raise StateBreakerError(
        "HAR does not contain a form POST exchange with the configured code/payload fields"
    )


def _safe_url_origin(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _merge_har_evidence(plan: JsonObject, har_path: Path) -> None:
    """Use HAR as request-shape evidence without copying auth material."""
    try:
        trace = load_har(har_path)
    except Exception as exc:  # noqa: BLE001 - normalize adapter errors at this boundary
        raise StateBreakerError(f"cannot load HAR evidence {har_path}: {exc}") from exc

    runner = _require_mapping(plan, "runner")
    template = _select_har_template_exchange(trace.exchanges, runner)
    body = _form_body(template) or {}

    code_field = str(runner.get("code_field") or "code")
    action_field = str(runner.get("action_field") or "action")
    action_value = body.get(action_field)
    code = body.get(code_field)
    if isinstance(action_value, str) and action_value:
        runner["action_value"] = action_value
    if isinstance(code, str) and code:
        runner["code"] = code
        runner.pop("code_file", None)
        runner.pop("code_json_field", None)

    auth_fields = [
        key
        for key in (
            str(runner.get("token_field") or "csrf_token"),
            "cookie",
            "authorization",
        )
        if key in body or key in template.request_headers
    ]
    plan["har_evidence"] = {
        "path": str(har_path),
        "capture_id": trace.capture_id,
        "exchange_count": len(trace.exchanges),
        "template_exchange_id": template.exchange_id,
        "request_method": template.method,
        "request_url_origin": _safe_url_origin(template.url),
        "auth_fields_ignored": sorted(set(auth_fields)),
    }


def prepare_browser_context_plan(
    plan_path: Path,
    *,
    har_path: Path | None = None,
    rounds: int | None = None,
    start_round: int | None = None,
    autorun: bool | None = None,
) -> JsonObject:
    """Load, validate, and enrich a browser-context plan."""
    plan = _read_json(plan_path)
    _validate_plan(plan)
    if har_path is not None:
        _merge_har_evidence(plan, har_path)

    runner = _require_mapping(plan, "runner")
    if not isinstance(runner.get("code"), str):
        runner["code"] = _load_embedded_code(plan, plan_path)
    runner.pop("code_file", None)
    runner.pop("code_json_field", None)

    schedule = _require_mapping(plan, "schedule")
    if rounds is not None:
        if rounds <= 0:
            raise StateBreakerError("rounds must be a positive integer")
        schedule["rounds"] = rounds
    if start_round is not None:
        if start_round <= 0:
            raise StateBreakerError("start-round must be a positive integer")
        schedule["start_round"] = start_round
    if autorun is not None:
        plan["autorun"] = autorun
    return plan


def _validate_plan(plan: JsonObject) -> None:
    selectors = _require_mapping(plan, "selectors")
    _require_string(selectors, "root")
    _require_string(selectors, "output")

    runner = _require_mapping(plan, "runner")
    _require_string(runner, "token_selector")
    _require_string(runner, "run_url_attribute")
    _require_string(runner, "exercise_attribute")

    fields = _require_mapping(plan, "fields")
    _require_string(fields, "source")
    _require_string(fields, "destination")
    _require_string(fields, "amount")

    schedule = _require_mapping(plan, "schedule")
    accounts = schedule.get("accounts")
    if not isinstance(accounts, list) or len(accounts) < 3 or not all(
        isinstance(item, str) and item for item in accounts
    ):
        raise StateBreakerError("schedule.accounts must contain at least three account names")
    balances = schedule.get("initial_balances")
    if not isinstance(balances, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in balances.items()
    ):
        raise StateBreakerError("schedule.initial_balances must be an object of money strings")


def _browser_runtime_source(plan: JsonObject) -> str:
    plan_json = json.dumps(plan, ensure_ascii=False, indent=2)
    return f"""// Generated by StateBreaker browser-context render.
// Run only in an authorized, already-authenticated browser page for the target plan.
(async () => {{
  "use strict";
  const plan = {plan_json};

  const moneyToCents = (value) => {{
    const text = String(value ?? "").replace(/[$,\\s]/g, "");
    const match = text.match(/^(-?)(\\d+)(?:\\.(\\d{{1,2}}))?$/);
    if (!match) throw new Error(`cannot parse money value: ${{value}}`);
    const cents = BigInt(match[2]) * 100n + BigInt((match[3] || "").padEnd(2, "0"));
    return match[1] === "-" ? -cents : cents;
  }};

  const centsToMoney = (input) => {{
    let cents = BigInt(input);
    const negative = cents < 0n;
    if (negative) cents = -cents;
    const dollars = cents / 100n;
    const frac = String(cents % 100n).padStart(2, "0");
    const whole = String(dollars).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",");
    return `${{negative ? "-" : ""}}${{plan.display?.currency_symbol ?? "$"}}${{whole}}.${{frac}}`;
  }};

  const regexEscape = (value) => String(value).replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&");

  const balanceLine = (text, label) => {{
    const pattern = new RegExp(`${{regexEscape(label)}}\\\\s+balance:\\\\s*\\\\$([0-9,]+(?:\\\\.[0-9]{{2}})?)`, "i");
    const match = String(text || "").match(pattern);
    return match ? moneyToCents(match[1]) : null;
  }};

  const parseResult = (text, source, destination) => {{
    const sourceFinal = balanceLine(text, `Final ${{source}}`);
    const destinationFinal = balanceLine(text, `Final ${{destination}}`);
    return {{
      ok: sourceFinal !== null && destinationFinal !== null,
      insufficient: /Insufficient funds/i.test(text || ""),
      sourceFinal,
      destinationFinal,
    }};
  }};

  const planRoot = () => {{
    const root = document.querySelector(plan.selectors.root);
    if (!root) throw new Error(`root selector not found: ${{plan.selectors.root}}`);
    return root;
  }};

  const readToken = () => {{
    const node = [...document.querySelectorAll(plan.runner.token_selector)].find((item) => item.value);
    if (!node) throw new Error(`token selector not found: ${{plan.runner.token_selector}}`);
    return node.value;
  }};

  const chooseSchedule = (balances) => {{
    const source = Object.keys(balances).sort((a, b) => balances[a] === balances[b] ? 0 : (balances[a] > balances[b] ? -1 : 1))[0];
    const destinations = Object.keys(balances).filter((name) => name !== source).sort();
    return {{
      source,
      destA: destinations[0],
      destB: destinations[1],
      amount: String((balances[source] * 9n) / 1000n),
    }};
  }};

  const makeBody = (context, destination, suffix) => {{
    const payload = {{}};
    payload[plan.fields.source] = context.source;
    payload[plan.fields.destination] = destination;
    payload[plan.fields.amount] = String(context.amount);

    const body = new URLSearchParams();
    body.set(plan.runner.token_field || "csrf_token", context.token);
    body.set(plan.runner.action_field || "action", plan.runner.action_value || "run");
    body.set(plan.runner.exercise_field || "exercise_id", context.exerciseId);
    body.set(plan.runner.code_field || "code", plan.runner.code);
    body.set(plan.runner.payload_field || "post_data", JSON.stringify(payload));
    body.set(plan.runner.tab_id_field || "tab_id", `statebreaker-browser-context-${{suffix}}-${{Date.now()}}`);
    return body;
  }};

  const sendOne = async (context, destination, suffix) => {{
    const response = await fetch(context.runUrl, {{
      method: "POST",
      credentials: "same-origin",
      headers: {{"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}},
      body: makeBody(context, destination, suffix),
    }});
    const text = await response.text();
    let parsed = null;
    try {{ parsed = JSON.parse(text); }} catch (_) {{}}
    return {{
      status: response.status,
      output: parsed && typeof parsed.output === "string" ? parsed.output : text.slice(0, 4000),
    }};
  }};

  const formatTranscript = (rows, balances) => {{
    const total = Object.values(balances).reduce((acc, value) => acc + value, 0n);
    const visibleRows = rows.slice(-(plan.display?.recent_rows ?? rows.length));
    const lines = [
      "STATEBREAKER BROWSER-CONTEXT EXECUTOR",
      `Plan: ${{plan.name || "browser-context-plan"}}`,
      "Schedule: highest source balance, integer floor(balance * 0.9)",
      "",
      `Final Total Balance: ${{centsToMoney(total)}}`,
      ...Object.entries(balances).map(([name, value]) => `Final ${{name.padEnd(20)}}: ${{centsToMoney(value)}}`),
      "",
      `Recent successful rounds: ${{rows.filter((row) => row.okA && row.okB).length}} / ${{rows.length}}`,
      "Round   Source                Amount                          Destinations                           Total After",
      "-".repeat(150),
    ];
    for (const row of visibleRows) {{
      lines.push(
        `${{("#" + row.round).padEnd(8)}}${{row.source.padEnd(22)}}${{row.amountFormatted.padEnd(32)}}${{(row.destA + " + " + row.destB).padEnd(39)}}${{row.total}}`
      );
    }}
    return lines.join("\\n");
  }};

  const run = async (options = {{}}) => {{
    const root = planRoot();
    const output = document.querySelector(plan.selectors.output);
    const balances = Object.fromEntries(
      Object.entries(plan.schedule.initial_balances).map(([name, value]) => [name, moneyToCents(value)])
    );
    const rows = [];
    const rounds = Number(options.rounds ?? plan.schedule.rounds ?? 1);
    const startRound = Number(options.startRound ?? plan.schedule.start_round ?? 1);
    const token = readToken();
    const runUrl = new URL(root.getAttribute(plan.runner.run_url_attribute), location.href).toString();
    const exerciseId = root.getAttribute(plan.runner.exercise_attribute);
    if (!exerciseId) throw new Error(`exercise attribute not found: ${{plan.runner.exercise_attribute}}`);

    for (let index = 0; index < rounds; index += 1) {{
      const step = chooseSchedule(balances);
      const context = {{...step, token, runUrl, exerciseId, code: plan.runner.code}};
      const startedAt = Date.now();
      const [resultA, resultB] = await Promise.all([
        sendOne(context, step.destA, "A"),
        sendOne(context, step.destB, "B"),
      ]);
      const parsedA = parseResult(resultA.output, step.source, step.destA);
      const parsedB = parseResult(resultB.output, step.source, step.destB);
      if (parsedA.ok) {{
        balances[step.source] = parsedA.sourceFinal;
        balances[step.destA] = parsedA.destinationFinal;
      }}
      if (parsedB.ok) {{
        balances[step.source] = parsedB.sourceFinal;
        balances[step.destB] = parsedB.destinationFinal;
      }}
      const total = Object.values(balances).reduce((acc, value) => acc + value, 0n);
      const row = {{
        round: startRound + index,
        source: step.source,
        destA: step.destA,
        destB: step.destB,
        amount: step.amount,
        amountFormatted: centsToMoney(BigInt(step.amount) * 100n),
        okA: parsedA.ok,
        okB: parsedB.ok,
        statusA: resultA.status,
        statusB: resultB.status,
        elapsedMs: Date.now() - startedAt,
        total: centsToMoney(total),
        outA: resultA.output.slice(0, 1600),
        outB: resultB.output.slice(0, 1600),
      }};
      rows.push(row);
      if (output && plan.display?.live_update !== false) {{
        output.textContent = formatTranscript(rows, balances);
      }}
      if (!row.okA || !row.okB) break;
    }}

    if (output && plan.display?.output_flood !== false) {{
      output.textContent = formatTranscript(rows, balances);
      Object.assign(output.style, {{
        fontSize: plan.display?.font_size || "20px",
        lineHeight: "1.45",
        fontFamily: "Consolas, Menlo, monospace",
        whiteSpace: "pre",
        maxHeight: plan.display?.max_height || "980px",
        minHeight: plan.display?.min_height || "820px",
        overflow: "auto",
        border: "4px solid #b91c1c",
        background: "#fff7ed",
        color: "#111827",
        padding: "18px",
      }});
      if (plan.display?.scroll_to_bottom) output.scrollTop = output.scrollHeight;
      output.scrollIntoView({{block: "center"}});
    }}

    const finalBalances = Object.fromEntries(Object.entries(balances).map(([name, value]) => [name, centsToMoney(value)]));
    const finalTotal = centsToMoney(Object.values(balances).reduce((acc, value) => acc + value, 0n));
    const result = {{
      plan: plan.name || "browser-context-plan",
      generatedBy: "statebreaker browser-context render",
      rows,
      finalBalances,
      finalTotal,
      policy: "No Cookie/localStorage dump; page token used only in-page and not returned.",
    }};
    window.StateBreakerBrowserContextResult = result;
    return result;
  }};

  window.StateBreakerBrowserContextRunner = {{run, plan}};
  return plan.autorun === false ? {{ready: true, plan: plan.name || "browser-context-plan"}} : run();
}})();
"""


def render_browser_context_executor_from_plan(plan: JsonObject) -> str:
    """Render a standalone browser-context executor from a prepared plan object."""
    _validate_plan(plan)
    runner = _require_mapping(plan, "runner")
    if not isinstance(runner.get("code"), str) or not runner["code"]:
        raise StateBreakerError("browser-context plan needs embedded runner.code")
    return _browser_runtime_source(plan)


def render_browser_context_executor(
    plan_path: Path,
    *,
    har_path: Path | None = None,
    rounds: int | None = None,
    start_round: int | None = None,
    autorun: bool | None = None,
) -> str:
    """Render a standalone browser-context executor JavaScript program."""
    return render_browser_context_executor_from_plan(
        prepare_browser_context_plan(
            plan_path,
            har_path=har_path,
            rounds=rounds,
            start_round=start_round,
            autorun=autorun,
        )
    )
