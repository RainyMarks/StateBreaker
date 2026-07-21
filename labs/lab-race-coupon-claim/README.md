# lab-race-coupon-claim

本地离线教学靶场：优惠券一次性领取重复成功。`POST /coupons/{code}/claim`
先检查 `status == "fresh"`，随后 `await`，最后才写入 `claimed`；并发请求会同时
通过 freshness 检查，导致同一张券被同一用户或多个用户重复领取并重复入账。

- `POST /coupons/issue` 发券，可传 `{"code": "FIXED1", "amount": 30}`
- `POST /coupons/{code}/claim` 领取，使用 `X-User-Id` 标识用户
- `GET /coupons/{code}` 查看券状态、最终持有人和领取次数
- `GET /users/{user}` 查看用户累计领取金额、次数和券码
- `POST /__test__/reset` 清空内存状态，便于离线测试重复运行

顺序执行时，第二次领取会返回 409；并发执行时，多次领取可能都返回 200，这是本靶场
故意保留的竞态漏洞。
