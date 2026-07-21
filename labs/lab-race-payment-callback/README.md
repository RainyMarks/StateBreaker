# lab-race-payment-callback

教学靶场：支付回调重复入账。`POST /payments/{order_id}/callback` 存在
check-then-act 竞态窗口（检查订单仍是 `unpaid` 与写入 `paid`、给账户加
credit 之间有 `await`），并发重复回调会让同一订单给用户重复加 credit；
顺序执行第二次回调返回 409。

- `POST /orders/{order_id}` 创建待支付订单（`user` 默认 `alice`，`credit` 默认 100）
- `POST /payments/{order_id}/callback` 模拟支付平台回调
- `GET /orders/{order_id}` 查看订单状态与回调落账次数
- `GET /accounts/{user}` 查看用户累计 credit
- `POST /__test__/reset` 清空状态

最小复现：

1. 创建订单：`POST /orders/o-1`，请求体 `{"user": "alice", "credit": 100}`。
2. 顺序调用两次 `POST /payments/o-1/callback`，第一次成功，第二次返回 409，
   `GET /accounts/alice` 显示 `credit_total == 100`。
3. 重置后重新建单，并发调用两次 `POST /payments/o-1/callback`，两次都会成功，
   `GET /accounts/alice` 显示 `credit_total == 200`。
