# StateBreaker v0.1 新版 CLI

新版 CLI 负责把组员独立开发的插件连接起来。它本身不实现抓包算法、竞态调度算法、
规则判断算法或报告排版算法；这些能力仍由 Entry Point 插件提供。

## 1. 安装与自检

```powershell
python -m pip install -e ".[dev]"
python -m pip install -e .\race-generator
python -m pip install -e .\race-executor
python -m pip install -e .\statebreaker-verifier-basic
python -m pip install -e .\statebreaker-reporter-pdf
statebreaker --version
statebreaker doctor
statebreaker plugins list
```

`plugins list` 必须能看到你准备调用的插件 ID。缺少插件时，CLI 会以退出码 3 失败并
列出同一插件组中可用的 ID，不会假装执行成功。

## 2. 一键运行本地演示

先启动“老王奶茶券”靶场：

```powershell
$env:STATEBREAKER_LAB_PORT = "18080"
docker compose up --build -d
```

再运行：

```powershell
statebreaker demo
```

`demo` 等价于使用以下默认配置运行完整流水线：

| 位置 | 默认值 |
|---|---|
| Workflow | `examples/coupon-race/workflow.yaml` |
| Invariants | `examples/coupon-race/invariants.yaml` |
| Target | `http://127.0.0.1:18080` |
| Generator | `team.race-generator` |
| Executor | `team.race-executor` |
| Verifier | `team.basic-verifier` |
| Reporter | `team.pdf-reporter` |
| Attack type | `concurrent-replay` |

常用覆盖参数：

```powershell
statebreaker demo --target http://127.0.0.1:8080
statebreaker demo --no-report
statebreaker demo --output-root .statebreaker/my-runs
```

## 3. 通用完整流水线

```powershell
statebreaker pipeline run <workflow.yaml> <invariants.yaml> [OPTIONS]
```

执行顺序固定为：

```text
Workflow + Invariant[]
        -> generator -> AttackPlan[]
        -> 选择一个计划
        -> executor  -> RawAttackResult
        -> verifier  -> Finding[]
        -> reporter  -> ReportArtifacts（可选）
```

完整示例：

```powershell
statebreaker pipeline run examples/coupon-race/workflow.yaml `
  examples/coupon-race/invariants.yaml `
  --generator team.race-generator `
  --executor team.race-executor `
  --verifier team.basic-verifier `
  --reporter team.pdf-reporter `
  --attack-type concurrent-replay `
  --target http://127.0.0.1:18080
```

计划选择是确定性的：指定 `--plan-id` 时精确选择该 ID；否则筛选
`--attack-type`，再按计划 ID 排序并选择第一个。这样 CI、老师演示和组员本地运行得到
相同的选择逻辑。

使用 `--no-report` 可以只运行到验证阶段。`--output-root` 指定所有 run 目录的父目录。

## 4. 工作流操作

只做静态校验，不发请求：

```powershell
statebreaker workflow validate workflow.yaml
```

顺序重放正常流程：

```powershell
statebreaker workflow replay workflow.yaml --target http://127.0.0.1:18080
```

重放结果包含 `responses.json`、最终 `variables.json` 和脱敏后的 `events.jsonl`。它适合
capture 插件作者确认生成的 Workflow 是否可执行。

调用 capture 插件导入流量：

```powershell
statebreaker workflow import traffic.har --plugin your.capture --output workflow.json
```

## 5. 单阶段调试命令

组员开发插件时不必每次运行完整流水线，可以只调用自己的阶段：

```powershell
statebreaker learn workflow.yaml --plugin your.learner -o learning-result.json
statebreaker generate workflow.yaml invariants.yaml --plugin your.generator -o plans.json
statebreaker attack plan.yaml --workflow workflow.yaml --plugin your.executor -o result.json
statebreaker verify result.json invariants.yaml --plugin your.verifier -o findings.json
statebreaker report bundle.json --plugin your.reporter --output-dir report
```

这些命令的输入和输出均按照 `src/statebreaker/models.py` 中的 Pydantic 契约验证。

## 6. 一次运行会保存什么

完整流水线写入 `.statebreaker/runs/<run_id>/`：

```text
workflow.json
invariants.json
attack-plans.json
selected-plan.json
raw-attack-result.json
findings.json
run-bundle.json
summary.json
events.jsonl
report/
  artifacts.json
  ... reporter 生成的文件
```

`summary.json` 适合脚本和 CI 读取；`run-bundle.json` 是 reporter 的标准输入；
`events.jsonl` 用于分析请求时序。

## 7. 组员如何接入自己的插件

组员只需在自己的独立 Python 包中实现对应异步接口，并注册 Entry Point。例如执行器：

```toml
[project.entry-points."statebreaker.executor"]
my-executor = "my_plugin:MyExecutor"
```

安装并确认发现：

```powershell
python -m pip install -e .\my-plugin
statebreaker plugins list
```

随后可以单独调试：

```powershell
statebreaker attack plan.yaml --workflow workflow.yaml --plugin my.executor
```

也可以直接接入完整流水线：

```powershell
statebreaker pipeline run workflow.yaml invariants.yaml --executor my.executor
```

因此，不同组员可以开发不同阶段，也可以各自实现同一阶段的不同算法。CLI 只按插件 ID
选择实现，核心代码不需要增加中心注册表。

## 8. 稳定退出码

| 退出码 | 含义 |
|---:|---|
| 0 | 成功 |
| 2 | Workflow、Invariant 或 AttackPlan 等输入校验失败 |
| 3 | 插件缺失、不兼容或输出违反契约 |
| 4 | HTTP 运行时、文件系统或其他预期执行错误 |

脚本和 CI 应根据退出码判断是否成功，不应只搜索终端文字。
