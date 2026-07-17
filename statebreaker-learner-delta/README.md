# statebreaker-learner-delta

`statebreaker.learner` 插件，`plugin_id = team.delta-learner`。多轮重放正常
Workflow，比较 `state_probe_steps` 的前后状态，提出候选 `Invariant`。

## 设计概述

```
collect_normal_samples()   复用同一个 ExecutionRuntime 重放整条 Workflow N 次，
                            单轮请求失败/探针非 JSON 时整轮丢弃，不污染统计
        │
        ▼
build_state_profile()      把每轮探针响应体拍平成 "$.field" 路径，
                            按“同一探针内跨样本”的表现分为 stable_fields / ignored_fields
                            （UUID / ISO 时间戳 / 高熵十六进制串 / 每轮都不同的字符串 → 忽略）
        │
        ▼
DEFAULT_PROPOSERS          对每个 stable 字段依次尝试：
                              MaxDeltaProposer      数值增量有界（如优惠金额单次 +50）
                              MinValueProposer      数值下界为 0（如从未见过负数）
                              StateTransitionProposer 低基数字段稳定的 before→after 转换
```

每条候选规则的 `parameters` 里都带 `confidence`（基于跨样本一致率）和
`sample_count`，`description` 自动生成成可读句子。**这是候选项，不是结论**——
不做假设、不判断规则是否正确，交给人工确认或下游 `statebreaker.verifier` 插件。

## 有意为之的空缺

`single-use` / `rate-limit` / `uniqueness` / `ownership` 没有实现：在单轮
before/after 探针对比里没有可观察信号，除非在基线轮次里重复调用同一动作
（这已经越界成主动探测而不是学习正常流程），或者 runtime 暴露会话身份变量
（当前没有）。新增一种候选规则类型只需要实现 `proposers.InvariantProposer`
协议并加入 `DEFAULT_PROPOSERS`，采样和画像阶段不需要改动。

## 安装与运行

```powershell
python -m pip install -e .\statebreaker-learner-delta
statebreaker plugins list
statebreaker learn .\examples\coupon-race\workflow.yaml `
  --plugin team.delta-learner --output learning-result.json
```

采样轮数默认 10（上限 100），可通过环境变量覆盖（entry point 加载时插件类是零参数构造的，
无法从 CLI 传参；非法值会以 `PluginError` 失败）：

```powershell
$env:STATEBREAKER_LEARNER_SAMPLES = "20"
```

`max-delta` 候选的 `parameters.bound_source` 为 `observed_maximum`：这是基线采样中的
**观测上界**，不是已证明的业务天花板。

## 测试

```powershell
python -m pip install -e ".[dev]"
pytest .\statebreaker-learner-delta\tests -q
```

`tests/test_profiling.py`、`tests/test_proposers.py` 是纯函数单测，不连网络。
`tests/test_plugin.py` 用 `httpx.MockTransport` 模拟老王奶茶券靶场的
`/api/runs`、`/state`、`/redeem` 接口，端到端跑通 `learn()`。
