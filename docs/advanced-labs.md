# 高级本地靶场 / Advanced Local Labs

本页给新手和汇报场景使用：这些靶场用于证明 StateBreaker 可以只靠公开 HTTP normal flow 学习流程、生成候选、执行受控并发实验，并用真实 trial evidence 输出 finding。

## 黑盒边界 / Black-Box Boundary

- 录制入口只知道一段公开 HTTP 正常流程：创建或准备资源、读取状态、执行一次变更动作、再次读取状态。
- 扫描器通过 HTTP/ASGI transport 发送请求；ASGI 只是测试内的传输优化，不等于读取目标内部变量。
- reset endpoint 是测试隔离接口，用于把每轮实验恢复到干净状态；它不参与依赖学习和 verdict 生成。
- `CONFIRMED` 必须能追溯到真实 `ExecutionTrial` 记录，不能只靠状态码或靶场名称下结论。

## 20 个高级靶场 / 20 Advanced Labs

- `lab-advanced-cart-bundle`：组合资源的一次性变更，覆盖嵌套 JSON body 和后置状态 probe。
- `lab-advanced-approval-chain`：审批链式状态，覆盖 role/body 参数和同一动作并发。
- `lab-advanced-graph-edge`：图边连接，覆盖两个路径内资源值和 body 值组合。
- `lab-advanced-tree-quota`：树形配额，覆盖深路径资源和数量型状态变化。
- `lab-advanced-ledger-transfer`：账本转移，覆盖数值守恒类 side effect。
- `lab-advanced-reservation-hold`：保留/占位，覆盖时间槽参数和重复 hold。
- `lab-advanced-onboarding-workflow`：步骤流转，覆盖状态机式 normal flow。
- `lab-advanced-header-body-quota`：header、query、body 同时参与依赖，覆盖多位置变量绑定。
- `lab-advanced-shortcode-redeem`：短码一次性动作，覆盖路径 token 和重复提交。
- `lab-advanced-composite-lock`：组合锁，覆盖 query 与 body 共同选择资源。
- `lab-advanced-dedup-batch`：批量去重，覆盖 idempotency header。
- `lab-advanced-state-machine`：状态机转换，覆盖前后状态 probe 的异常比较。
- `lab-advanced-window-limit`：窗口限额，覆盖 query window 和次数上限。
- `lab-advanced-batch-settlement`：批处理结算，覆盖 batch id 与状态关闭。
- `lab-advanced-sharded-stock`：分片库存，覆盖 query shard 和数量型扣减。
- `lab-advanced-cas-profile`：版本号更新，覆盖 compare-and-set 形态。
- `lab-advanced-linked-resource`：关联资源创建，覆盖父子资源和配额变化。
- `lab-advanced-waitlist-queue`：队列提升，覆盖候选身份和队列状态。
- `lab-advanced-set-membership`：集合成员变更，覆盖 membership side effect。
- `lab-advanced-priority-claim`：优先级领取，覆盖 query priority 和一次性 claim。

## 默认 4 个代表扫描样本 / Default Representative Scan Set

默认测试不会每次跑完整 20 个扫描，因为完整集更慢。`DEFAULT_SCAN_LABS` 选择 4 个代表样本：

- `lab-advanced-cart-bundle`：基础路径资源 + JSON body + 状态 probe。
- `lab-advanced-ledger-transfer`：数值守恒和重复 side effect。
- `lab-advanced-header-body-quota`：header、query、body 多位置依赖。
- `lab-advanced-cas-profile`：版本号/条件更新形态。

这 4 个覆盖了新手最该先理解的公开 API 形态：路径变量、请求体变量、header/query 变量、数值状态、条件更新和后置状态读取。

## 常用命令 / Commands

```bash
# 默认代表集：录制 20 个 normal flow，并扫描 4 个代表靶场
pytest tests/orchestration/test_advanced_blackbox_labs.py -q

# 完整 20/20 高级扫描
set STATEBREAKER_ADVANCED_LAB_FULL_SCAN=1
pytest tests/orchestration/test_advanced_blackbox_labs.py -q
```

PowerShell 临时环境变量写法：

```powershell
$env:STATEBREAKER_ADVANCED_LAB_FULL_SCAN="1"
pytest tests/orchestration/test_advanced_blackbox_labs.py -q
```
