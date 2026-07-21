# lab-race-inventory-oversell

教学靶场：库存超卖。`POST /products/{sku}/buy` 存在 check-then-act 竞态窗口：
接口先检查 `sold < stock`，随后在真正增加 `sold` 前故意 `await`。多个并发购买请求
可能同时通过旧库存检查，最终让 `sold` 超过 `stock`。

## 正常流程

1. 初始化库存：

   ```bash
   curl -X POST http://127.0.0.1:8000/products/widget/stock \
     -H "Content-Type: application/json" \
     -d '{"stock": 1}'
   ```

2. 顺序购买：

   ```bash
   curl -X POST http://127.0.0.1:8000/products/widget/buy
   ```

   库存为 1 时，第一笔会成功，第二笔顺序购买会返回 409 `sold_out`。

3. 查询商品：

   ```bash
   curl http://127.0.0.1:8000/products/widget
   ```

4. 重置测试状态：

   ```bash
   curl -X POST http://127.0.0.1:8000/__test__/reset
   ```

## 触发漏洞

启动本地服务：

```bash
cd labs/lab-race-inventory-oversell
uvicorn app:app --reload
```

向同一个 `sku` 并发发送多笔 `POST /products/{sku}/buy`。由于检查库存和增加销量之间
存在竞态窗口，库存为 1 时也可能出现多笔成功响应，随后查询可看到 `sold > stock`。

本靶场只使用进程内内存状态，适合本地离线测试；重启服务或调用
`POST /__test__/reset` 会清空数据。
