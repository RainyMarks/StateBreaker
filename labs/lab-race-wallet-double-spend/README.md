# lab-race-wallet-double-spend

本地离线教学靶场：钱包余额双花。应用只使用进程内内存状态，不连接外部服务。

## 接口

- `POST /accounts/{user}/deposit`：给用户钱包充值，JSON body 为 `{"amount": 100}`。
- `POST /accounts/{user}/withdraw`：从用户钱包提现，JSON body 为 `{"amount": 60}`。
- `GET /accounts/{user}`：查看用户钱包余额、累计充值和累计提现。
- `POST /__test__/reset`：清空内存状态，方便自动化测试重复运行。

## 正常流程

1. `POST /accounts/alice/deposit`，body 为 `{"amount": 100}`，余额变为 `100`。
2. 顺序执行 `POST /accounts/alice/withdraw`，body 为 `{"amount": 60}`，余额变为 `40`。
3. 再顺序执行同样的提现请求会返回 `422 insufficient_funds`，因为余额不足。
4. `GET /accounts/alice` 会看到余额仍为 `40`，累计提现为 `60`。

## 漏洞说明

`POST /accounts/{user}/withdraw` 在检查 `amount <= balance` 后，和真正扣减余额之间有一个
`await`。两个并发提现请求可能同时读到旧余额并都通过检查，随后各自扣减余额。

例如初始充值 `100` 后，并发发起两笔 `60` 的提现：

- 两笔请求都可能返回成功。
- 最终余额可能变为 `-20`。
- `total_withdrawn` 可能变为 `120`，超过 `total_deposited`。

这就是有意保留的 check-then-act 竞态，用于本地 race condition 发现工具的离线验证。

## 本地运行

```bash
uvicorn app:app --app-dir labs/lab-race-wallet-double-spend --reload
```

也可以在测试中直接导入 `create_app()`，用 ASGI client 本地调用，无需启动网络服务。
