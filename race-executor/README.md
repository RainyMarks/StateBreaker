# StateBreaker Race Executor

这是一个 `statebreaker.executor` 插件，用于在本地优惠券竞态靶场中执行
`team.race-generator` 生成的攻击计划。它把多种优惠券攻击模式汇总到同一个
`execute(plan, runtime)` 接口，并保持请求量有界。

支持的模式：

- `concurrent-replay`：两个兑换请求同时提交，验证最小竞态复现。
- `burst-replay`：四个兑换请求同时提交，观察漏洞放大效果。
- `offset-sweep`：两个请求按固定毫秒偏移提交，扫描 150ms 左右的检查到写入窗口。
- `precondition-bypass-replay`：跳过前置状态探针，顺序重复提交，验证服务端是否兜底。
- `idempotency-key-reuse`：使用相同 `X-Request-ID` 并发提交，验证是否缺少幂等去重。
- `stale-state-assisted-replay`：在首个兑换请求尚未提交时读取中间状态，利用“仍未使用”的陈旧状态触发后续兑换。
- `run-eviction-pressure`：创建 101 个新实验，验证固定大小状态池是否会让原实验不可用。

该插件只面向本地靶场或已明确授权的环境。默认单个计划最多发送 16 个目标请求；
当前 generator 产出的计划最大并发为 4。`run-eviction-pressure` 会额外发送 101 个
创建实验请求，它是有界状态可用性检测，不是优惠券多次使用漏洞。

## 判定语义（重要）

`plugin_data.vulnerability_observed` **不是** 正式 `Finding`：

- 优先读取 plan metadata 中 generator 嵌入的 `invariant`（selector + kind + parameters）；
- 例如 `max-delta`：用攻击前后状态按 selector 取值，若 `after - before > max_delta` 则记为 observed；
- 无法评估 invariant 时才回退到本地启发式；
- `is_formal_finding` 恒为 `false`。正式结论请使用 `statebreaker.verifier` 插件。

非法计划 / 超限参数会抛出 `PluginError`，以便 CLI 以插件退出码失败。

`schedule.options` 约定：

| 选项 | 行为 |
|---|---|
| `continue_on_rejection` | 串行重放时，非 2xx 后是否继续 |
| `max_attempts` | 并发突发重试次数；`>1` 时必须 `reset_before_retry=true` |
| `reset_before_retry` | 每次重试前重跑 target 之前的 setup/probe |
| `required_executor_capability` | 执行前检查 executor capability 是否具备 |

## 安装

```powershell
python -m pip install -e .\race-generator
python -m pip install -e .\race-executor
statebreaker plugins list
```

## 单个计划执行

`statebreaker attack` 一次读取一个 `AttackPlan`：

```powershell
statebreaker attack .\one-plan.json `
  --workflow .\examples\coupon-race\workflow.yaml `
  --plugin team.race-executor `
  --output .\.statebreaker\one-result.json
```

## 批量检测

先生成所有计划，再用批量入口逐个检测：

```powershell
statebreaker generate `
  .\examples\coupon-race\workflow.yaml `
  .\examples\coupon-race\invariants.yaml `
  --plugin team.race-generator `
  --output .\.statebreaker\coupon-plans.json

statebreaker-coupon-audit `
  .\examples\coupon-race\workflow.yaml `
  .\.statebreaker\coupon-plans.json `
  --output-dir .\.statebreaker\coupon-audit
```

批量入口会输出 `summary.json` 和每个计划的原始执行结果。重点看
`plugin_data.vulnerability_observed`、`discount_delta`、`successful_redemptions`
和 `lab_event_kinds`。
