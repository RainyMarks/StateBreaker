const ui = {
  runId: document.querySelector("#run-id"),
  discount: document.querySelector("#discount"),
  successCount: document.querySelector("#success-count"),
  couponState: document.querySelector("#coupon-state"),
  status: document.querySelector("#status-line"),
  events: document.querySelector("#events"),
  reset: document.querySelector("#reset-button"),
  single: document.querySelector("#single-button"),
  race: document.querySelector("#race-button"),
};

let currentRunId = null;
let busy = false;

function setBusy(next) {
  busy = next;
  [ui.reset, ui.single, ui.race].forEach((button) => { button.disabled = next; });
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function renderState(state) {
  ui.runId.textContent = state.run_id;
  ui.discount.textContent = String(state.discount_yuan);
  ui.successCount.textContent = String(state.successful_redemptions);
  ui.couponState.textContent = state.coupon_used ? "已使用" : "未使用";
  ui.couponState.style.color = state.discount_yuan > 50 ? "#ff8e76" : "#e7e143";
}

function renderEvents(events) {
  ui.events.replaceChildren();
  if (!events.length) {
    const empty = document.createElement("li");
    empty.className = "event empty";
    empty.textContent = "小票打印机还没收到消息。";
    ui.events.append(empty);
    return;
  }
  events.forEach((event) => {
    const item = document.createElement("li");
    item.className = "event";

    const sequence = document.createElement("span");
    sequence.className = "event-seq";
    sequence.textContent = String(event.sequence).padStart(2, "0");

    const kind = document.createElement("span");
    const shortKind = event.kind.replace("coupon.", "");
    kind.className = `event-kind ${shortKind === "committed" ? "commit" : "check"}`;
    kind.textContent = event.kind;

    const message = document.createElement("span");
    message.className = "event-message";
    message.textContent = event.message;

    const state = document.createElement("span");
    state.className = "event-state";
    state.textContent = `¥${event.snapshot.discount_yuan} / ${event.request_id.slice(0, 6)}`;

    item.append(sequence, kind, message, state);
    ui.events.append(item);
  });
}

async function refresh() {
  if (!currentRunId) return;
  const [state, timeline] = await Promise.all([
    requestJson(`/api/runs/${currentRunId}/state`),
    requestJson(`/api/runs/${currentRunId}/events`),
  ]);
  renderState(state);
  renderEvents(timeline.events);
}

async function resetRun() {
  setBusy(true);
  ui.status.textContent = "正在开新桌，顺便把上一桌的羊毛扫掉……";
  try {
    const state = await requestJson("/api/runs", { method: "POST", body: "{}" });
    currentRunId = state.run_id;
    renderState(state);
    await refresh();
    ui.status.textContent = "新桌已开。BUG50 看起来非常自信。";
  } catch (error) {
    ui.status.textContent = `开桌失败：${error.message}`;
  } finally {
    setBusy(false);
  }
}

async function sendRedeem(label) {
  const requestId = `${label}-${crypto.randomUUID()}`;
  return requestJson(`/api/runs/${currentRunId}/redeem`, {
    method: "POST",
    headers: { "X-Request-ID": requestId },
    body: JSON.stringify({ coupon_code: "BUG50" }),
  });
}

async function redeemOnce() {
  if (!currentRunId || busy) return;
  setBusy(true);
  ui.status.textContent = "老实排队兑换中……";
  try {
    await sendRedeem("honest");
    ui.status.textContent = "兑换成功：老王给你减了 50 元。";
  } catch (error) {
    ui.status.textContent = `兑换被拒：${error.message}`;
  } finally {
    await refresh();
    setBusy(false);
  }
}

async function triggerRace() {
  if (!currentRunId || busy) return;
  setBusy(true);
  ui.status.textContent = "两只手同时伸向收银台……";
  const results = await Promise.allSettled([sendRedeem("left"), sendRedeem("right")]);
  const succeeded = results.filter((result) => result.status === "fulfilled").length;
  await refresh();
  ui.status.textContent = succeeded === 2
    ? "状态已打破：一张券成功兑换两次，优惠变成 100 元。"
    : `本轮成功 ${succeeded} 次；请开新桌后再试。`;
  setBusy(false);
}

ui.reset.addEventListener("click", resetRun);
ui.single.addEventListener("click", redeemOnce);
ui.race.addEventListener("click", triggerRace);
resetRun();
