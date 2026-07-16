# 插件开发指南

## 创建插件

复制 `plugin-template`，将其改成独立 Python 包。插件依赖应写为：

```toml
dependencies = ["statebreaker>=0.1,<0.2"]
```

再选择一个 entry-point 组：

```toml
[project.entry-points."statebreaker.executor"]
my-executor = "my_package.plugin:MyExecutor"
```

支持的组：

| 组 | 必需异步方法 |
|---|---|
| `statebreaker.capture` | `capture(source, options) -> Workflow` |
| `statebreaker.learner` | `learn(workflow, runtime) -> LearningResult` |
| `statebreaker.generator` | `generate(workflow, invariants) -> list[AttackPlan]` |
| `statebreaker.executor` | `execute(plan, runtime) -> RawAttackResult` |
| `statebreaker.verifier` | `verify(result, invariants) -> list[Finding]` |
| `statebreaker.reporter` | `render(bundle, output_dir) -> ReportArtifacts` |

## Manifest

每个插件对象暴露 `manifest`：

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

`plugin_id` 在同组必须唯一。重复 ID、错误 group、API 不兼容和缺少方法都会在执行前报错。

## 共享运行时

executor/learner 可以使用：

- `runtime.variables`：本轮动态变量；
- `runtime.execute_step(step, request_ordinal=n)`：发送一次标准化请求；
- `runtime.emit(...)`：记录自定义事件；
- `runtime.events`、`runtime.responses`：生成结果证据；
- `runtime.run_id`、`runtime.run_dir`：隔离运行产物。

核心不限制目标地址。插件如果增加并发或重试，必须提供明确上限、超时和停止条件，并在文档中声明只用于授权环境。

## 接入验收

```powershell
python -m pip install -e .\your-plugin
statebreaker plugins list
pytest
```

插件必须能在不修改 `src/statebreaker` 的情况下安装、发现和运行。
