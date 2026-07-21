# 术语表 / Concepts

## CapturedTrace

一次正常流程的 HTTP 记录。它是 StateBreaker 的输入，不应该手写攻击计划或 finding。

## WorkflowGraph

从 normal flow 学到的动作图。它记录动作顺序、请求之间的变量绑定、可回读状态的 probes，以及哪些动作看起来更值得测试。

## Probe

用于读取状态的普通 HTTP 请求。扫描会用 probe 对比正常执行和并发执行后的状态差异。

## Baseline

正常顺序执行下的行为基线。它告诉 oracle 什么变化是“正常会发生的”，并保留支持它的 trial id。

## Trial

一次真实执行记录。control trial 表示正常顺序执行，attack trial 表示受控并发执行。每个 confirmed finding 都必须能追溯到 trial evidence。

## Oracle

比较 control 和 attack 的组件。它根据 trial、baseline 和状态差异输出 verdict，而不是根据业务名称或状态码硬猜。

## Finding

最终结论。常见 verdict 包括 `confirmed`、`suspected` 或其他非确认状态；只有 `CONFIRMED` 才能用于 PoC 和正式报告。
