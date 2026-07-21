# lab-quota-oversell

教学靶场：限量席位抢购。`POST /drops/{sku}/buy` 存在 check-then-act 竞态窗口
（检查 `sold < seats` 与计数加一之间有 `await`），并发购买会突破席位上限；
顺序执行第二个买家返回 409。

- `POST /drops/open` 开仓（`seats` 默认 1）；`GET /drops/{sku}` 查销量
- `POST /__test__/reset` 清空状态
