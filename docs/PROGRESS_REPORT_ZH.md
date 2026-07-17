# StateBreaker 当前项目状态（中文版）

- **仓库：** [RainyMarks/StateBreaker](https://github.com/RainyMarks/StateBreaker)
- **核心版本/API：** `0.1.0` / `0.1`
- **更新日期：** 2026-07-17
**当前开发状态：** Capture 已合并主干；分阶段 CLI 和英文默认靶场位于 PR #5

## 1. 项目现在是什么

StateBreaker 是面向业务逻辑状态漏洞的可扩展测试框架。它关注普通扫描器不容易发现的
问题，例如优惠券并发重复兑换、余额双花、一次性 Token 重放、跨用户领取和流程跳步。

项目目前已经不是只有接口的空骨架，也不是完整的通用扫描器。准确定位是：

> 一套已经跑通“正常流程 → 候选规则 → 攻击计划 → 真实并发 → 状态验证 → PDF 报告”
> 的通用插件骨架；当前装入骨架并跑通的第一套具体实现，是 HAR importer 加老王奶茶券
> 竞态实验。框架的接口可替换，但现阶段不宣称算法已经覆盖其他业务场景。

## 2. 当前目录和内容

```text
src/statebreaker/                   核心模型、runtime、插件发现、pipeline、CLI
plugins/statebreaker-har-capture/   HAR 1.2 Capture 插件
statebreaker-learner-delta/         正常状态差分 Learner
race-generator/                     竞态攻击计划 Generator
race-executor/                      有界并发 Executor
statebreaker-verifier-basic/        状态规则 Verifier
statebreaker-reporter-pdf/          PDF Reporter
plugin-template/                    新组员插件模板
labs/coupon-race/                   老王奶茶 BUG50 Docker 靶场
examples/coupon-race/               Workflow、Invariant、AttackPlan 示例
docs/                               演示、架构、契约和开发文档
```

## 3. 六阶段完成度

```text
capture → learner → generator → executor → verifier → reporter
   ✅        ✅         ✅          ✅         ✅         ✅
```

| 阶段 | plugin_id | 已实现 | 当前边界 |
|---|---|---|---|
| Capture | `har.capture` | 离线 HAR 1.2、同源校验、JSON/Form、Cookie/Auth | 不自动推断动态 ID/Extractor，不支持多 origin |
| Learner | `team.delta-learner` | 多轮正常重放、max-delta/min/state-transition 候选 | 规则是样本候选，需人工确认 |
| Generator | `team.race-generator` | concurrent、burst、offset、幂等键等约 10 类计划 | 目标识别仍偏 coupon/race 标签 |
| Executor | `team.race-executor` | 真实 HTTP、有界并发、状态快照、事件时间线 | 尚无正式 Last-Byte Gate |
| Verifier | `team.basic-verifier` | max-delta、min、count、single-use、transition | 依赖可查询的业务状态 |
| Reporter | `team.pdf-reporter` | PDF、JSON 摘要 | PDF 使用便携 Latin 字体 |

另有 `template.dry-run`，只用于解释插件发现，不发送请求。

## 4. 核心提供什么

- Pydantic 公共模型和 JSON Schema；
- 每个命名 session 独立的 `httpx.AsyncClient` 和 Cookie Jar；
- `${variable}` 模板替换；
- JSONPath/Header/Regex 动态提取；
- Authorization、Cookie、Token、Password 等事件日志脱敏；
- JSONL 事件、correlation ID、request ordinal、单调时间；
- Entry Point 自动发现、版本兼容和重复 ID 检查；
- 稳定退出码：输入 2、插件 3、运行时 4；
- 分阶段 CLI 和 CI 自动 pipeline。

## 5. 新 CLI 的关键变化

旧交互式 wizard 已删除。当前 CLI 不用一键 `demo` 隐藏过程，而是让老师看到真实步骤：

```text
workflow show/replay
→ invariants show
→ generate
→ plans list/select
→ attack
→ verify
→ bundle build
→ report
```

执行 `attack` 时会直接显示两个请求的 SEND/DONE 相对时间、HTTP 状态、服务器 check /
commit 数、攻击前后状态和数值 delta。`pipeline run` 仍保留给 CI 和批处理。

CLI 自身只显示通用对象（Workflow、Invariant、AttackPlan、RawAttackResult、Finding）和插件
阶段。课堂上看到的 `BUG50`、`redeem`、`discount_yuan` 来自本次传入的奶茶券示例数据，
而不是 CLI 的固定字段。更换场景时仍使用相同命令，只替换数据文件、目标地址和对应插件；
这就是 v0.1 当前可以证明的通用性边界。

完整命令见 [现场演示指南](DEMO_GUIDE_ZH.md) 和 [CLI 文档](cli.md)。

## 6. 老王奶茶靶场

靶场是单容器 FastAPI + 原生 HTML/CSS/JS，默认只绑定本机。每次 `POST /api/runs`
创建隔离实验。兑换逻辑故意包含 150 ms TOCTOU 窗口：

```text
检查 coupon_used == false
→ await 150 ms
→ discount += 50
→ coupon_used = true
```

顺序重放结果：

```text
discount_yuan: 0 → 50
successful_redemptions: 0 → 1
```

两请求并发结果：

```text
checks=2, commits=2, rejections=0
discount_yuan: 0 → 100
successful_redemptions: 0 → 2
```

Invariant 允许最大增量 50，实际增量 100，因此 verifier 输出 `CONFIRMED`。

## 7. Capture PR 审查结果

HAR Capture 初稿能够解析结构，但会删除 Cookie/Authorization，并拒绝所有请求体，无法
重放大多数登录后的 POST 流程。合并前已经补充：

- 默认保留可重放认证信息，并提供 `strip_credentials`；
- JSON 和 `application/x-www-form-urlencoded`；
- 认证 JSON HAR fixture；
- Python 3.11/3.12 独立 CI；
- 插件测试 24 项通过后合并到 `main`。

随后分阶段 CLI 又增加 `workflow import --options` 集成测试，因此当前插件测试为 25 项。

## 8. 质量状态

当前开发分支已验证：

- 核心/靶场测试：27 passed；
- HAR Capture 插件测试：25 passed；
- Ruff：通过；
- 核心和 Capture mypy：通过；
- GitHub Actions：Linux 3.11/3.12、Windows 3.11/3.12、Docker lab、Capture 3.11/3.12
  全部通过；
- 人工八阶段实验：正常 0→50、攻击 0→100、Finding confirmed、PDF 生成成功。

## 9. 仍需继续开发

1. Capture 自动识别动态 ID、Token 传播、Extractor 和请求依赖；
2. 从浏览器/代理实时采集，而不只导入 HAR；
3. 将 generator/executor 从 coupon 标签扩展到提款、邀请码、Token、流程跳步；
4. Last-Byte Gate、HTTP/2 同步、自动最小并发数和成功率统计；
5. 更完整的 invariant 学习、人机确认和 HTML 时间线报告；
6. 4—6 个不同业务逻辑漏洞靶场和跨场景实验。

## 10. 一句话汇报结论

> 我们已经把最初的插件骨架推进成一个可运行的最小闭环：它能导入或描述正常流程、
> 重放正常状态、生成并选择竞态计划、真实发送并发请求、用业务状态确认漏洞并输出 PDF；
> 当前奶茶券是验证这一方法的第一个靶场，下一阶段重点是 Capture 动态依赖和更多场景的
> 通用化。

## 11. 相关文档

- [现场演示指南](DEMO_GUIDE_ZH.md)
- [CLI 参考](cli.md)
- [架构](architecture.md)
- [数据契约](contracts.md)
- [插件开发](plugin-development.md)
- [English progress report](PROGRESS_REPORT_EN.md)
