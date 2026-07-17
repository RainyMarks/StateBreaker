# statebreaker-verifier-basic

最小 `statebreaker.verifier` 插件，`plugin_id = team.basic-verifier`。

根据 `RawAttackResult` 的攻击前/后状态与响应证据，对照 `Invariant[]` 输出正式 `Finding[]`：

| verdict | 含义 |
|---|---|
| `confirmed` | 状态证据可计算，且规则被破坏 |
| `rejected` | 状态证据可计算且规则成立，或证据不足且无启发式异常 |
| `probable` | 无法完整评估，但存在启发式异常（如多次 200 / plugin_data 标记） |

支持的 `kind`：`max-delta`、`min-value`、`count-limit`、`single-use`、`state-transition`。

## 安装

```powershell
python -m pip install -e .\statebreaker-verifier-basic
statebreaker plugins list --group statebreaker.verifier
```

## 使用

```powershell
statebreaker verify .\raw-attack-result.json .\examples\coupon-race\invariants.yaml `
  --plugin team.basic-verifier `
  --output .\findings.json
```
