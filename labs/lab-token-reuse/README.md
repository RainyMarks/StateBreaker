# lab-token-reuse

教学靶场：一次性恢复票据。`POST /recoveries/{ticket}/finish` 存在
check-then-act 竞态窗口（检查 `state == "armed"` 与写入 `"consumed"` 之间有
`await`），并发两次提交都会生效、`applied` 计数变为 2；顺序执行第二次返回 409。

- `POST /recoveries/begin` 签发票据；`GET /recoveries/{ticket}` 查票据状态
- `POST /__test__/reset` 清空状态
