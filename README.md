# StateBreaker v0.1

## 新 CLI 快速开始

新版 CLI 展示的是一套通用、可组合的业务状态测试过程，而不是写死场景的一键 PoC。
本次课程演示把“老王奶茶券竞态”作为装入这套骨架的第一套场景配置和参考插件；终端中
出现的优惠券字段来自示例 Workflow 和插件输出，不是核心 CLI 的固定字段。

课堂上推荐直接启动逐阶段交互实验台：

```powershell
statebreaker interactive
```

它会显示通用阶段菜单，但当前默认加载奶茶券竞态参考场景；不会自动替你执行完整攻击。
使用 `statebreaker interactive --en` 或 `--zh` 选择界面语言，运行中按 `9` 可即时切换。

```powershell
python -m pip install -e .\race-generator -e .\race-executor `
  -e .\statebreaker-verifier-basic -e .\statebreaker-reporter-pdf `
  -e .\plugins\statebreaker-har-capture
$env:STATEBREAKER_LAB_PORT = "18080"
docker compose up --build -d
```

课堂演示依次运行：

```powershell
statebreaker workflow show examples/coupon-race/workflow.yaml --target http://127.0.0.1:18080
statebreaker workflow replay examples/coupon-race/workflow.yaml `
  --target http://127.0.0.1:18080
statebreaker invariants show examples/coupon-race/invariants.yaml
statebreaker generate examples/coupon-race/workflow.yaml `
  examples/coupon-race/invariants.yaml --plugin team.race-generator `
  -o .statebreaker/manual/plans.json
statebreaker plans list .statebreaker/manual/plans.json
statebreaker plans select .statebreaker/manual/plans.json `
  --attack-type concurrent-replay -o .statebreaker/manual/selected-plan.json
statebreaker attack .statebreaker/manual/selected-plan.json `
  --workflow examples/coupon-race/workflow.yaml --target http://127.0.0.1:18080 `
  --plugin team.race-executor -o .statebreaker/manual/result.json
statebreaker verify .statebreaker/manual/result.json `
  examples/coupon-race/invariants.yaml --plugin team.basic-verifier `
  -o .statebreaker/manual/findings.json
statebreaker bundle build --workflow examples/coupon-race/workflow.yaml `
  --target http://127.0.0.1:18080 `
  --plan .statebreaker/manual/selected-plan.json `
  --result .statebreaker/manual/result.json `
  --findings .statebreaker/manual/findings.json `
  -o .statebreaker/manual/run-bundle.json
statebreaker report .statebreaker/manual/run-bundle.json `
  --plugin team.pdf-reporter --output-dir .statebreaker/manual/report
```

每一步都会显示真实 HTTP 请求、并发时间差、响应状态、攻击前后状态和规则违反依据。
现场汇报直接参考 [演示指南](docs/DEMO_GUIDE_ZH.md)；完整参数见
[新版 CLI 文档](docs/cli.md)。

StateBreaker 是一个面向业务逻辑漏洞实验的可扩展框架。它把“流量采集、正常状态学习、攻击计划生成、攻击执行、结果验证、报告生成”拆成六类独立插件，让不同组员可以并行开发，而不用互相复制数据结构、HTTP 会话代码和命令行入口。

当前仓库是 **v0.1 接口骨架 + 老王奶茶券竞态靶场**，不是已经完成的自动漏洞扫描器。核心已经提供：

- 带版本号的统一数据模型和 JSON Schema；
- 基于 Python Entry Points 的插件发现和兼容性检查；
- 独立命名会话、Cookie 隔离、变量替换和动态参数提取；
- JSONL 事件日志、敏感字段脱敏和统一运行目录；
- 稳定的 CLI 路由；
- 一个可重置、状态可观察、可稳定复现竞态的 Docker 靶场；
- 一个不会真正发起攻击的 `plugin-template` 示例插件。

仓库已经包含第一版 HAR importer，以及暂时面向奶茶券竞态闭环的计划生成器、并发执行器、
验证器和 PDF reporter。它们是独立的参考插件，不是核心内置算法；组员可以替换或扩展它们，
而不修改核心数据契约。

> 仅可测试自己拥有或已经获得明确授权的系统。仓库内的攻击执行示例只用于本地靶场和授权实验环境。

---

## 1. 先理解整体结构

StateBreaker 的数据流如下：

```text
浏览器/HAR/手写请求
        │
        ▼
 capture 插件 ──► Workflow
                       │
                       ▼
 learner 插件 ──► StateProfile + Invariant[]
                                      │
                                      ▼
 generator 插件 ────────────────► AttackPlan[]
                                      │
                                      ▼
 executor 插件 ────────────────► RawAttackResult
                                      │
                                      ▼
 verifier 插件 ─────────────────► Finding[]
                                      │
                                      ▼
 reporter 插件 ────────────────► HTML/JSON/图表/PoC
```

六个阶段通过模型文件交换结果。某个组员只要遵守自己的输入输出契约，就不需要了解其他插件的内部实现。

例如：

- 抓包组员负责把 HAR 转成 `Workflow`；
- 规则组员只读取 `Workflow`，输出 `LearningResult`；
- 攻击生成组员读取 `Workflow + Invariant[]`，输出 `AttackPlan[]`；
- 调度组员读取单个 `AttackPlan`，执行请求并输出 `RawAttackResult`；
- 验证组员只依据结果和规则输出 `Finding[]`；
- 报告组员把一次运行的全部产物渲染成报告。

### 仓库目录

```text
StateBreaker/
├─ src/statebreaker/          # 核心模型、运行时、插件发现、pipeline 和 CLI
├─ plugins/statebreaker-har-capture/ # HAR 1.2 capture 插件
├─ plugin-template/           # 可复制的独立插件模板
├─ race-generator/            # 优惠券竞态攻击计划生成插件
├─ race-executor/             # 优惠券竞态攻击检测执行插件
├─ statebreaker-learner-delta/ # 多轮正常状态差分 learner 插件
├─ statebreaker-verifier-basic/ # 最小状态证据 verifier 插件
├─ statebreaker-reporter-pdf/  # 最小 PDF reporter 插件
├─ labs/coupon-race/          # “老王奶茶券”Docker 竞态靶场
├─ examples/coupon-race/      # 示例 Workflow、Invariant、AttackPlan
├─ docs/                      # 架构、契约和插件说明
├─ tests/                     # 核心单元测试和靶场测试
├─ .github/workflows/ci.yml   # Python 与 Docker 持续集成
├─ docker-compose.yml         # 靶场启动入口
└─ pyproject.toml             # 核心包配置
```

核心公共 API 位于：

- `src/statebreaker/models.py`：所有跨插件传递的数据；
- `src/statebreaker/runtime.py`：HTTP 会话、变量、提取器和事件日志；
- `src/statebreaker/plugins.py`：插件发现、加载和兼容性检查；
- `src/statebreaker/pipeline.py`：CI/批量模式的完整编排；
- `src/statebreaker/cli.py`：稳定命令行入口。

修改公共模型会影响所有组员。除非大家已经讨论并确认新契约，否则优先在插件内部扩展自己的配置，不要随意修改核心模型。

---

## 2. 环境准备

### 必需软件

- Python 3.11 或 3.12；
- Git；
- Docker Desktop（只在运行靶场时需要）。

### Windows PowerShell

```powershell
cd "C:\Users\你的用户名\Desktop\StateBreaker"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
statebreaker doctor
```

如果 PowerShell 禁止激活脚本，可以不激活虚拟环境，直接使用：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\statebreaker.exe doctor
```

### Linux / macOS

```bash
cd StateBreaker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
statebreaker doctor
```

`statebreaker doctor` 应显示 Python 版本、核心 API 版本、运行目录和可用插件组。

### 先跑一次核心测试

```bash
pytest -q
ruff check .
mypy src/statebreaker
```

新组员应先确认原仓库测试通过，再开始自己的修改。这样后面出现失败时，能区分是环境问题还是新代码引入的问题。

---

## 3. 启动“老王奶茶券”靶场

靶场模拟一张只能使用一次的 `BUG50` 奶茶券。服务端故意执行：

```text
检查优惠券未使用
       ↓
等待 150 ms
       ↓
优惠金额增加 50 元
       ↓
标记优惠券已使用
```

两个请求在等待期间交错执行时，都可能通过“未使用”检查，最后把优惠金额增加到 100 元。

### 默认端口 8080

```bash
docker compose up --build
```

浏览器打开：<http://127.0.0.1:8080/>

### 8080 被占用时

Windows PowerShell：

```powershell
$env:STATEBREAKER_LAB_PORT = "18080"
docker compose up --build
```

Linux / macOS：

```bash
STATEBREAKER_LAB_PORT=18080 docker compose up --build
```

然后打开：<http://127.0.0.1:18080/>

### 页面操作

1. 点击“开一张新桌”；
2. 点击“老实兑换一次”，最终优惠应为 50 元；
3. 再兑换一次，应被拒绝，优惠仍为 50 元；
4. 重新开桌；
5. 点击“发动双倍手速”，页面会同时发送两个请求；
6. 两个请求都通过检查，最终优惠应为 100 元；
7. 查看页面下方时间线，确认两个 `checked_unused` 事件都发生在首次 `committed` 之前。

### 靶场 API

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/runs` | 创建一轮全新的独立实验 |
| `GET` | `/api/runs/{run_id}/state` | 查询优惠、券状态和成功次数 |
| `POST` | `/api/runs/{run_id}/redeem` | 兑换固定优惠券 `BUG50` |
| `GET` | `/api/runs/{run_id}/events` | 查询检查与写入事件时间线 |
| `GET` | `/healthz` | Docker 健康检查 |

结束实验：

```bash
docker compose down
```

不要把本靶场的“修复”当成开发任务。它就是用于验证检测器是否能发现真实状态异常的漏洞版基准。

---

## 4. CLI 快速体验

### 校验示例工作流

```bash
statebreaker workflow validate examples/coupon-race/workflow.yaml
```

### 导出全部 JSON Schema

```bash
statebreaker schema export schemas
```

### 安装并发现模板插件

```bash
python -m pip install -e ./plugin-template
statebreaker plugins list
```

输出中应出现：

```text
statebreaker.executor  template.dry-run  0.1.0  preview-only,no-network-requests
```

### 调用模板插件

```bash
statebreaker attack examples/coupon-race/attack-plan.yaml \
  --plugin template.dry-run \
  --workflow examples/coupon-race/workflow.yaml
```

Windows PowerShell 可以写成一行：

```powershell
statebreaker attack examples/coupon-race/attack-plan.yaml --plugin template.dry-run --workflow examples/coupon-race/workflow.yaml
```

模板插件只返回 dry-run 结果，不发送 HTTP 请求。它的目的只是证明：组员可以在不修改核心代码的情况下，安装自己的包并被 CLI 自动发现。

---

## 5. 必须遵守的数据契约

所有公共模型都含有：

```yaml
schema_version: "0.1"
```

核心会拒绝未知版本、非法步骤依赖、未定义变量和不兼容插件。不要用普通字典绕过 Pydantic 校验；插件返回值必须是核心模型实例。

### 5.1 Workflow

`Workflow` 描述一次正常业务流程，包括：

- 目标地址 `base_url`；
- 命名会话 `sessions`；
- 初始变量 `variables`；
- 有顺序和依赖的请求步骤 `steps`；
- 用于读取真实业务状态的 `state_probe_steps`。

示例步骤：

```yaml
- schema_version: "0.1"
  id: redeem-coupon
  role: action
  session: alice
  request:
    schema_version: "0.1"
    method: POST
    path: /api/runs/${run_id}/redeem
    json_body:
      coupon_code: ${coupon_code}
  depends_on:
    - state-before
```

`${run_id}` 由前面的提取器写入运行时变量：

```yaml
extract:
  - schema_version: "0.1"
    name: run_id
    kind: jsonpath
    expression: $.run_id
```

### 5.2 Extractor

支持三类动态参数提取：

| `kind` | `expression` 示例 | 说明 |
|---|---|---|
| `jsonpath` | `$.data.order_id` | 从 JSON 响应提取 |
| `header` | `X-CSRF-Token` | 从响应 Header 提取，大小写不敏感 |
| `regex` | `token=([A-Za-z0-9_-]+)` | 从响应正文提取，第一个捕获组优先 |

提取出的值会写入统一变量表，后续步骤可通过 `${variable}` 使用。

如果同一个变量被并发请求同时写入，插件必须明确自己的合并策略，不能依赖“最后一次写入碰巧正确”。

### 5.3 Invariant

`Invariant` 是候选业务规则。骨架只负责格式，不负责判断规则是否正确。

```yaml
schema_version: "0.1"
id: coupon-max-delta
kind: max-delta
selector: $.discount_yuan
before_probe: state-before
after_probe: state-after
parameters:
  max_delta: 50
description: 一张 BUG50 优惠券最多增加 50 元优惠
```

支持的规则类型包括数值最大变化、最小值、单次使用、状态转换、唯一关系、次数限制和所有权关系。

### 5.4 AttackPlan

`AttackPlan` 只描述攻击意图，不直接执行网络请求：

```yaml
schema_version: "0.1"
id: double-hand-coupon
workflow_name: coupon-race-demo
attack_type: concurrent-replay
target_steps:
  - redeem-coupon
session_bindings:
  redeem-coupon: alice
schedule:
  schema_version: "0.1"
  concurrency: 2
  offsets_ms:
    - 0
    - 0
  options: {}
invariant_ids:
  - coupon-max-delta
```

生成器必须引用真实存在的步骤 ID 和规则 ID。执行器不得悄悄改变计划含义；如果因能力限制降级，应在结果元数据和日志中写清楚。

### 5.5 RawAttackResult

执行器的结果至少包含：

- `plan_id`；
- 请求响应集合；
- 攻击前状态 `before_state`；
- 攻击后状态 `after_state`；
- 统一事件时间线；
- 执行器自己的元数据。

业务逻辑漏洞不能只凭“两个请求都返回 200”确认。正确证据链应是：

```text
攻击前 discount = 0
两次 redeem 的检查阶段重叠
攻击后 discount = 100
规则规定单次最多增加 50
因此状态增量违反规则
```

### 5.6 Finding

验证器输出三种结论：

- `confirmed`：独立状态查询已证明业务状态被破坏；
- `probable`：响应或时间线高度可疑，但状态证据不完整；
- `rejected`：请求看似成功，但没有违反业务规则的副作用。

每条 Finding 必须引用证据 ID，方便报告器回溯原始请求、状态快照和事件。

详细字段见 [docs/contracts.md](docs/contracts.md) 和导出的 JSON Schema。

---

## 6. 六类插件的固定接口

插件采用 Python 标准 `importlib.metadata.entry_points()` 发现。组员安装自己的包以后，核心会扫描以下入口组：

| Entry Point 组 | 必须实现的方法 | 返回类型 |
|---|---|---|
| `statebreaker.capture` | `capture(source, options)` | `Workflow` |
| `statebreaker.learner` | `learn(workflow, runtime)` | `LearningResult` |
| `statebreaker.generator` | `generate(workflow, invariants)` | `list[AttackPlan]` |
| `statebreaker.executor` | `execute(plan, runtime)` | `RawAttackResult` |
| `statebreaker.verifier` | `verify(result, invariants)` | `list[Finding]` |
| `statebreaker.reporter` | `render(run_bundle, output_dir)` | `ReportArtifacts` |

所有插件对象还必须提供：

```python
manifest = PluginManifest(
    plugin_id="唯一且稳定的插件 ID",
    name="人类可读名称",
    version="0.1.0",
    api_version="0.1",
    group="statebreaker.executor",
    capabilities=["能力标签"],
)
```

核心会在执行前检查：

- `plugin_id` 是否重复；
- Entry Point 组是否与 `manifest.group` 相符；
- `api_version` 是否兼容；
- 必需方法是否存在；
- 调用异常是否需要统一包装并显示。

---

## 7. 从模板创建自己的插件

建议每位组员维护一个独立 Python 包。开发初期可以放在 StateBreaker 目录旁边，也可以放到自己的 Git 分支中。不要把所有算法继续堆进 `src/statebreaker`。

### 第一步：复制模板

Windows PowerShell 示例：

```powershell
Copy-Item -Recurse .\plugin-template ..\statebreaker-my-plugin
cd ..\statebreaker-my-plugin
```

Linux / macOS：

```bash
cp -R plugin-template ../statebreaker-my-plugin
cd ../statebreaker-my-plugin
```

### 第二步：至少修改这些位置

1. `pyproject.toml` 中的项目名称；
2. Python 包目录名；
3. 插件类名；
4. `manifest.plugin_id`、名称、版本和能力；
5. Entry Point 组、Entry Point 名和导入路径；
6. README 中的安装、配置和测试说明。

假设开发一个普通异步并发执行器：

```toml
[project]
name = "statebreaker-async-executor"
version = "0.1.0"
dependencies = [
  "statebreaker>=0.1,<0.2",
]

[project.entry-points."statebreaker.executor"]
async-executor = "statebreaker_async_executor.plugin:AsyncExecutorPlugin"
```

入口名建议与 `plugin_id` 保持一致。

### 第三步：声明 Manifest

```python
from statebreaker.models import PluginManifest


class AsyncExecutorPlugin:
    manifest = PluginManifest(
        plugin_id="async-executor",
        name="普通 asyncio 并发执行器",
        version="0.1.0",
        api_version="0.1",
        group="statebreaker.executor",
        capabilities=["asyncio-gather", "bounded-concurrency"],
    )
```

### 第四步：实现该组的唯一方法

```python
from statebreaker.models import AttackPlan, RawAttackResult
from statebreaker.runtime import ExecutionRuntime


class AsyncExecutorPlugin:
    # manifest 省略

    async def execute(
        self,
        plan: AttackPlan,
        runtime: ExecutionRuntime,
    ) -> RawAttackResult:
        ...
```

插件方法都是异步接口。即使当前实现不需要异步，也应使用 `async def`，避免以后接入网络或文件 I/O 时破坏契约。

### 第五步：可编辑安装并检查发现结果

在 StateBreaker 的虚拟环境中执行：

```bash
python -m pip install -e ../statebreaker-my-plugin
statebreaker plugins list
```

如果列表没有出现插件，先检查：

- 插件是否安装在运行 `statebreaker` 的同一个 Python 环境；
- `pyproject.toml` 的 Entry Point 组名是否拼对；
- 导入路径和类名是否真实存在；
- 包目录是否被构建系统收录。

### 第六步：为插件建立自己的测试

推荐目录：

```text
statebreaker-my-plugin/
├─ src/statebreaker_my_plugin/
│  ├─ __init__.py
│  └─ plugin.py
├─ tests/
│  ├─ test_manifest.py
│  ├─ test_plugin.py
│  └─ fixtures/
├─ pyproject.toml
└─ README.md
```

至少测试：

- Manifest 可被 Pydantic 校验；
- 正常输入返回正确核心模型；
- 坏配置给出明确错误；
- 不修改输入对象；
- 相同输入在不涉及网络随机性时可重复得到相同输出；
- 插件可被真实安装和 `statebreaker plugins list` 发现。

---

## 8. 各插件组员应该怎么开发

### 8.1 Capture：流量采集与标准化

职责：把 HAR、手写 YAML、代理记录或浏览器扩展输出转成 `Workflow`。

必须完成：

- 保留请求顺序、方法、路径、Header、JSON/Form Body；
- 区分 Alice、Bob 等命名会话；
- 识别 Cookie、Authorization 和 CSRF Token；
- 把购物车 ID、订单 ID、Token 等动态值改成 `${variable}`；
- 在产生动态值的上游响应中添加 `Extractor`；
- 补充 `depends_on`，保证重放顺序合法；
- 允许用户选择哪些请求是 `state_probe_steps`；
- 输出前用 `Workflow.model_validate(...)` 做最终校验。

不要做：

- 把真实密码、Cookie 或 Token 写死进 Workflow；
- 自动认定某个响应字段就是漏洞；
- 在 Capture 阶段实现并发攻击；
- 把浏览器缓存、图片、字体等无关资源全部塞进业务流程。

推荐先实现最小 HAR 导入：只支持 Fetch/XHR、JSON/Form、Cookie 和简单依赖；复杂推断逐步增加。导入后必须用运行时顺序重放一次，确认流程真实可执行。

接口骨架：

```python
from pathlib import Path
from typing import Any

from statebreaker.models import Workflow


class HarCapturePlugin:
    # manifest.group = "statebreaker.capture"

    async def capture(
        self,
        source: Path,
        options: dict[str, Any],
    ) -> Workflow:
        har = self._read_har(source)
        candidate = self._convert(har, options)
        return Workflow.model_validate(candidate)
```

完成标准：导入一轮“创建实验 → 查询状态 → 兑换 → 再查询状态”的流量，生成的 Workflow 能在重置后的靶场上完整重放。

### 8.2 Learner：正常状态分析与候选规则

职责：重复正常流程，对前后状态做差分，输出 `StateProfile + Invariant[]`。

必须完成：

- 多次执行正常基线，不从单次样本草率推断；
- 比较结构化 JSON 状态；
- 标记时间戳、UUID、随机 Token 等易变字段；
- 识别稳定数值变化、状态转换、次数和所有权关系；
- 给每条候选规则附置信度和样本证据；
- 允许测试人员确认、修改或删除规则；
- 只输出候选规则，不把候选规则当作已证实事实。

不要做：

- 只比较 HTTP 状态码；
- 把任意随机变化都当成业务规则；
- 在学习阶段发送攻击请求；
- 把某个靶场字段名硬编码进通用算法。

接口骨架：

```python
from statebreaker.models import LearningResult, Workflow
from statebreaker.runtime import ExecutionRuntime


class DeltaLearnerPlugin:
    # manifest.group = "statebreaker.learner"

    async def learn(
        self,
        workflow: Workflow,
        runtime: ExecutionRuntime,
    ) -> LearningResult:
        samples = await self._collect_normal_samples(workflow, runtime)
        profile = self._build_state_profile(samples)
        invariants = self._propose_invariants(profile)
        return LearningResult(profile=profile, invariants=invariants)
```

完成标准：对优惠券正常兑换多次采样后，至少提出“`discount` 单次增加不超过 50”或“券从 unused 转为 used”的有意义候选规则，并能解释它来自哪些样本。

### 8.3 Generator：攻击流程生成

职责：根据正常 Workflow 和已确认 Invariant 生成攻击计划，不执行网络请求。

必须完成：

- 支持至少一种攻击变异：重复、并发、跳步、乱序或跨用户；
- 生成计划时引用存在的步骤和规则；
- 清楚记录并发数、重复次数、时间偏移和会话绑定；
- 输出确定性结果，便于测试和复现；
- 对不适用的规则明确跳过并给出原因。

不要做：

- 在 Generator 内直接访问目标系统；
- 随意替换 Token 却不在计划中记录；
- 生成无法满足依赖关系的步骤 ID；
- 无上限地扩大并发数。

接口骨架：

```python
from statebreaker.models import AttackPlan, Invariant, Workflow


class MutationGeneratorPlugin:
    # manifest.group = "statebreaker.generator"

    async def generate(
        self,
        workflow: Workflow,
        invariants: list[Invariant],
    ) -> list[AttackPlan]:
        plans: list[AttackPlan] = []
        plans.extend(self._generate_replays(workflow, invariants))
        plans.extend(self._generate_concurrency(workflow, invariants))
        return plans
```

完成标准：对示例优惠券 Workflow 生成一个目标为 `redeem-coupon`、并发数为 2、关联 `coupon-max-delta` 的合法 AttackPlan。

### 8.4 Executor：攻击执行与调度

职责：实际发送 AttackPlan 指定的请求，保留会话隔离和完整时间线，输出原始结果。

共享运行时已经提供：

- `runtime.execute_step(step, request_ordinal=...)`；
- 每个命名会话独立的 `httpx.AsyncClient` 与 Cookie Jar；
- `${variable}` 替换；
- JSONPath/Header/正则提取；
- 超时；
- 请求和响应事件；
- 默认敏感字段脱敏。

普通并发执行器可以在本地靶场中这样调用共享运行时：

```python
import asyncio
import json
from datetime import UTC, datetime

from statebreaker.models import AttackPlan, RawAttackResult
from statebreaker.runtime import ExecutionRuntime


class AsyncExecutorPlugin:
    # manifest.group = "statebreaker.executor"

    async def execute(
        self,
        plan: AttackPlan,
        runtime: ExecutionRuntime,
    ) -> RawAttackResult:
        step_map = {step.id: step for step in runtime.workflow.steps}
        target_id = plan.target_steps[0]
        target_index = next(
            index
            for index, step in enumerate(runtime.workflow.steps)
            if step.id == target_id
        )
        target = step_map[target_id]
        bound_session = plan.session_bindings.get(target.id, target.session)
        target = target.model_copy(update={"session": bound_session})

        concurrency = plan.schedule.concurrency
        if not 1 <= concurrency <= 32:
            raise ValueError("concurrency must be between 1 and 32")

        # 先顺序执行创建实验和攻击前状态查询。
        before_state: dict[str, object] = {}
        for step in runtime.workflow.steps[:target_index]:
            record = await runtime.execute_step(step)
            if step.id in runtime.workflow.state_probe_steps:
                before_state = json.loads(record.body_preview)

        started_at = datetime.now(UTC)
        responses = await asyncio.gather(
            *(
                runtime.execute_step(target, request_ordinal=index)
                for index in range(concurrency)
            )
        )
        finished_at = datetime.now(UTC)

        # 攻击完成后独立查询业务状态。
        after_state: dict[str, object] = {}
        for step in runtime.workflow.steps[target_index + 1 :]:
            record = await runtime.execute_step(step)
            if step.id in runtime.workflow.state_probe_steps:
                after_state = json.loads(record.body_preview)

        return RawAttackResult(
            run_id=runtime.run_id,
            attack_plan_id=plan.id,
            started_at=started_at,
            finished_at=finished_at,
            responses=list(responses),
            before_state=before_state,
            after_state=after_state,
            events=list(runtime.events),
            plugin_data={"scheduler": "asyncio", "concurrency": concurrency},
        )
```

这是针对当前线性优惠券示例的最小执行器。正式通用实现还必须：

1. 按依赖图而不只是列表位置安排准备和查询步骤；
2. 支持 `offsets_ms` 和插件声明的调度能力；
3. 将发送时间、完成时间、请求序号和关联 ID 写入结果；
4. 正确关闭运行时；
5. 对超时和部分失败保留已获得的证据，不吞异常；
6. 对并发数和重试次数设置硬上限；
7. 避免多个并发响应提取同名变量时发生不确定覆盖。

如果开发 Last-Byte Gate、时间偏移搜索或自适应并发搜索，可以在插件内部增加自己的配置模型，但最终仍必须返回 `RawAttackResult`。原始 socket 实现不得破坏 Alice/Bob 会话隔离，也不得跳过脱敏日志。

完成标准：在重置后的奶茶券靶场连续运行 10 轮，顺序两次兑换最终为 50，并发两次最终为 100；时间线证明两个请求都在首次写入前完成“未使用”检查。

### 8.5 Verifier：漏洞确认

职责：把原始响应、前后状态、时间线和 Invariant 组合成可审计结论。

推荐判断顺序：

1. 选择规则要求的状态字段；
2. 计算攻击前后真实状态变化；
3. 检查是否超过规则边界；
4. 关联请求响应和时间线事件；
5. 根据证据完整度输出 `confirmed / probable / rejected`；
6. 把所有依据写进 `evidence_refs` 和结构化详情。

接口骨架：

```python
from statebreaker.models import Finding, Invariant, RawAttackResult


class StateVerifierPlugin:
    # manifest.group = "statebreaker.verifier"

    async def verify(
        self,
        result: RawAttackResult,
        invariants: list[Invariant],
    ) -> list[Finding]:
        findings: list[Finding] = []
        for invariant in invariants:
            finding = self._evaluate(result, invariant)
            findings.append(finding)
        return findings
```

不要把下面情况误报为 Confirmed：

- 两个响应都是 200，但最终优惠只有 50；
- 响应正文说“成功”，独立状态查询却没有变化；
- 状态变化存在，但没有违反对应 Invariant；
- 状态查询本身失败或读到了另一个 `run_id`。

完成标准：同一验证器对顺序兑换输出 `rejected`，对优惠从 0 变 100 的并发结果输出 `confirmed`，缺少 after_state 时最多输出 `probable`。

### 8.6 Reporter：报告与可复现产物

职责：把 `RunBundle` 渲染成人类可读且可复核的产物。

报告至少包含：

- 漏洞标题和结论；
- 正常 Workflow；
- AttackPlan；
- 对应 Invariant；
- 攻击前后状态；
- 请求与服务端事件时间线；
- 最低有效并发数和成功率（有数据时）；
- 失败轮次，避免只展示成功样本；
- 可复现命令或 PoC；
- 运行环境、插件版本和核心 API 版本。

接口骨架：

```python
from pathlib import Path

from statebreaker.models import ReportArtifacts, RunBundle


class HtmlReporterPlugin:
    # manifest.group = "statebreaker.reporter"

    async def render(
        self,
        run_bundle: RunBundle,
        output_dir: Path,
    ) -> ReportArtifacts:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "report.html"
        self._render_html(run_bundle, report_path)
        return ReportArtifacts(
            files=[str(report_path)],
            metadata={"format": "html"},
        )
```

Reporter 不应重新访问目标系统，也不应重新判断漏洞；它负责忠实展示 Verifier 已经给出的结论。生成 HTML 时要转义不可信响应正文，避免报告本身出现脚本注入。

完成标准：仅凭保存的 RunBundle 离线生成报告，重新运行 Reporter 不需要靶场在线。

---

## 9. 会话、变量和状态证据的正确用法

### 命名会话隔离

Workflow 中的每个会话名对应独立客户端：

```yaml
sessions:
  alice: {}
  bob: {}
```

Alice 的 Cookie 不会自动进入 Bob 的 Cookie Jar。跨用户插件如果需要把某个资源 ID 从 Alice 传给 Bob，应在 AttackPlan 中显式记录变量替换，而不是复制整个 Cookie Header。

### 变量生命周期

变量可能来自：

- Workflow 初始变量；
- CLI 或插件提供的运行配置；
- 上游响应 Extractor；
- 插件显式写入的派生值。

执行某步骤前，所有 `${variable}` 必须已经存在。若变量来自步骤 A，则使用它的步骤 B 应声明 `depends_on: [A]`。

### 状态查询必须独立

不要把兑换接口自己的响应当作最终状态。正确流程是：

```text
GET state  ──► before_state
POST redeem ┐
POST redeem ┴─► 并发执行
GET state  ──► after_state
GET events ──► 服务端事件证据
```

### 关联 ID

每轮运行、每个步骤和每个请求序号都应可关联：

- `run_id`：StateBreaker 一次本地运行；
- `plan_id`：攻击计划；
- `step_id`：工作流步骤；
- `request_ordinal`：同一步骤的第几个并发请求；
- `correlation_id`：请求与事件的关联值。

没有关联 ID 的时间线很难证明两个请求属于同一次实验。

---

## 10. 运行产物和日志

核心默认把产物写到：

```text
.statebreaker/runs/<run_id>/
```

典型内容包括：

```text
workflow.json
attack_plan.json
events.jsonl
raw_attack_result.json
findings.json
report/
```

插件可以增加自己的文件，但应遵守：

- 只写本次 `run_id` 对应目录；
- 文件名稳定且可预测；
- 结构化数据优先使用 JSON；
- 原始二进制流量注明格式和版本；
- 不把明文密码、Authorization、Cookie、Token、Secret 写入日志；
- 报告引用产物时使用相对路径，方便整体移动。

默认脱敏只是一道保险。插件自定义日志仍需主动调用核心脱敏逻辑或采用同等规则。

---

## 11. 测试规范

### 核心回归测试

每次提交前：

```bash
pytest -q
ruff check .
mypy src/statebreaker
```

### 插件单元测试

不同插件建议覆盖：

| 插件 | 重点测试 |
|---|---|
| Capture | HAR fixture、动态值替换、会话识别、坏输入 |
| Learner | 稳定字段、易变字段、空样本、候选规则证据 |
| Generator | 五类变异、非法引用、确定性、边界配置 |
| Executor | Cookie 隔离、超时、部分失败、并发上限、事件顺序 |
| Verifier | confirmed/probable/rejected、缺失状态、选择器错误 |
| Reporter | 离线生成、HTML 转义、缺失可选字段、产物列表 |

### 插件发现集成测试

仅仅能 `import` 不够。必须构建或安装插件后检查真实 Entry Point：

```bash
python -m pip install -e .
statebreaker plugins list
```

然后通过对应 CLI 调用一次：

```bash
statebreaker generate <workflow.yaml> <invariants.yaml> --plugin your-generator
```

### 靶场回归测试

```bash
pytest -q tests/lab
```

验收不只看接口返回，还要检查：

- 新实验优惠为 0；
- 顺序兑换两次最终优惠为 50；
- 并发兑换两次最终优惠为 100；
- 连续 10 轮能够稳定复现；
- 时间线中两个检查事件先于首次提交事件。

### 测试不能依赖的东西

- 组员个人浏览器登录状态；
- 固定存在的远程服务器；
- 真实账户密码；
- 上一次运行遗留的 `.statebreaker` 目录；
- 固定端口一定空闲；
- 测试执行顺序。

---

## 12. Git 协作建议

建议每位组员从 `main` 创建自己的功能分支：

```bash
git switch main
git pull
git switch -c feature/capture-har
```

分支名示例：

- `feature/capture-har`
- `feature/learner-state-diff`
- `feature/generator-mutations`
- `feature/executor-asyncio`
- `feature/executor-last-byte`
- `feature/verifier-invariants`
- `feature/reporter-html`

提交要求：

- 一次提交只解决一个清晰问题；
- 插件代码、测试和文档一起提交；
- 不提交 `.venv`、缓存、`.statebreaker` 运行产物或真实凭据；
- 不把个人 IDE 配置强行覆盖给全组；
- 若必须修改核心契约，单独提交并在说明中列出受影响插件；
- 合并前跑完整测试，并附一条可复制的演示命令。

当两位组员都需要相似能力时，优先复用核心运行时或新增经过讨论的公共辅助函数，不要复制两份稍有差异的 Cookie、脱敏或模型代码。

---

## 13. 每类插件的交付标准

一个插件可以交给全组使用前，至少满足：

- [ ] 有独立 `pyproject.toml`；
- [ ] 依赖范围写明 `statebreaker>=0.1,<0.2`；
- [ ] Manifest 字段完整，`plugin_id` 全局唯一；
- [ ] 使用正确 Entry Point 组；
- [ ] 不修改核心注册表即可被发现；
- [ ] 输入和输出都是核心 Pydantic 模型；
- [ ] 有错误输入和边界条件测试；
- [ ] 有一条真实 CLI 集成测试；
- [ ] 日志不泄露密码、Cookie 或 Token；
- [ ] 网络插件有超时、并发上限和资源关闭；
- [ ] README 写明配置、命令、能力和限制；
- [ ] 在本地靶场或离线 fixture 上有可重复演示；
- [ ] `pytest`、`ruff` 和类型检查通过；
- [ ] 不夸大尚未实现或尚未验证的能力。

---

## 14. 常见问题

### `statebreaker: command not found`

确认虚拟环境已激活，或用：

```bash
python -m statebreaker --help
```

Windows 也可以直接运行 `.venv\Scripts\statebreaker.exe`。

### `plugins list` 看不到我的插件

最常见原因是装到了另一个 Python 环境。检查：

```bash
python -c "import sys; print(sys.executable)"
python -m pip show statebreaker
python -m pip show 你的插件包名
statebreaker plugins list
```

然后检查 `pyproject.toml` 的 Entry Point 组和导入路径。

### 提示核心 API 版本不兼容

v0.1 插件应声明：

```python
api_version="0.1"
```

并依赖：

```toml
"statebreaker>=0.1,<0.2"
```

不要通过删除版本检查强行运行。

### `${run_id}` 未定义

检查：

- 上游步骤是否真的返回该值；
- Extractor 表达式是否正确；
- 当前步骤是否依赖上游步骤；
- 插件是否跳过了准备步骤；
- 并发写同名变量时是否发生覆盖。

### Docker 报端口已占用

换一个本机端口：

```powershell
$env:STATEBREAKER_LAB_PORT = "18080"
docker compose up --build
```

### 两个并发请求都返回成功，但优惠只有 50

这不算已确认漏洞。检查是否真的使用同一个 `run_id`，请求是否在 150 ms 窗口内重叠，以及 after_state 是否来自独立查询。

### 并发测试偶尔失败

记录每轮发送和完成时间，不要只保留成功样本。先验证普通 `asyncio.gather`，再逐步实现时间偏移、提高并发数或更精确的门控。所有搜索都应有上限，并报告总轮次和成功率。

### Windows 中文路径导致命令失败

在 PowerShell 中用引号包住完整路径，并尽量使用当前仓库的相对路径。不要在批处理脚本中假定系统代码页。

---

## 15. 组员拿到压缩包后的最短流程

```text
1. 解压到不含管理员权限限制的目录
2. 创建 Python 3.11/3.12 虚拟环境
3. pip install -e ".[dev]"
4. statebreaker doctor
5. pytest -q
6. docker compose up --build
7. 浏览器验证老实兑换=50、双倍手速=100
8. pip install -e ./plugin-template
9. statebreaker plugins list
10. 复制 plugin-template 开始自己的插件
```

建议组员在开始开发前把第 1—9 步全部跑通，并保存终端输出。这样能够确认 Python、Docker、核心包、靶场和插件发现机制都正常。

---

## 16. 常用命令速查

```bash
# 环境检查
statebreaker doctor

# 查看已安装插件
statebreaker plugins list

# 导出 Schema
statebreaker schema export schemas

# 校验 Workflow
statebreaker workflow validate examples/coupon-race/workflow.yaml

# 通过 Capture 插件导入
statebreaker workflow import <source> --plugin <plugin_id> --options options.yaml --output workflow.json

# 查看并顺序重放正常流程
statebreaker workflow show workflow.yaml
statebreaker workflow replay workflow.yaml --target http://127.0.0.1:18080

# 查看业务规则
statebreaker invariants show invariants.yaml

# 学习候选规则
statebreaker learn <workflow.yaml> --plugin <plugin_id> --output learning-result.json

# 生成攻击计划
statebreaker generate <workflow.yaml> <invariants.yaml> --plugin <plugin_id> --output attack-plans.json

# 查看并明确选择一个计划
statebreaker plans list attack-plans.json
statebreaker plans select attack-plans.json --attack-type concurrent-replay --output selected-plan.json

# 执行攻击计划
statebreaker attack <plan.yaml> --workflow <workflow.yaml> --plugin <plugin_id> --output raw-attack-result.json

# 验证结果
statebreaker verify <result.json> <invariants.yaml> --plugin <plugin_id> --output findings.json

# 组装报告输入
statebreaker bundle build --workflow workflow.yaml --plan selected-plan.json --result result.json --findings findings.json --output run-bundle.json

# 生成报告
statebreaker report <bundle.json> --plugin <plugin_id> --output-dir <directory>

# 核心质量检查
pytest -q
ruff check .
mypy src/statebreaker

# 靶场
docker compose up --build
docker compose down
```

具体参数以 `statebreaker <command> --help` 为准。

---

## 17. 进一步文档

- [现场演示指南](docs/DEMO_GUIDE_ZH.md)
- [新版 CLI 参考](docs/cli.md)
- [架构说明](docs/architecture.md)
- [数据契约](docs/contracts.md)
- [插件开发说明](docs/plugin-development.md)
- [进度报告（中文）](docs/PROGRESS_REPORT_ZH.md)
- [Progress report (English)](docs/PROGRESS_REPORT_EN.md)
- [贡献指南](CONTRIBUTING.md)
- [模板插件说明](plugin-template/README.md)
- [竞态攻击计划生成插件](race-generator/README.md)
- [竞态攻击检测执行插件](race-executor/README.md)
- [差分 learner 插件](statebreaker-learner-delta/README.md)
- [基础 verifier 插件](statebreaker-verifier-basic/README.md)
- [PDF reporter 插件](statebreaker-reporter-pdf/README.md)
- [HAR Capture 插件](plugins/statebreaker-har-capture/README.md)
- [优惠券 Workflow 示例](examples/coupon-race/workflow.yaml)
- [优惠券 Invariant 示例](examples/coupon-race/invariants.yaml)
- [优惠券 AttackPlan 示例](examples/coupon-race/attack-plan.yaml)

如果 README 与代码冲突，以 `src/statebreaker/models.py` 中的模型和 CLI 的 `--help` 输出为当前 v0.1 的最终依据，并及时修正文档。
