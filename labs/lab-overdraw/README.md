# lab-overdraw

教学靶场：储值账户扣款。`POST /wallets/{id}/debit` 存在 check-then-act 竞态
窗口（检查 `amount <= balance` 与扣减之间有 `await`），并发两笔扣款都会成功，
余额变负；顺序执行第二笔返回 422。

- `POST /wallets/open` 开户；`GET /wallets/{id}` 查余额
- `POST /__test__/reset` 清空状态
