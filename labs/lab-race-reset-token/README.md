# lab-race-reset-token

教学靶场：密码重置一次性 token。`POST /tokens/{token}/use` 存在
check-then-act 竞态窗口（检查 `status == "unused"` 与写入 `"used"` 之间有
`await`），并发两次使用同一个 token 都会成功，用户的 `reset_count` 和
`password_version` 会递增两次；顺序执行则第二次返回 409。

- `POST /tokens/issue` 签发 token，可传 `user` 和固定 `token`
- `POST /tokens/{token}/use` 使用 token，可传 `new_password`
- `GET /tokens/{token}`、`GET /users/{user}` 查询状态
- `POST /__test__/reset` 清空状态

## 本地运行

```bash
uvicorn app:app --reload --app-dir labs/lab-race-reset-token
```

也可以在该目录下运行：

```bash
cd labs/lab-race-reset-token
uvicorn app:app --reload
```

## 复现思路

1. 调用 `POST /tokens/issue` 签发一个 token。
2. 顺序调用两次 `POST /tokens/{token}/use`：第一次 200，第二次 409。
3. 重新签发 token 后并发调用两次 `POST /tokens/{token}/use`：两次都可能 200。
4. 调用 `GET /users/{user}`，可看到 `reset_count == 2` 和 `password_version == 2`。
