# lab-race-quota-bypass

教学靶场：用户每日配额绕过。`POST /quotas/{user}/use` 存在 check-then-act
竞态窗口（检查 `used < limit` 与 `used += 1` 之间有 `await`），同一用户的多次
并发使用会突破每日上限；顺序执行时超额请求返回 409。

- `POST /quotas/{user}` 设置用户每日配额（请求体 `{"limit": 1}`，同时将 `used` 归零）
- `POST /quotas/{user}/use` 消耗一次配额
- `GET /quotas/{user}` 查询当前配额状态
- `POST /__test__/reset` 清空状态
