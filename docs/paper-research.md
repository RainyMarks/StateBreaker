# StateBreaker 可发表性调研纪要

本文档整理自 2026-07-21 的并行调研（学术相关工作、工业工具、创新点定位、评测与 benchmark、会议期刊匹配），并经主代理对关键文献做了复核。用于判断：当前题目有没有机会发一区/顶会，以及要补什么。

**一句话结论：** 有真实研究空白，不是纯工程堆砌；但当前形态直接冲安全顶会偏早。补齐“真实系统新漏洞 + 强对照 + 论文级 benchmark”后，有机会冲击软件工程顶会，再冲安全顶会。

---

## 1. 题目如何定义才有投稿价值

不要卖 CLI / 一键按钮，要卖研究问题：

> 在完全黑盒、无业务语义、无源码、无手工 workflow 标注的条件下，如何系统性发现 Web API 上的状态一致性破坏类并发漏洞（request race / stateful race）？

建议英文题目方向：

> Trace-Driven Black-Box Discovery of Stateful Race Conditions in Web APIs

StateBreaker 对应的方法主张：

1. 用户只录一次正常业务流程流量（HAR / Postman / 本地代理）。
2. 自动学习请求依赖（value lineage）、workflow graph、state probe、baseline 与 invariants。
3. 自动生成 race candidate 与 attack plan，执行 control vs attack trial。
4. 用 state-aware differential oracle 与真实 trial 证据确认 finding。
5. 核心逻辑 business-agnostic，不依赖字段名 / 路径名 / 业务词。

---

## 2. 相关工作全景

现有工作大致分三派。StateBreaker 的差异化空间，正好卡在三派的交叉空白处。

### 2.1 白盒 / 灰盒学术检测（强自动，非纯黑盒 HTTP）

| 工作 | Venue | 做法 | 局限 |
|---|---|---|---|
| [RACEDB](https://yonghwi-kwon.github.io/data/racedb_sp25.pdf) | IEEE S&P 2025 | Application-aware request race detection + replay 验证；14 个 PHP 应用，39 个漏洞（18 未知，7 CVE） | 需要 PHP 源码 / concolic / DB 相关分析，面向 LAMP |
| [ReqRacer](https://bugjin.github.io/fse-2021-reqrace.pdf) | ESEC/FSE 2021 | 动态记录 shared-resource / happens-before，推断危险 interleaving 并重放 | 需要运行时 instrumentation / 日志侧信息 |
| [ACIDRain / 2AD](https://www.bailis.org/papers/acidrain-sigmod2017.pdf) | SIGMOD 2017 | 从数据库访问轨迹分析隔离异常，验证电商类并发攻击 | 需要 DB trace，不是纯 HTTP 黑盒 |

**对 StateBreaker 的启示：**  
“control vs concurrent / serialized vs concurrent”对照验证并不新；顶会门槛是真实系统与新漏洞规模。StateBreaker 的差异必须强调：**不碰源码、不碰 DB log，只靠公开 HTTP 正常流。**

### 2.2 工业黑盒利用（调度器强，自动化发现弱）

| 工作 | 来源 | 做法 | 局限 |
|---|---|---|---|
| [Smashing the state machine](https://portswigger.net/research/smashing-the-state-machine) | PortSwigger / DEF CON 31 | 扩展 race 攻击面，提出 single-packet attack | 人工选请求、人工判结果 |
| [Single-packet attack](https://portswigger.net/research/the-single-packet-attack-making-remote-race-conditions-local) | PortSwigger | HTTP/2 多路复用 + 最后片段同包释放，压低 jitter | 是调度/利用技术，不是端到端发现系统 |
| Turbo Intruder | Burp 扩展 | 高并发 / gate 同步 | 需要人工脚本与人工判定 |

**对 StateBreaker 的启示：**  
`http1-last-byte` / `http2-stream-gate` 单独不够新。调度器只能当支撑模块；论文核心必须是**自动发现与状态感知判定**。

### 2.3 Stateful REST API fuzzing（状态探索强，race 目标弱）

代表：RESTler（ICSE 2019 一带）、EvoMaster、Schemathesis 等。

这些工具擅长：

- 基于 OpenAPI / 规格做状态化探索；
- 找 500、spec 违反、部分逻辑错误。

它们通常不专门做：

- 黑盒 race candidate 生成；
- 正常行为 baseline / invariant 学习；
- control vs attack 的状态差分确认。

### 2.4 研究空白是否成立？

**成立，但叙事要精确。**

可辩护的空白：

> 目前缺少一套“只录一次正常 HTTP 流量、不依赖源码/DB/业务词、自动学习依赖与状态 probe、并用证据确认”的 Web API race discovery 方法。

容易被打回的过度宣称：

- “我们发明了 race condition 检测” —— 太宽，RACEDB/ReqRacer/ACIDRain 已覆盖。
- “我们发明了 single-packet / last-byte 同步” —— 工业界已有。
- “我们发明了 control vs attack 对照” —— 学术验证框架已有类似思想。

---

## 3. 创新点新颖度评估

| 候选贡献 | 新颖度 | 可辩护性 | 审稿风险 |
|---|---|---|---|
| 黑盒、business-agnostic、trace-driven race 发现范式 | 高 | 高 | 必须证明不是“手工选请求 + 自动发” |
| 自动 state probe 发现与验证 | 中高 | 高 | 需说明漏检/误检边界 |
| state-aware differential oracle（baseline + invariant + control/attack） | 中 | 中高 | 对照思想不全新，要强调“从正常流自动学习” |
| 无语义依赖的泛化 | 中 | 取决于实验 | 自建靶场易被质疑过拟合 |
| 多调度器 + 最小化证据 | 低到中 | 中 | 只能作支撑，不能当主贡献 |

### 建议的顶会级 contribution 叙事（草案）

1. 提出面向 Web API 的 **trace-driven black-box stateful race discovery** 范式：只需一次正常流量，无需源码、DB 轨迹或业务规则。
2. 提出自动 **state probe discovery + baseline/invariant learning**，把“哪里观察状态、什么是正常”从人工经验变成可学习组件。
3. 提出 **evidence-backed differential oracle**：以真实 control/attack trial 证据确认，而不是只看 HTTP 200 或响应文本相似度。
4. 构建并开源面向多数据结构 / 多流量形态的黑盒评测基准，并在真实开源系统上验证。

---

## 4. 工业工具对比（差异化）

| 能力 | Turbo Intruder / Burp race | RESTler / EvoMaster | RACEDB / ReqRacer / ACIDRain | StateBreaker |
|---|---|---|---|---|
| 纯黑盒 HTTP | 是 | 部分（常需 OpenAPI） | 否（源码/DB/instrument） | 是 |
| 只需一次正常流录制 | 否（人工选） | 否 | 否 | 是 |
| 自动学依赖 / workflow | 否 | 有限 | 有，但依赖白盒信息 | 是 |
| 自动发现 state probe | 否 | 否/弱 | 不同形式 | 是 |
| 状态感知 oracle | 人工 | 多为 status/spec | 有验证器 | 自动 differential |
| 强调度器 | 很强 | 弱 | 不同 | 有，非主卖点 |
| 业务无关 | 是 | 中 | 中 | 目标是是 |

**结论：**  
相对工业工具，StateBreaker 强在自动化发现；相对学术白盒工具，StateBreaker 强在纯黑盒与业务无关。这两者合在一起，才构成可投稿差异。

---

## 5. 评测与 Benchmark 该怎么做

### 5.1 现有基础与不够之处

当前已有：

- 约 10 个基础本地靶场；
- 20 个高级本地靶场（多数据结构 / 多流量形态）；
- 黑盒 `CapturedTrace -> AutoRaceScanner` 路径；
- 泛化性审查：未见硬编码靶场名/业务词驱动核心逻辑。

不够冲顶会的原因：

- 靶场多为自建，审稿人会问“是不是只打自己的题”；
- 缺少与 Turbo Intruder / naive baseline / 消融版的公平对照；
- 缺少真实开源系统上的 previously unknown 发现；
- 注意：已有名为 RaceBench 的 C/C++ 并发 benchmark，论文命名应避开冲突（可考虑 `RACE-REST`、`StatefulRace-Bench`、`APIRaceBench` 等）。

### 5.2 论文级 benchmark 设计建议

建议规模：40–80 个场景，至少覆盖：

1. **Race 类型：** same-action、cross-user、cross-action、limit overrun、limit under、state machine illegal transition。
2. **数据结构：** scalar、nested object、array/list、set、queue、graph/edge、tree、ledger、CAS/version、composite key、sharded resource。
3. **流量形态：** path 变量、header-only、body-only、query-only、混合 key、短 ID、幂等头。
4. **观察形态：** GET JSON object、JSON array、派生 rollup probe、动作前后 probe 完整性。

每个场景必须提供：

- 公开 HTTP normal flow（黑盒 recorder）；
- reset / 隔离方式；
- 可观察不变量（不要写内部 sleep/锁位置）；
- ground-truth 标签与难度。

防硬编码措施：

- 检测器不得 import 靶场内部状态；
- 字段名/路径扰动回归；
- 未见场景 hold-out；
- 核心包业务词门禁测试。

### 5.3 真实评测系统方向

优先选：可本地部署、有状态写操作、可 reset、有公开 HTTP API 的开源系统。方向包括：

- 电商 / 库存 / 购物车；
- 预订 / 席位 / hold；
- 钱包 / ledger / voucher；
- 工单 / 审批 / 工作流；
- 配额 / 限流 / 会员权益。

参考线索：

- RACEDB 使用过一批 PHP 应用（OpenCart、WordPress、phpBB、osCommerce 等）——可作为白盒竞品对比对象，但不等于 StateBreaker 只能测 PHP。
- REST API fuzzing 文献常用的 EMB / 自托管服务集合，可筛出有状态写接口的子集。
- 真实 CVE / advisory 中的 TOCTOU / race 案例（如部分电商平台已知竞态）可用于“已知漏洞复现”实验。

### 5.4 必做指标与对照

**Baseline：**

1. 人工 + Turbo Intruder / single-packet（上限参考）。
2. Naive：重复 mutating 请求 + HTTP status oracle。
3. Path-only candidate generator（去掉 header/body/query / fixed-path 支持）。
4. No-probe / no-baseline / no-lineage 消融版。

**指标：**

- confirmed / probable / rejected；
- 真阳性、假阳性、假阴性；
- requests / trials / wall-clock；
- time-to-first-finding；
- 人工配置成本（分钟）；
- 重复实验成功率与最小化后并发度。

---

## 6. Venue 匹配与投稿路线

### 6.1 匹配表（务实）

| Venue | 类型 | 匹配度 | 门槛 |
|---|---|---|---|
| IEEE S&P / CCS / USENIX Security / NDSS | 安全顶会 | 中高（题目相关） | 真实新漏洞、强对比、低误报、负责任披露 |
| ICSE / FSE / ASE / ISSTA | 软件工程顶会 | 高（自动化测试/oracle/benchmark） | 消融、基准、可复现工件 |
| TDSC / TIFS / TSE / TOSEM / CoS | 期刊 | 中高 | 完整实验与扩展讨论 |
| ACSAC / RAID / 应用安全会议 | 会议 | 中 | 工程完整性 + 一定真实效果 |
| 中文核心 / EI / 教学或工程论文 | 国内/应用 | 高（当前即可） | 系统实现 + 靶场验证即可起步 |

### 6.2 分层结论

| 形态 | 现实可投层级 |
|---|---|
| 当前：自建靶场 + CLI + 黑盒扫描通过 | 工程/教学论文、部分应用会议、偏三区或会议起步 |
| 补强：对照 + 消融 + 论文级 benchmark | 软件工程顶会 / 一区或二区期刊有机会 |
| 再补强：真实系统新发现 / CVE | 才具备冲击安全顶会的资格 |

### 6.3 分阶段路线建议

**阶段 A（1–2 个月）：把故事立住**

- 固定论文主叙事与 contribution；
- 整理 RaceBench 风格元数据（分类、难度、黑盒 trace）；
- 完成消融实验脚手架。

**阶段 B（2–4 个月）：把实验做硬**

- 接入 5–10 个真实开源系统；
- 复现若干已知 race；
- 力争发现并负责任披露新问题；
- 与 naive / Turbo Intruder 人工基线对比。

**阶段 C：投稿**

1. 先投 ISSTA / ASE / ACSAC 试水；
2. 有完整工件与真实结果后冲 ICSE / FSE；
3. 有足够新漏洞证据后再冲 S&P / CCS / USENIX Security / NDSS。

---

## 7. 拒稿风险清单（按优先级）

1. **只在自建靶场上成功** —— 被认定过拟合。
2. **没有公平 baseline** —— 无法证明自动化带来收益。
3. **把调度器当主贡献** —— 相对 PortSwigger 工作不够新。
4. **oracle 误报不可控** —— 状态噪声导致“看起来 confirmed”。
5. **录制质量敏感** —— 缺动作前后 probe 就学不到 effect，需在论文中诚实报告并给缓解。
6. **与 RACEDB 对比不充分** —— 即使威胁模型不同，也必须明确讨论差异与局限。

---

## 8. 关键参考文献与链接

### 学术

- RACEDB (IEEE S&P 2025): https://yonghwi-kwon.github.io/data/racedb_sp25.pdf
- ReqRacer (ESEC/FSE 2021): https://bugjin.github.io/fse-2021-reqrace.pdf
- ACIDRain (SIGMOD 2017): https://www.bailis.org/papers/acidrain-sigmod2017.pdf
- S&P 2025 accepted papers: https://sp2025.ieee-security.org/accepted-papers.html

### 工业

- Smashing the state machine: https://portswigger.net/research/smashing-the-state-machine
- Single-packet attack: https://portswigger.net/research/the-single-packet-attack-making-remote-race-conditions-local
- Turbo Intruder race example: https://github.com/PortSwigger/turbo-intruder/blob/master/resources/examples/race-single-packet-attack.py

### 项目内相关文档

- 课堂汇报稿：`docs/presentation-report.md`
- 黑盒边界：`docs/blackbox-boundaries.md`
- 高级靶场总览：`docs/advanced-labs.md`
- 架构说明：`docs/architecture.md`

---

## 9. 给项目组的行动建议

如果目标是“能发一区/顶会”，下一阶段优先做这三件事，而不是继续堆 CLI 功能：

1. **真实系统评测包**：选可部署开源目标，统一 reset、录制、扫描脚本。
2. **对照与消融**：naive / 人工 Turbo Intruder / 去掉 probe / 去掉 baseline / 去掉 lineage。
3. **论文级 benchmark 元数据**：给现有 30 个靶场补分类、难度、不变量、hold-out 协议，并扩到未见系统。

如果目标是“先有一篇稳妥论文”，可以把当前系统做成：

> 黑盒 Web API race 自动化测试框架 + 多数据结构教学/评估靶场

投应用安全会议或软件工程工具类轨道，再迭代冲更高 venue。
