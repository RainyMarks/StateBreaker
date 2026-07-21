# StateBreaker 汇报稿：流程、分工与泛化能力

这份文档用于课堂汇报。它不深入每个底层函数，但会把完整流程、四个模块分工、自动化程度、流量 capture 和泛化能力讲清楚。

## 我的角色：项目把控与端到端闭环

我负责把控整个项目：确定 StateBreaker 的目标、技术路线、质量门禁和演示流程。

项目目标不是写一组固定脚本去打某几个靶场，而是做一个黑盒、流量驱动的 race condition 发现工具。用户只提供一次正常流程流量，工具自动学习请求依赖、正常状态变化，再执行受控并发实验，最后用真实 trial 证据生成 finding、PoC、JSON 和 HTML 报告。

一句话流程：

```text
录制一次正常流程 -> 学习请求依赖和状态基线 -> 生成并发实验计划 -> 执行对照组/攻击组 -> 用证据判定并生成报告
```

我主要做的把控工作：

- 让工具保持黑盒：只能看 HTTP 流量、reset/probe/trial 证据，不能读取目标源码或内部变量。
- 让核心包保持业务无关：`src/statebreaker/` 不写具体业务词，不针对某个靶场路径或字段硬编码。
- 推进“一键打穿”体验：`statebreaker run` 串起项目、capture、discover、scan、report。
- 组织 10 个基础靶场、20 个高级靶场、20 个逐个扫描任务、5 个泛化性审查、5 个可读性/双语审查。
- 统一质量门禁：`python check.py` 跑 ruff、mypy strict 和 pytest。

## 整体流程

### 1. Capture：拿到正常流量

工具可以从 HAR/Postman 导入，也可以启动本地 HTTP 正向代理录制。用户只需要像正常用户一样操作一遍目标系统。

关键点：capture 记录的是公开 HTTP 请求/响应，不读取目标代码。

```python
recorder = await start_http_proxy_recorder(
    capture_id=capture_id,
    project=project,
    listen_host=listen_host,
    listen_port=listen_port,
    allow_public_bind=allow_public_proxy,
)
```

在流程中的位置：这是所有自动分析的输入。没有 capture，就没有依赖、状态基线和并发计划。

### 2. Discover：从正常流量里学习结构

Discover 不做攻击，只分析正常流程。它会做 trace 归一化、值流追踪、模板化、正常流回放确认、工作流图构建和状态 probe 发现。

关键代码片段：

```python
trace = normalize_trace(trace, base_url=project.project.base_url)
bindings = infer_bindings(trace.exchanges)
templates = build_templates(trace.exchanges, bindings)
replay = await replay_flow(templates, bindings, sender, session_id=session_id)
confirmed = evaluate_bindings(bindings, replay)
graph = build_graph(trace, confirmed, graph_id=f"graph-{trace.capture_id}")
probes = discover_probe_candidates(graph)
```

在流程中的位置：它把“录到的一串 HTTP”变成“可理解的工作流图”和“可回读状态的 probe”。

### 3. Baseline + Planning：学习正常行为并生成并发计划

Baseline 会在干净状态下执行控制组、单次动作、顺序重复动作，学习正常状态变化和不变量。Planning 根据共享资源、动作风险、session 组合、调度器能力生成并发实验计划。

在流程中的位置：这里决定“哪些动作值得 race”和“怎么 race”。

### 4. Execution + Oracle + Report：真实执行并给证据

Execution 负责执行 control trial 和 attack trial。Oracle 比较正常组和并发组的状态变化，如果并发结果突破正常不变量，才输出 `CONFIRMED`。报告阶段输出 PoC、JSON bundle 和 HTML。

在流程中的位置：这是最终可信度来源。工具不会只因为 HTTP 200 或某个字段名就报漏洞，必须有真实 trial 证据。

## 四个模块分工

下面先假设团队有田、马、赵、李四位同学。

## 田：流量 capture 与项目入口

负责模块：

- CLI 一键入口：`statebreaker run`、`statebreaker wizard`
- capture import：HAR/Postman 导入
- capture proxy：本地 HTTP 正向代理录制
- 项目初始化和用户交互提示

大致原理：

田负责把“用户的一次正常操作”变成标准 `CapturedTrace`。工具不要求用户手写复杂命令，也不要求知道目标业务代码。代理只监听本机，用户把浏览器或客户端临时指向代理，然后正常操作一遍。

关键代码片段：

```python
selected_capture_id = capture_id or _default_proxy_capture_id()
return anyio.run(
    _record_proxy_capture_async,
    project,
    selected_capture_id,
    listen_host,
    listen_port,
    max_exchanges,
    allow_public_proxy,
)
```

在流程中的位置：

```text
用户正常操作 -> capture -> CapturedTrace -> 后续 discover/scan
```

自己做了啥：

- 把 HAR/Postman 和本地代理录制统一成同一种 capture。
- 加了 loopback-only 安全默认值，避免无意暴露代理。
- 处理 chunked/gzip/deflate 等常见 HTTP 细节，让真实流量更容易录下来。
- 让 `run` 默认自动串完整流程，`wizard` 适合需要逐步确认的演示。

## 马：依赖推断、模板化与状态探针

负责模块：

- trace normalizer
- value lineage
- request template
- workflow graph
- probe discovery

大致原理：

马负责回答一个问题：从一次正常流量中，工具怎么知道哪些请求有关联？

核心思路是“值从哪里来、又被哪里消费”。比如第一个响应返回一个 id，后面的 path/query/header/body 使用了这个 id，工具就能推断出生产者和消费者关系。然后把具体值替换成变量模板，后续重放时可以适配新创建的资源。

关键代码片段：

```python
bindings = infer_bindings(trace.exchanges)
templates = build_templates(trace.exchanges, bindings)
replay = await replay_flow(templates, bindings, sender, session_id=session_id)
confirmed = evaluate_bindings(bindings, replay)
```

在流程中的位置：

```text
CapturedTrace -> 变量绑定 -> 请求模板 -> 工作流图 -> 状态 probe
```

自己做了啥：

- 设计值识别规则：UUID、数字 ID、token、短机器 ID、header/body/query 中的资源值。
- 做前向 lineage：只追踪“先出现、后消费”的值，降低误判。
- 做 replay 验证：不是猜到依赖就算数，而是重放正常流确认依赖真的可用。
- 做状态 probe 发现：找到能观察状态变化的 GET/状态查询请求。

## 赵：基线学习、候选生成与泛化

负责模块：

- baseline learner
- invariant learner
- candidate ranking/filtering
- attack plan synthesis

大致原理：

赵负责把“可能有关系的动作”变成“值得并发测试的计划”。

工具先学习正常行为：动作前状态是什么，单次动作后状态怎么变，顺序重复后状态怎么变。然后生成候选，比如同一个动作并发、两个动作共享资源、跨 session 竞争同一个资源。

关键代码片段：

```python
actions = find_mutating_actions(templates, probes=probes, max_actions=max_actions)
control = await self._run_control(action, probes, normalizers, bindings)
single = await self._run_single(action, probes, normalizers, bindings)
sequential = await self._run_sequential(action, probes, normalizers, bindings)
effect = self._build_effect(action, single, sequential)
```

在流程中的位置：

```text
工作流图 + 状态 probe -> baseline -> candidate -> attack plan
```

自己做了啥：

- 不依赖具体业务字段，而是看状态差分、数值变化、重复执行效果。
- 支持固定路径/header/body/query 中的资源，不只依赖 URL path 里的 id。
- 加强短 ID 识别，例如 `AB-7`、`o-1` 这类机器式短标识。
- 支持多种数据结构场景：嵌套 dict/list、集合、队列、图边、树形层级、ledger、CAS、复合 key、分片库存等。

### 老师重点：如何做到泛化

泛化不是靠“靶场关键词列表”，而是靠结构和行为信号：

- 结构信号：值在响应、path、query、header、body 之间如何流动。
- 时间信号：值必须先产生后消费。
- 状态信号：动作前后 probe 能观察到状态变化。
- 行为信号：单次、顺序重复、并发执行之间的差异。
- 证据信号：`CONFIRMED` 必须能追溯到真实 `ExecutionTrial`。

我们还做了验证：

- 20 个高级本地靶场覆盖不同数据结构和不同流量形态。
- 每个高级靶场都由 StateBreaker 黑盒扫描通过。
- 额外做了 5 个泛化性审查，确认核心包没有针对靶场名、路径或业务字段硬编码。
- 还做了扰动测试：插入噪声 probe、把资源额外放到 header/query/body、短 ID 和通用字段名都能通过。

当前已知边界也要如实说明：

- 正常流量里最好包含动作前后的状态查询，否则 baseline 可能学不到 effect。
- 真实 TCP、更多 scheduler、GraphQL/XML/text probe 等还可以继续扩展。
- 身份 header 目前有常见字段名单，未来可做更智能的 identity header 推断。

## 李：执行器、Oracle 与报告

负责模块：

- session manager
- reset strategy
- scheduler backend
- trial execution
- oracle verdict
- report / PoC / HTML / JSON

大致原理：

李负责把计划变成真实实验，并给出证据。每个计划会执行控制组和攻击组。控制组代表正常顺序行为，攻击组代表并发释放请求。Oracle 比较两者的状态变化，只有并发组产生超出正常基线的结果，才输出 finding。

关键代码片段：

```python
findings = await self._execute_plans(
    controller,
    planning.plans,
    baseline.profile,
    repetitions=repetitions,
    require_state_evidence=project.oracle.require_state_evidence_for_confirmed,
    outcome=outcome,
)
```

在流程中的位置：

```text
attack plan -> control/attack trials -> oracle verdict -> reports
```

自己做了啥：

- 把 reset 放到 discover/scan 前和每轮 trial 前，减少脏状态干扰。
- 支持 `async-http` 等调度器，后续可扩展 last-byte、HTTP/2 gate。
- 输出 PoC、JSON evidence bundle 和 HTML 报告。
- 报告和 CLI 做展示层脱敏，JSON 结构保持机器可解析。
- 加入中英双语展示，降低新手和汇报时的理解门槛。

## 自动化程度

最简使用：

```bash
statebreaker run --project demo --proxy-capture
```

用户需要做的只有：

1. 确认授权测试环境。
2. 配好目标 `base_url`。
3. 按提示让浏览器/客户端走本地代理。
4. 正常操作一遍目标流程。
5. 等工具自动完成 discovery、scan、report。

工具自动完成：

- 项目创建或选择
- capture 保存
- 正常流回放
- 依赖确认
- 状态 probe 发现
- baseline 学习
- candidate 生成
- attack plan 合成
- control/attack trial 执行
- oracle 判定
- PoC/JSON/HTML 报告生成

如果老师关注安全性，可以强调：`wizard` 模式可以逐步确认；`run` 模式默认自动化程度更高，适合演示和回归测试。

## 演示建议

推荐演示顺序：

1. 展示 `statebreaker run --proxy-capture`，说明用户只录一次正常流程。
2. 展示 discover 输出：workflow nodes、confirmed dependencies、state probes。
3. 展示 scan 输出：race plans tested、finding verdict、success rate。
4. 打开 HTML 报告，说明证据来自真实 trials。
5. 展示 `docs/advanced-labs.md`，说明 20 个高级靶场覆盖不同数据结构。
6. 展示泛化性结论：不是针对靶场代码，而是靠 HTTP trace、状态 probe 和 trial evidence。

## 结论

StateBreaker 的核心价值是把 race condition 测试从“手写并发脚本”变成“录一次正常流量后的自动化证据链”。它不需要知道目标源码，也不应该依赖具体业务名。工具通过流量结构、状态变化和真实 trial 证据来泛化到不同数据结构、不同接口形态和不同并发风险场景。
