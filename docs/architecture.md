# StateBreaker 架构说明

## 1. 设计目标

StateBreaker 核心提供“版本化数据契约 + HTTP 运行时 + 插件总线 + CLI 编排”，而不是
把所有安全算法写在一个包里。不同组员可以独立开发同一阶段的不同实现，也可以各自负责
不同阶段；核心按 Entry Point 自动发现插件。

```text
HAR / YAML / proxy output
          │
       capture
          ▼
       Workflow ───── learner ─────> LearningResult
          │                              │
          └──────── generator <── Invariant[]
                         ▼
                    AttackPlan[]
                         │ explicit select
                         ▼
                     AttackPlan
                         │
                      executor
                         ▼
                  RawAttackResult
                         │
                      verifier
                         ▼
                      Finding[]
                         │ + Workflow + AttackPlan + Result
                         ▼
                      RunBundle ── reporter ──> PDF/JSON
```

## 2. 核心代码边界

`src/statebreaker` 当前包含：

| 文件 | 职责 |
|---|---|
| `models.py` | 所有跨插件公共模型和 `schema_version=0.1` |
| `runtime.py` | HTTP 会话、Cookie、模板变量、Extractor、脱敏事件日志 |
| `plugins.py` | Protocol、manifest、Entry Point 发现、冲突和 API 检查 |
| `pipeline.py` | CI/批量模式的 generate→execute→verify→report 编排 |
| `documents.py` | YAML/JSON 读取、Pydantic 校验和确定性 JSON 写入 |
| `cli.py` | 分阶段命令、输入输出校验、详细过程显示和稳定退出码 |

核心 runtime 只负责标准请求管道。并发数、屏障、offset、Last-Byte Gate 等调度算法属于
executor 插件。这样更换攻击手法时不需要修改核心。

## 3. 两种运行方式

### 人工可检查模式

课堂演示和调试使用独立命令：

```text
workflow show/replay
→ invariants show
→ generate
→ plans list/select
→ attack
→ verify
→ bundle build
→ report
```

计划生成与真实执行分离，避免在测试人员尚未检查并发参数时自动发送请求。

### 自动化模式

`statebreaker pipeline run` 为 CI、回归和批处理保留。它调用相同插件和模型，并把完整
产物写入 `.statebreaker/runs/<run_id>/`，不是另一套算法。

## 4. 会话、变量与证据

- 每个 Workflow session 对应独立 `httpx.AsyncClient` 和 Cookie Jar；
- `${variable}` 在发送前递归替换；
- JSONPath/Header/Regex Extractor 将响应值写回 runtime variables；
- 每个请求记录 correlation ID、step ID、ordinal、UTC 时间和 `monotonic_ns`；
- Authorization、Cookie、password、token、secret 等字段写入事件日志前会脱敏；
- executor 返回 before/after 状态，verifier 才负责生成正式 Finding。

## 5. 当前插件

| 阶段 | plugin_id | 当前能力 |
|---|---|---|
| capture | `har.capture` | 离线 HAR 1.2、JSON/Form、认证请求 |
| learner | `team.delta-learner` | 多样本差分、候选 max-delta/min/state-transition |
| generator | `team.race-generator` | concurrent/burst/offset 等竞态计划 |
| executor | `team.race-executor` | 有界并发、时间线和状态证据 |
| verifier | `team.basic-verifier` | confirmed/probable/rejected |
| reporter | `team.pdf-reporter` | PDF 和 JSON 摘要 |

## 6. 版本和失败策略

- 核心包版本当前为 `0.1.0`，公共 API 为 `0.1`；
- plugin manifest 必须声明兼容 API 和唯一 plugin ID；
- 插件输出返回核心后再次经过 Pydantic 校验；
- 输入错误退出码 2，插件错误退出码 3，运行时错误退出码 4；
- 不兼容或缺失插件会明确失败，不生成伪成功产物。
