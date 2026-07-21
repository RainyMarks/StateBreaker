# lab-oneshot-redemption

教学靶场：一次性权益码领取。`POST /perks/{code}/claim` 存在 check-then-act
竞态窗口（检查 `status == "fresh"` 与写入 `spent` 之间有 `await`），并发两次
领取都会成功，账户被加双倍 credit；顺序执行则第二次返回 409。

- `POST /perks/issue` 发码；`GET /perks/{code}`、`GET /accounts/{user}` 查状态
- `POST /__test__/reset` 清空状态
