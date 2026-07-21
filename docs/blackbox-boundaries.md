# 黑盒边界 / Black-Box Boundaries

StateBreaker 的核心假设是：只从一段授权 normal flow 和后续 HTTP 实验中学习，不读取目标应用内部状态。

## 工具能看见什么 / What It Can See

- `CapturedTrace` 中的公开 HTTP 请求和响应：method、URL、headers、body、status、响应 body。
- `project.yaml` 中的授权范围、base URL、预算、reset 策略和 session 配置。
- 扫描期间真实发送的 control/attack 请求，以及对应的 `ExecutionTrial` 结果。
- 通过普通 HTTP probe 读回来的状态变化，例如再次 GET 某个资源。

## 工具不能看见什么 / What It Cannot See

- 目标服务的数据库、内存、锁、队列、线程、源码或私有变量。
- 靶场内部对象、fixture 私有状态或 Python 模块变量。
- 任何没有通过 HTTP normal flow、HTTP probe 或 trial evidence 暴露出来的事实。

## Reset 的角色 / Role of Reset

reset endpoint 只用于实验隔离：每轮 control/attack 前把授权测试环境恢复到干净状态。它不是业务接口，不参与 workflow graph、dependency inference、baseline 或 finding verdict。

如果目标没有 reset 能力，也可以接入其他 `ResetStrategy`，但仍要保持同一原则：reset 只保证实验可重复，不提供内部答案。

## CONFIRMED 的门槛 / CONFIRMED Evidence Gate

`CONFIRMED` finding 必须引用真实 `ExecutionTrial`：

- 至少能说明 control 和 attack 的行为差异。
- evidence refs 必须指向已保存的 trial 记录。
- JSON bundle 和报告只是展示这些证据，不能凭空生成结论。
- HTTP 200、异常响应、路径名称或靶场名称都不能单独作为确认依据。

## ASGI 测试传输 / ASGI Test Transport

本地测试使用 ASGI transport 是为了避免开真实端口、让测试更快更稳定。它仍然只发送 HTTP request、接收 HTTP response，不读取 FastAPI app 的私有状态。因此高级靶场测试仍按黑盒边界理解。
