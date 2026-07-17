# StateBreaker v0.1 新版 CLI

新版 CLI 负责把组员独立开发的插件连接起来。它本身不实现抓包算法、竞态调度算法、
规则判断算法或报告排版算法；这些能力仍由 Entry Point 插件提供。

需要现场展示时优先阅读 [现场演示指南](DEMO_GUIDE_ZH.md)；本文保留完整命令参考。

## 1. 安装与自检

```powershell
python -m pip install -e ".[dev]"
python -m pip install -e .\race-generator
python -m pip install -e .\race-executor
python -m pip install -e .\statebreaker-verifier-basic
python -m pip install -e .\statebreaker-reporter-pdf
python -m pip install -e .\plugins\statebreaker-har-capture
statebreaker --version
statebreaker doctor
statebreaker plugins list
```

`plugins list` 必须能看到你准备调用的插件 ID。缺少插件时，CLI 会以退出码 3 失败并
列出同一插件组中可用的 ID，不会假装执行成功。

## 2. 真实竞态测试的分阶段流程

先启动“老王奶茶券”靶场：

```powershell
$env:STATEBREAKER_LAB_PORT = "18080"
docker compose up --build -d
```

### 第一步：查看或导入正常流程

```powershell
statebreaker workflow show examples/coupon-race/workflow.yaml `
  --target http://127.0.0.1:18080
```

如果从浏览器 HAR 开始：

```powershell
statebreaker workflow import recording.har --plugin har.capture `
  --options capture-options.yaml --output workflow.json
statebreaker workflow show workflow.json
```

`capture-options.yaml` 可以包含：

```yaml
state_probe_entry_indices: [1, 3]
strip_credentials: false
```

### 第二步：真实重放一次正常业务流程

```powershell
statebreaker workflow replay examples/coupon-race/workflow.yaml `
  --target http://127.0.0.1:18080
```

这一轮按顺序执行 `create → state-before → redeem once → state-after`。它用于证明正常
情况下优惠只从 0 增加到 50，而不是直接进行攻击。

### 第三步：确认业务规则

```powershell
statebreaker invariants show examples/coupon-race/invariants.yaml
```

示例规则是 `$.discount_yuan` 的单次增量不得超过 50。

### 第四步：生成并检查候选攻击计划

```powershell
statebreaker generate examples/coupon-race/workflow.yaml `
  examples/coupon-race/invariants.yaml `
  --plugin team.race-generator `
  --output .statebreaker/manual/plans.json
statebreaker plans list .statebreaker/manual/plans.json
```

此时只生成模型文件，不发送攻击请求。终端会列出 concurrent replay、burst replay、
offset sweep 等候选策略。

### 第五步：明确选择即将执行的竞态方案

```powershell
statebreaker plans select .statebreaker/manual/plans.json `
  --attack-type concurrent-replay `
  --output .statebreaker/manual/selected-plan.json
```

CLI 会显示目标步骤、并发数、时间偏移、会话绑定和关联规则。选择与执行是两个独立步骤，
便于测试人员在发请求前检查计划。

### 第六步：真实执行并发请求

```powershell
statebreaker attack .statebreaker/manual/selected-plan.json `
  --workflow examples/coupon-race/workflow.yaml `
  --target http://127.0.0.1:18080 `
  --plugin team.race-executor `
  --output .statebreaker/manual/result.json
```

终端会展示两个请求各自的 SEND/DONE 时间、到达间隔、HTTP 状态、服务器 check/commit
证据，以及 `discount_yuan: 0 → 100` 的状态变化。

### 第七步：独立验证业务规则

```powershell
statebreaker verify .statebreaker/manual/result.json `
  examples/coupon-race/invariants.yaml `
  --plugin team.basic-verifier `
  --output .statebreaker/manual/findings.json
```

验证器比较允许增量 50 和实际增量 100，输出 `CONFIRMED`。判断依据是业务状态，不是
“收到两个 HTTP 200”。

### 第八步：组装并生成报告

```powershell
statebreaker bundle build `
  --workflow examples/coupon-race/workflow.yaml `
  --target http://127.0.0.1:18080 `
  --plan .statebreaker/manual/selected-plan.json `
  --result .statebreaker/manual/result.json `
  --findings .statebreaker/manual/findings.json `
  --output .statebreaker/manual/run-bundle.json

statebreaker report .statebreaker/manual/run-bundle.json `
  --plugin team.pdf-reporter `
  --output-dir .statebreaker/manual/report
```

这套命令和数据接口是通用骨架；上面的插件则是 v0.1 针对奶茶券竞态完成的第一套参考
实现。更换系统时不需要修改核心 CLI，但需要提供新的 Workflow、Invariant、目标地址，
并选择能够理解该场景的插件。当前不能宣称这些参考插件已经覆盖所有业务逻辑漏洞。

## 3. 自动化流水线（CI 使用）

分阶段命令适合课堂展示和人工检查。`pipeline run` 仅作为 CI、回归测试或批量运行入口：

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

省略 `--reporter` 即可只运行到验证阶段。核心没有默认绑定奶茶券插件或
`concurrent-replay`：调用者必须明确提供 generator、executor、verifier，并用
`--plan-id` 或 `--attack-type` 选择计划。`--output-root` 只指定本次组合运行的证据目录。

## 4. 工作流操作参考

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
statebreaker workflow import traffic.har --plugin your.capture `
  --options capture-options.yaml --output workflow.json
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

## 6. 通用阶段产物与可选运行目录

StateBreaker 并不要求所有模块必须组成一次固定的“完整流水线”。每个阶段都可以独立运行，
并通过 `-o/--output` 把标准模型写到用户指定的位置：

| 阶段 | 标准输出 |
|---|---|
| Capture | `Workflow` |
| Learn | `LearningResult`（含 `StateProfile` 和 `Invariant[]`） |
| Generate | `AttackPlan[]` |
| Select | `AttackPlan` |
| Execute | `RawAttackResult`，以及真实请求产生的 `events.jsonl` |
| Verify | `Finding[]` |
| Bundle | `RunBundle` |
| Report | `ReportArtifacts` |

只有使用可选的 `pipeline run` 组合多个插件时，核心才会在
`.statebreaker/runs/<run_id>/`（或 `--output-root` 指定的位置）集中保存该次实验的证据：

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
`events.jsonl` 用于分析请求时序。这个目录是通用运行时的归档格式，不包含 `BUG50`、
`redeem` 或 `discount_yuan` 等奶茶券专用逻辑。

本仓库当前放入该契约的具体内容仍然是奶茶券实验。也就是说：**骨架可替换，v0.1 的首套
算法实现针对奶茶券竞态**。以后增加提款、邀请码或 Token 场景时，新插件继续读写同一组
模型，而不需要修改 `src/statebreaker`。

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

也可以接入可选的组合运行：

```powershell
statebreaker pipeline run workflow.yaml invariants.yaml `
  --generator my.generator `
  --executor my.executor `
  --verifier my.verifier `
  --attack-type my-attack-type
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
