# StateBreaker 现场演示指南

## 最推荐：交互式实验台

在仓库根目录运行：

```powershell
statebreaker interactive
```

语言可以在启动时选择：

```powershell
statebreaker interactive --zh
statebreaker interactive --en
```

进入实验台后按 `9` 也可以在中文和英文之间即时切换，已有实验状态和阶段产物不会丢失。

界面会先展示通用六阶段骨架，再加载当前的 `coupon-race` 参考场景。它不是一键执行：你可以
按 `1 → 2 → 3 → 4 → 5 → 6 → 7` 逐步操作，每一步都会显示输入模型、插件 ID、真实请求、
状态证据和生成的标准产物。执行真实请求前还会再次要求确认。

课堂展示顺序：

1. 选 `1`，说明 Workflow 和 Invariant 都来自外部文件；
2. 选 `2`，展示正常顺序执行只能优惠 50；
3. 选 `3`，展示 Generator 输出多份 `AttackPlan`；
4. 选 `4`，选择 `concurrent-replay`，证明攻击策略也是数据；
5. 选 `5` 并确认，展示真实并发时间线以及 0 → 100 的状态变化；
6. 选 `6`，展示 Verifier 根据“最大增量 50”给出 `confirmed`；
7. 选 `7`，生成标准 `RunBundle` 和报告；
8. 随时选 `8`，查看哪些阶段产物已经准备完成。

需要接入其他场景时运行 `statebreaker interactive --preset custom`，交互输入 Workflow、
Invariant、目标地址及各阶段 plugin ID。当前仓库真正跑通的算法仍暂时针对奶茶券竞态，
`custom` 入口用于证明核心没有写死该场景，并为组员后续插件保留相同接入方式。

这份指南对应当前分阶段 CLI。演示目标不是运行一个预设的一键脚本，而是展示一套可以
迁移到其他系统的竞态条件测试方法：记录正常流程、定义状态规则、生成攻击计划、明确
选择计划、真实并发执行、独立验证状态、生成报告。

## 1. 演示前准备

### 1.1 安装核心和插件

在仓库根目录执行：

```powershell
python -m pip install -e ".[dev]"
python -m pip install -e .\plugins\statebreaker-har-capture
python -m pip install -e .\statebreaker-learner-delta
python -m pip install -e .\race-generator
python -m pip install -e .\race-executor
python -m pip install -e .\statebreaker-verifier-basic
python -m pip install -e .\statebreaker-reporter-pdf

statebreaker doctor
statebreaker plugins list
```

应看到以下正式插件：

```text
har.capture
team.delta-learner
team.race-generator
team.race-executor
team.basic-verifier
team.pdf-reporter
```

### 1.2 启动老王奶茶铺

推荐使用 18080，避免本机 8080 被其他服务占用：

```powershell
$env:STATEBREAKER_LAB_PORT = "18080"
docker compose up --build -d
Invoke-RestMethod http://127.0.0.1:18080/healthz
```

浏览器打开 <http://127.0.0.1:18080/>。页面默认英文，可切换中文。

### 1.3 准备本轮产物目录

```powershell
$D = ".statebreaker/manual-demo"
New-Item -ItemType Directory -Force -Path $D | Out-Null
```

## 2. 先用网页解释漏洞（约 1 分钟）

1. 点击 `Open a Fresh Table`。
2. 点击 `Redeem Once, Politely`，优惠从 0 变为 50。
3. 再兑换一次会被拒绝，说明顺序请求看起来安全。
4. 重新开桌，点击 `Deploy Both Hands`。
5. 两个请求都在券被标记已使用前通过检查，最终优惠变为 100。
6. 页面事件区显示两次 `coupon.checked` 发生在第一次 `coupon.committed` 前。

讲解重点：服务端故意执行“检查未使用 → 等待 150 ms → 加 50 → 标记已使用”。漏洞
证据是最终业务状态变成 100，不是简单地收到两个 HTTP 200。

## 3. 用 CLI 展示真实测试流程（约 5 分钟）

### 步骤 1：查看正常业务流程

```powershell
statebreaker workflow show .\examples\coupon-race\workflow.yaml `
  --target http://127.0.0.1:18080
```

终端会逐步显示：

```text
POST /api/runs                         创建独立实验
GET  /api/runs/${run_id}/state         攻击前状态
POST /api/runs/${run_id}/redeem        正常兑换动作
GET  /api/runs/${run_id}/state         攻击后状态
```

其中 `run_id` 来自第一步响应的 JSONPath extractor，不是硬编码。

### 步骤 2：顺序重放正常流程

```powershell
statebreaker workflow replay .\examples\coupon-race\workflow.yaml `
  --target http://127.0.0.1:18080 `
  --output-root "$D/baseline"
```

预期状态：

```text
state-before: discount_yuan=0, successful_redemptions=0
state-after:  discount_yuan=50, successful_redemptions=1
```

这一步证明正常单次操作的真实影响是加 50。

### 步骤 3：查看待验证业务规则

```powershell
statebreaker invariants show .\examples\coupon-race\invariants.yaml
```

规则为：`$.discount_yuan` 单次最大增量 `max_delta=50`。

### 步骤 4：生成候选攻击计划

```powershell
statebreaker generate `
  .\examples\coupon-race\workflow.yaml `
  .\examples\coupon-race\invariants.yaml `
  --plugin team.race-generator `
  --output "$D/plans.json"

statebreaker plans list "$D/plans.json"
```

当前 generator 会生成 concurrent replay、burst replay、offset sweep、幂等键复用等约
10 个候选计划。此时只生成数据模型，还没有发送攻击请求。

### 步骤 5：明确选择竞态计划

```powershell
statebreaker plans select "$D/plans.json" `
  --attack-type concurrent-replay `
  --output "$D/selected-plan.json"
```

预期计划参数：

```text
target_steps: redeem-coupon
concurrency: 2
offsets_ms: [0.0, 0.0]
strategy: simultaneous
```

计划选择与执行分离，测试人员可以在真正发请求前检查并发数、目标步骤和关联规则。

### 步骤 6：真实执行两个并发请求

```powershell
statebreaker attack "$D/selected-plan.json" `
  --workflow .\examples\coupon-race\workflow.yaml `
  --target http://127.0.0.1:18080 `
  --plugin team.race-executor `
  --output "$D/result.json"
```

终端会打印真实时间线，形式如下：

```text
+   0.000 ms SEND #1 POST /api/runs/<run_id>/redeem
+   1.xxx ms SEND #2 POST /api/runs/<run_id>/redeem
+ 155.xxx ms DONE #1 HTTP 200
+ 157.xxx ms DONE #2 HTTP 200
server evidence: checks=2, commits=2, rejections=0
discount_yuan: 0 -> 100 (delta=+100)
successful_redemptions: 0 -> 2 (delta=+2)
```

具体毫秒数每次会变化，但两个请求应落入同一个 150 ms 竞态窗口。

### 步骤 7：让 verifier 独立判定

```powershell
statebreaker verify "$D/result.json" `
  .\examples\coupon-race\invariants.yaml `
  --plugin team.basic-verifier `
  --output "$D/findings.json"
```

预期输出：

```text
CONFIRMED
observed_delta=100.0
allowed_max=50
```

executor 的启发式标记不是最终结论；正式 `Finding` 由 verifier 根据状态证据产生。

### 步骤 8：组装证据并生成 PDF

```powershell
statebreaker bundle build `
  --workflow .\examples\coupon-race\workflow.yaml `
  --target http://127.0.0.1:18080 `
  --plan "$D/selected-plan.json" `
  --result "$D/result.json" `
  --findings "$D/findings.json" `
  --output "$D/run-bundle.json"

statebreaker report "$D/run-bundle.json" `
  --plugin team.pdf-reporter `
  --output-dir "$D/report"
```

最终打开：

```text
.statebreaker/manual-demo/report/statebreaker-report.pdf
```

## 4. 如何展示 Capture 插件

浏览器开发者工具导出 HAR 后执行：

```powershell
statebreaker workflow import .\recording.har `
  --plugin har.capture `
  --options .\capture-options.yaml `
  --output .\captured-workflow.json

statebreaker workflow show .\captured-workflow.json
```

示例配置：

```yaml
state_probe_entry_indices: [1, 3]
strip_credentials: false
```

当前 importer 支持同源 HTTP/HTTPS、GET/POST/PUT/PATCH/DELETE、JSON/Form Body、Cookie
和 Authorization。它尚不能自动识别响应生成的动态 ID、自动创建 extractor 或处理多
origin 流程；这部分是 Capture 模块下一阶段工作。

## 5. 换到其他系统时改什么

CLI 流程不变，只替换：

- Workflow：真实目标的请求、会话、动态变量和状态查询步骤；
- Invariant：该业务真正应满足的状态规则；
- `--target`：新的测试地址；
- generator/executor：如果新场景需要不同攻击策略，则选择其他插件 ID。

核心仍按相同顺序执行 `show/replay → generate → select → attack → verify → report`。

## 6. 推荐汇报话术

> StateBreaker 不是针对奶茶券写死的一键 PoC。奶茶铺只是当前可观察的测试目标。工具先
> 重放正常流程，证明一次操作只增加 50；再由 generator 提出多个攻击计划，测试人员
> 明确选择两请求竞态；executor 真实发送请求并记录毫秒时间线；最后 verifier 根据
> 0 到 100 的业务状态变化确认规则被破坏。换系统时替换 Workflow、Invariant 和插件，
> 整个测试步骤保持不变。

## 7. 结束实验

```powershell
docker compose down
```
