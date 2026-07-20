# 本地运行靶场，不需要 Docker

先安装项目开发依赖和插件：

```powershell
cd D:\StateBreaker\StateBreaker
.\.venv\Scripts\pip install -e .[dev] -e race-generator -e race-executor -e statebreaker-verifier-basic
```

启动某个靶场：

```powershell
.\.venv\Scripts\python.exe labs\run_local_lab.py payment-callback-idempotency
.\.venv\Scripts\python.exe labs\run_local_lab.py refund-vs-fulfill-race
.\.venv\Scripts\python.exe labs\run_local_lab.py bank-double-withdraw
.\.venv\Scripts\python.exe labs\run_local_lab.py payment-step-skip
.\.venv\Scripts\python.exe labs\run_local_lab.py payment-binding-mismatch
```

默认端口：

- coupon-race: 8080
- payment-step-skip: 8090
- bank-double-withdraw: 8091
- payment-callback-idempotency: 8092
- refund-vs-fulfill-race: 8093
- payment-binding-mismatch: 8094

也可以加 `--port 9000` 改端口。