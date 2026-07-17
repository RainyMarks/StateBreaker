# StateBreaker Race Generator

`race-generator` 是本项目中负责“攻击计划生成算法”的独立插件。它实现
`statebreaker.generator` 接口，读取已建模的 `Workflow` 和已确认的
`Invariant[]`，为优惠券竞态问题生成有界、确定性、可复现的 `AttackPlan[]`。

该插件只生成计划，不直接发送网络请求。真实请求执行由 executor 插件负责。

## 插件信息

- 插件 ID：`team.race-generator`
- 插件组：`statebreaker.generator`
- 入口类：`statebreaker_race_generator.plugin:RaceAttackGenerator`
- 输入：`Workflow + list[Invariant]`
- 输出：`list[AttackPlan]`
- 当前目标：优惠券单次使用、最大优惠增量、次数限制和状态转换类规则

## 已实现能力

当前 generator 会为优惠券目标步骤生成七类攻击计划：

| 攻击类型 | 目的 | 当前靶场预期 |
|---|---|---|
| `concurrent-replay` | 两个兑换请求零偏移并发重放，复现最小竞态窗口 | 成功，最终优惠 100 |
| `burst-replay` | 四个兑换请求零偏移突发重放，观察漏洞放大效果 | 成功，最终优惠 200 |
| `offset-sweep` | 使用 10、50、100、140 ms 偏移扫描可利用窗口 | 前三个通常成功，140 ms 通常错过窗口 |
| `precondition-bypass-replay` | 跳过前置状态探针后顺序重复兑换，验证服务端是否兜底 | 失败/阴性对照，后续请求返回 409 |
| `idempotency-key-reuse` | 两个请求复用同一个 `X-Request-ID`，检测幂等去重缺失 | 成功，当前靶场只记录该 header |
| `stale-state-assisted-replay` | 首个兑换请求进入等待窗口时读取中间状态，再补第二个请求 | 成功，中间状态仍显示未使用 |
| `run-eviction-pressure` | 创建 101 个新实验，检测固定大小 run 池是否挤出活动状态 | 原 run 返回 404，属于状态可用性风险 |

## 生成逻辑

插件会先筛选适用的优惠券规则。目前支持以下 invariant 类型：

- `max-delta`
- `single-use`
- `count-limit`
- `state-transition`

随后根据 `before_probe` 和 `after_probe` 定位中间的 action 步骤，并优先选择：

- 带有 `attack-target` 标签的步骤；
- 步骤 ID、路径或标签中包含 `coupon`、`discount`、`优惠`、`券` 的步骤。

生成出的每个计划都会包含：

- `target_steps`：真实存在的目标步骤；
- `session_bindings`：目标步骤使用的会话；
- `invariant_ids`：关联的规则；
- `schedule.concurrency`：并发数；
- `schedule.offsets_ms`：每个请求的时间偏移；
- `schedule.options`：具体策略参数，例如重复次数、跳过步骤、共享请求 ID 等；
- `metadata`：生成器版本、目的、当前靶场预期和安全边界说明。

所有计划都会在返回前校验步骤、会话、规则引用和 offset 数量，避免生成无法执行的计划。

每个计划的 `metadata.invariant` 会嵌入完整规则快照（id/kind/selector/parameters/probes），
供 executor 按 selector 评估 `vulnerability_observed`，而无需再次加载 invariants 文件。

总计划数硬上限为 40，避免多条 invariant 时组合爆炸。`max_attempts` 默认 1；若设为更大值，
executor 要求同时设置 `reset_before_retry=true`。

## 安装和发现

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .\race-generator
.\.venv\Scripts\statebreaker.exe plugins list --group statebreaker.generator
```

预期可以看到：

```text
statebreaker.generator  team.race-generator  0.1.0
```

## 生成攻击计划

```powershell
.\.venv\Scripts\statebreaker.exe generate `
  .\examples\coupon-race\workflow.yaml `
  .\examples\coupon-race\invariants.yaml `
  --plugin team.race-generator `
  --output .\.statebreaker\coupon-plans.json
```

当前示例会生成 10 条计划：

- 1 条 `concurrent-replay`
- 1 条 `burst-replay`
- 1 条 `precondition-bypass-replay`
- 1 条 `idempotency-key-reuse`
- 1 条 `stale-state-assisted-replay`
- 1 条 `run-eviction-pressure`
- 4 条 `offset-sweep`

## 配合靶场检测

generator 只负责生成计划。若要在优惠券靶场中执行这些计划，需要安装
`race-executor`：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .\race-executor

.\.venv\Scripts\statebreaker-coupon-audit.exe `
  .\examples\coupon-race\workflow.yaml `
  .\.statebreaker\coupon-plans.json `
  --output-dir .\.statebreaker\coupon-audit
```

检测摘要位于：

```text
.statebreaker/coupon-audit/summary.json
```

重点字段：

- `vulnerability_observed`：是否确认优惠券多次生效；
- `stale_state_observed`：是否观测到提交窗口内的陈旧状态；
- `run_evicted`：原实验状态是否被固定容量 run 池挤出；
- `discount_delta`：攻击前后的优惠金额变化；
- `successful_redemptions`：服务端记录的成功兑换次数；
- `target_status_codes`：目标请求响应码。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest .\race-generator\tests -q
.\.venv\Scripts\python.exe -m ruff check .\race-generator
```

推荐在提交前同时运行 executor 测试，确保生成计划仍能被执行：

```powershell
.\.venv\Scripts\python.exe -m pytest .\race-generator\tests .\race-executor\tests -q
```

## 安全边界

该插件仅用于本地靶场或已明确授权的系统。生成的并发数、重复次数和辅助请求数都有固定上限。

`precondition-bypass-replay` 不会也不能从客户端删除服务端 `coupon_used` 判断；
它是阴性对照，用于区分“客户端流程限制”和“服务端真实状态约束”。

`run-eviction-pressure` 是状态可用性检测，不是优惠券多次使用攻击；它用于说明当前靶场的
`MAX_RUNS=100` 固定容量策略会让旧实验状态被新实验挤出。
