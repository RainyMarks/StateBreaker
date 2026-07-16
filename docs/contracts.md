# 数据契约

## Workflow

Workflow 描述一条正常业务操作，不描述攻击线程：

- `base_url`：目标根地址，不做主机白名单限制；
- `sessions`：命名会话及初始 Header/Cookie；
- `variables`：静态变量；
- `steps`：按声明顺序执行；依赖步骤必须出现在当前步骤之前；
- `state_probe_steps`：角色必须为 `probe` 的状态查询步骤。

请求模板统一使用 `${name}`。当整个字段只有一个占位符时保留变量原始类型；嵌入字符串时转成文本。变量必须在当前步骤前由 Workflow 初值或 extractor 产生，否则校验失败。

## Extractor

- `jsonpath`：对 JSON 响应求值并取第一个结果；
- `header`：不区分大小写读取响应 Header；
- `regex`：优先取命名组 `value`，其次取第一个捕获组，最后取完整匹配。

`required: true` 的提取器没有结果时，运行立即失败。

## AttackPlan

AttackPlan 只描述攻击意图：目标步骤、会话绑定、并发数量、时间偏移、插件选项和关联规则。核心不解释这些参数；executor 插件负责执行并返回 `RawAttackResult`。

## Evidence

`RunEvent` 同时记录 UTC 时间和 `monotonic_ns`，以便报告插件重建相对时间线。敏感键在事件日志中替换为 `<redacted>`。

`Finding` 的三种结论是：

- `confirmed`：独立状态证据确认规则被破坏；
- `probable`：响应异常但缺少充分状态证据；
- `rejected`：请求看似成功但状态没有越界。

核心只定义结论格式，不自动得出结论。

运行以下命令获取机器可读契约：

```powershell
statebreaker schema export .\schemas
```
