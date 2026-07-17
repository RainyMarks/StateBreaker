# StateBreaker 插件开发指南

## 1. 为什么使用独立插件包

核心只维护数据契约、HTTP runtime、插件发现和 CLI。每个算法放在独立 Python 包中，
组员可以各建分支，安装后由 Entry Point 接入，不需要修改中心注册表。

## 2. 六类接口

| Entry Point 组 | 必需异步方法 |
|---|---|
| `statebreaker.capture` | `capture(source, options) -> Workflow` |
| `statebreaker.learner` | `learn(workflow, runtime) -> LearningResult` |
| `statebreaker.generator` | `generate(workflow, invariants) -> list[AttackPlan]` |
| `statebreaker.executor` | `execute(plan, runtime) -> RawAttackResult` |
| `statebreaker.verifier` | `verify(result, invariants) -> list[Finding]` |
| `statebreaker.reporter` | `render(bundle, output_dir) -> ReportArtifacts` |

例如 generator 只需要理解 Workflow、Invariant 和 AttackPlan，不需要知道 HAR 如何抓取，
也不需要知道 PDF 如何生成。

## 3. 创建包

可复制 `plugin-template`，保留标准 `src/` 布局：

```toml
[project]
dependencies = ["statebreaker>=0.1,<0.2"]

[project.entry-points."statebreaker.executor"]
my-executor = "my_package.plugin:MyExecutor"
```

每个插件公开 manifest：

```python
manifest = PluginManifest(
    plugin_id="team.concurrent-basic",
    name="Basic concurrent executor",
    version="0.1.0",
    api_version="0.1",
    group="statebreaker.executor",
    capabilities=["asyncio-burst"],
)
```

plugin ID 在同组内必须唯一。错误 group、API 不兼容、重复 ID 或缺少方法都会在执行前
失败。

## 4. 使用共享 runtime

learner/executor 可使用：

- `runtime.variables`：本轮动态变量；
- `runtime.execute_step(step, request_ordinal=n)`：发送标准化请求；
- `runtime.emit(...)`：追加自定义证据事件；
- `runtime.events` / `runtime.responses`：构造结果；
- `runtime.run_id` / `runtime.run_dir`：隔离运行产物。

并发调度由 executor 自己实现，但必须有明确的并发上限、超时和停止条件。

## 5. 本地安装和发现

```powershell
python -m pip install -e .\my-plugin
statebreaker plugins list
```

安装后可单独调用该阶段，也可替换自动 pipeline 中对应 ID：

```powershell
statebreaker attack plan.json --workflow workflow.yaml --plugin my.executor
statebreaker pipeline run workflow.yaml invariants.yaml --executor my.executor
```

## 6. 测试要求

每个插件至少测试：

- manifest、Entry Point 发现和 API 兼容；
- 正常输入输出模型；
- 坏配置、坏文档和插件异常；
- 确定性输出或有界调度；
- 不泄露 Token、Cookie、Authorization 等敏感值；
- 与核心 CLI 的最小集成调用。

推荐命令：

```powershell
pytest .\my-plugin\tests
ruff check .\my-plugin
mypy .\my-plugin\src
```

## 7. 当前参考实现

| 包 | 可参考内容 |
|---|---|
| `plugins/statebreaker-har-capture` | HAR 解析、options、独立 CI |
| `statebreaker-learner-delta` | runtime 重放和候选规则 |
| `race-generator` | 确定性计划生成和 hard cap |
| `race-executor` | 有界并发、状态探针和时间线 |
| `statebreaker-verifier-basic` | 多种 invariant 判定 |
| `statebreaker-reporter-pdf` | RunBundle 到文件产物 |

插件必须能在不修改 `src/statebreaker` 的情况下独立安装和发现。
