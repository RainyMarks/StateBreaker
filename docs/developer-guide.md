# StateBreaker 开发入口指南

这份文档回答一个问题：想改某类功能时，应该先看哪里。

## 修改前先确认

每次改动前先明确：

- 是否会改变 CLI 参数或输出。
- 是否会改变 artifact JSON 字段。
- 是否会影响 `CONFIRMED` finding 的证据链。
- 是否会把业务词带进 `src/statebreaker/`。

默认第一轮重构不改变这些公共接口。

## 常见改动入口

| 想改什么 | 先看哪里 | 注意事项 |
| --- | --- | --- |
| 导入新流量格式 | `src/statebreaker/capture/` | 输出必须是现有 `CapturedTrace` 或 `RequestTemplate` |
| 变量/依赖推断 | `src/statebreaker/intelligence/` | 只能使用结构信号，不能写业务词规则 |
| 状态探针 | `src/statebreaker/intelligence/probe_discovery.py` | probe 是观察状态，不是判定漏洞 |
| 正常行为学习 | `src/statebreaker/baseline/` | invariants 必须记录 supporting trial ids |
| 候选评分 | `src/statebreaker/discovery/` | 分数解释要能对应 observable behavior |
| 计划生成 | `src/statebreaker/planning/` | 不要手写目标业务计划 |
| 请求执行 | `src/statebreaker/execution/` | 保持 scope、budget、session 隔离 |
| 新 scheduler | `src/statebreaker/execution/transports/` | 实现 `SchedulerBackend`，只负责 prepare/release |
| 判定逻辑 | `src/statebreaker/oracle/` | confirmed verdict 必须有 trial 证据 |
| 报告输出 | `src/statebreaker/reporting/` | 只在展示层 redaction，不改 stored evidence |
| CLI 命令 | `src/statebreaker/cli/` | 保持已有命令兼容 |

## 一次功能改动的推荐顺序

1. 先补或定位测试，明确正常组和异常组。
2. 再改模型外的业务逻辑；除非必须，不改 `models/`。
3. 如果新增 artifact 字段，需要同时补 round-trip 测试和迁移说明。
4. 跑相关测试，例如 `pytest tests/orchestration/test_scanner.py -q`。
5. 最后跑完整 `python check.py`。

## 不要做的事

- 不要在核心包里写目标业务词规则。
- 不要为了让测试过而伪造 finding。
- 不要让 response-only 的成功直接变成 confirmed，除非配置明确关闭状态证据要求。
- 不要绕过 `BudgetTracker`、`ScopeGuard`、`SessionManager`。
- 不要让 report 反向修改 trial、finding 或 baseline。

## 判断改动有没有把架构弄乱

一个健康改动应该满足：

- 新人能在 `architecture.md` 的流程图里找到它属于哪个阶段。
- 输入输出仍然是现有模型或清晰的内部 dataclass。
- 测试能说明它对正常组和异常组分别产生什么影响。
- 失败时能给出可解释错误，而不是静默丢证据。
