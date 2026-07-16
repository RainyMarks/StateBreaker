# 架构说明

## 设计目标

StateBreaker 核心是“契约 + 共享管道”，不是某一种攻击算法。组员可以各自维护独立 Python 包，通过 entry point 接入，而不修改核心注册表。

```text
正常流量 / HAR / YAML
          │
    capture plugin
          ▼
       Workflow ── learner ──> StateProfile + Invariant[]
          │                              │
          └──────── generator ───────────┘
                         ▼
                    AttackPlan
                         │
                      executor
                         ▼
                  RawAttackResult
                         │
                     verifier
                         ▼
                      Finding[] ── reporter ──> artifacts
```

## 核心边界

`src/statebreaker` 分为四层：

- `models.py`：唯一公共数据源；插件之间不得交换未建模的私有对象。
- `runtime.py`：串行 HTTP、会话、变量、提取器和事件日志；不调度并发攻击。
- `plugins.py`：插件 Protocol、manifest 校验、发现和冲突检测。
- `cli.py`：输入校验、插件分发、输出校验和稳定退出码。

共享运行时按 session 名创建独立 `httpx.AsyncClient`。插件可并发调用 `execute_step()`，但并发数量、屏障、原始套接字和时间偏移均属于 executor 插件自身。

## 数据流与产物

一次运行创建 `.statebreaker/runs/<run_id>/`。核心至少维护：

- `events.jsonl`：按发生顺序追加的脱敏事件；
- 插件通过 CLI 输出的版本化 JSON 文档；
- correlation ID、step ID、request ordinal 和单调时钟时间。

插件输出返回 CLI 后必须再次经过 Pydantic 校验。数据不兼容时立即失败，不能生成看似正常的空报告。

## 版本策略

- 核心包版本遵循语义化版本；当前为 `0.1.0`。
- 公共数据和插件 API 使用 `0.1`。
- 插件 manifest 必须精确声明兼容 API；核心拒绝不兼容插件。
- 修改字段语义或插件方法签名时必须升级 API 版本并保留迁移说明。
