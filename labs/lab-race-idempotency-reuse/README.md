# lab-race-idempotency-reuse

教学靶场：幂等键复用创建订单。`POST /orders` 读取 `Idempotency-Key`
header，先检查 key 是否未使用，再经过一个 `await` 才记录 key 并创建订单。
并发请求使用同一个 key 时会同时通过检查，导致同一个幂等键创建多个订单；
顺序执行第二次会返回 409。

- `POST /orders` 创建订单，请求体可传 `sku`、`quantity`，必须带
  `Idempotency-Key` header
- `GET /orders` 查看已创建订单
- `GET /idempotency/{key}` 查看幂等键记录及关联订单
- `POST /__test__/reset` 清空状态
