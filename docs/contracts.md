# StateBreaker 数据契约

所有公共模型位于 `src/statebreaker/models.py`，使用 Pydantic 严格校验，并携带
`schema_version: "0.1"`。插件之间只能通过这些模型交换数据。

## Workflow

描述一条正常业务操作，不描述攻击线程：

- `base_url`：目标根地址；CLI 可用 `--target` 临时覆盖；
- `sessions`：命名会话及初始 Header/Cookie；
- `variables`：静态变量；
- `steps`：按声明顺序执行的 `RequestStep[]`；
- `state_probe_steps`：攻击前后读取业务状态的 probe 步骤。

请求模板使用 `${name}`。变量必须来自 Workflow 初值或更早步骤的 Extractor，否则
Workflow 校验失败。请求支持 Header、Query、JSON Body、Form Body 和独立超时。

## Extractor

| kind | 行为 |
|---|---|
| `jsonpath` | 对 JSON 响应求值并取第一个匹配 |
| `header` | 从响应 Header 读取值 |
| `regex` | 优先 `value` 命名组，其次第一个捕获组 |

`required: true` 的 Extractor 无结果时立即失败。

## LearningResult 与 Invariant

`LearningResult` 包含 `StateProfile` 和候选 `Invariant[]`。当前 learner 产生的是基于正常
样本的候选边界，测试人员仍应确认其业务含义。

Invariant 通过 `selector` 指向状态字段，并用 `parameters` 表达规则，例如：

```yaml
id: coupon-max-delta
kind: max-delta
selector: $.discount_yuan
parameters:
  max_delta: 50
```

## AttackPlan

AttackPlan 表达攻击意图：

- `attack_type` 和 `target_steps`；
- `session_bindings`；
- `schedule.concurrency`、`offsets_ms`、插件 options；
- `invariant_ids` 和 metadata。

核心不解释并发算法，executor 负责执行。

## RawAttackResult 与 RunEvent

RawAttackResult 保存：

- HTTP `responses`；
- `before_state` / `after_state`；
- 完整 `events`；
- executor 的 `plugin_data`。

RunEvent 同时记录 UTC 时间和 `monotonic_ns`，用于重建毫秒时间线。敏感字段在事件日志
中替换为 `<redacted>`。

## Finding

| verdict | 含义 |
|---|---|
| `confirmed` | 独立状态证据确认业务规则被破坏 |
| `probable` | 响应异常，但状态证据不完整 |
| `rejected` | 请求看似成功，最终状态没有越界 |

executor 的启发式布尔值不是 Finding；正式结论必须由 verifier 输出。

## RunBundle 与 ReportArtifacts

RunBundle 将 `Workflow + selected AttackPlan + RawAttackResult + Finding[]` 固定在同一份
报告输入中。Reporter 返回 ReportArtifacts，列出实际生成文件及 metadata。

## 导出 JSON Schema

```powershell
statebreaker schema export .\schemas
```
