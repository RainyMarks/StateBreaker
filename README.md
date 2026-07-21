# StateBreaker v0.2

StateBreaker 是一个面向授权安全测试的黑盒竞态条件发现工具。用户只需要提供一次“正常业务流程”的流量记录，工具会学习请求依赖和正常状态变化，再执行受控并发实验；只有真实 trial 证据支持时，才输出 finding、PoC、JSON 和 HTML 报告。

适合给老师汇报的一句话：

```text
录一次正常操作 -> 自动学习依赖和基线 -> 对高风险动作做可控并发实验 -> 用真实证据生成报告
```

## 先用一键检测

默认最简入口是：

```bash
statebreaker run
```

它会自动串起项目创建或选择、正常流量录制或导入、discovery 预览、完整扫描和报告生成。默认推荐代理录制：

```bash
statebreaker run --project my-target --proxy-capture
```

如果希望每个关键阶段都手动确认，可以使用交互向导：

```bash
statebreaker wizard --project my-target --proxy-capture
```

## 用户需要做什么

1. 确认目标是授权测试环境，不要对生产系统或无授权系统运行并发实验。
2. 准备测试账号、目标 `base_url`，必要时准备 reset endpoint，让每轮实验能回到干净状态。
3. 运行一键检测；首次创建项目时按提示填写项目名和 `Base URL`。
4. 通过代理或 HAR/Postman 提供一段“正常操作”流量，例如登录后完成一次兑换、下单、领取、扣款等标准流程。
5. 查看 discovery 预览；`run` 会默认继续自动扫描，若想逐步确认请改用 `wizard`。
6. 扫描结束后查看 findings 和报告文件。

## 语言设置 / Language

CLI 面向新手的帮助和关键提示支持轻量双语输出，不会翻译 JSON 字段、artifact schema 或报告 bundle 中的机器可解析键名。

```bash
# 默认：英文锚点 + 中文解释
set STATEBREAKER_LANG=bilingual

# 只看中文
set STATEBREAKER_LANG=zh-CN

# 只看英文
set STATEBREAKER_LANG=en
```

非法值会回退到 `bilingual`。在 Windows PowerShell 中可以使用 `$env:STATEBREAKER_LANG="zh-CN"` 临时设置当前终端。

## 代理录制步骤

默认推荐代理录制，不需要手动导出 HAR：

```bash
statebreaker run --project my-target --proxy-capture
```

按终端提示操作：

1. 工具启动本地 HTTP 正向代理，默认地址是 `127.0.0.1:8088`。
2. 把浏览器或客户端代理设置为这个地址。
3. 在目标系统里只做一遍正常流程，不要夹杂无关操作。
4. 回到终端按 Enter 停止录制，流量会保存为 capture。
5. 程序继续完成 discovery、扫描和报告生成；若使用 `wizard`，会在扫描前让你确认。

如果已经有 HAR 或 Postman collection，也可以直接导入：

```bash
statebreaker run --project my-target --capture-file normal.har
```

## 安全边界

- 只在授权测试环境使用。并发实验会真实发送请求，可能改变测试账号的数据状态。
- 代理没有认证，默认只允许监听 loopback 地址。不要把它暴露到不受信网络。
- 如果确实要让代理绑定非 loopback 地址，底层高级命令要求显式使用 `--unsafe-public-proxy`，且只应短时用于受信网络。
- 当前代理录制 HTTP 明文流量；HTTPS CONNECT 会盲转发（方便浏览器加载 CDN），但不解密、不入库。要分析 HTTPS 请导入 HAR/Postman。
- `CONFIRMED` finding 不能只靠 HTTP 200 判断，必须能追溯到真实 `ExecutionTrial` 证据。
- 报告和 CLI 展示会做展示层脱敏；存储的证据不应被报告流程反向改写。

## 报告输出

扫描确认问题后，报告会写入：

```text
.statebreaker/projects/<project>/reports/
```

常见输出包括：

- `<finding-id>.report.json`：证据 JSON bundle。
- `<finding-id>.html`：适合打开查看的 HTML report。
- `<finding-id>-poc.py`：如果有可复现攻击 trial，会生成可执行 PoC。

需要重新生成或查看某个 finding 时：

```bash
statebreaker findings list --project my-target
statebreaker report FINDING-ID --project my-target
statebreaker reproduce FINDING-ID --project my-target
```

## 高级模式

需要精确控制单个阶段时，再使用分阶段命令：

```bash
statebreaker project init my-target
statebreaker capture import normal.har --project my-target
statebreaker capture proxy --project my-target
statebreaker capture browser --project my-target
statebreaker discover --project my-target
statebreaker scan --project my-target --auto
statebreaker findings list --project my-target
statebreaker report FINDING-ID --project my-target
statebreaker reproduce FINDING-ID --project my-target
```

命令含义：

- `discover` 只分析正常流，不发起并发攻击实验。
- `scan` 跑自动扫描，包括 baseline、候选、计划、真实 trial、verdict 和 findings；报告需要再运行 `report`，或直接用 `run`/`wizard` 自动生成。
- `capture proxy` 单独启动本地 HTTP 代理录制，适合拆开调试 capture。
- `report` 根据已存 finding 生成 PoC、JSON 和 HTML。
- `reproduce` 打印对应 finding 的可执行 PoC。

## 新手阅读顺序

1. 先读本文，直接按一键检测跑通一次。
2. 再读 `docs/architecture.md`，理解一次扫描经过哪些模块。
3. 读 `docs/blackbox-boundaries.md`，理解 StateBreaker 能看见什么、不能看见什么。
4. 读 `docs/advanced-labs.md`，了解 20 个高级本地靶场和默认代表扫描样本。
5. 遇到术语时查 `docs/concepts.md`。
6. 如果要做课堂汇报，读 `docs/presentation-report.md`。
7. 如果关注可发表性与冲顶路线，读 `docs/paper-research.md`。
8. 如果要改代码，读 `docs/developer-guide.md`，按功能类型找到入口。
9. 每次改完运行 `python check.py`，它会依次跑 ruff、mypy strict 和 pytest。

## 输入和输出

输入：

- 一段正常流程的流量记录，例如 HAR、Postman collection，或测试里构造出的 `CapturedTrace`。
- `project.yaml`，包括目标 `base_url`、允许访问的 host、测试账号、reset 策略、执行预算等。
- 可选 reset endpoint，用来让每轮实验回到干净状态。

输出：

- `.statebreaker/projects/<project>/graphs/`：推断出的 workflow graph。
- `.statebreaker/projects/<project>/baselines/`：正常行为和 learned invariants。
- `.statebreaker/projects/<project>/plans/`：自动生成的 attack plans。
- `.statebreaker/projects/<project>/trials/`：真实执行过的 control/attack trials。
- `.statebreaker/projects/<project>/findings/`：最终 verdict 和证据引用。
- `.statebreaker/projects/<project>/reports/`：PoC、JSON bundle、HTML report。

## 开发和质量门

```bash
python check.py
```

这个命令必须通过：

- `ruff check src tests`
- `mypy --strict`
- `pytest tests -q`

当前核心约束：

- `src/statebreaker/` 必须保持业务无关，不出现目标业务词。
- 每个 `CONFIRMED` finding 必须能追溯到真实 `ExecutionTrial`。
- learned invariants 和 baselines 必须保留 supporting trial ids。
- 不要在自动发现流程成熟前引入 Web UI。

## 目录速览

- `src/statebreaker/models/`：严格的 Pydantic 契约，所有 artifact 都基于这里。
- `src/statebreaker/intelligence/`：从正常流中推断变量、模板、workflow graph 和 probes。
- `src/statebreaker/baseline/`：学习正常行为、状态变化和 invariants。
- `src/statebreaker/discovery/` + `planning/`：从 learned behavior 生成候选和 attack plans。
- `src/statebreaker/execution/`：reset、sessions、request rendering 和 scheduler backends。
- `src/statebreaker/oracle/`：对比 control/attack trial，输出 verdict。
- `src/statebreaker/orchestration/`：把上面阶段串成 `discover` 和 `scan`。
- `labs/`：带业务名的脆弱教学靶场；核心包里不能出现这些业务名。
- `tests/`：当前最重要的行为说明书。
