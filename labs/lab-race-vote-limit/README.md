# lab-race-vote-limit

教学靶场：每个用户在同一投票中只能投一次。`POST /polls/{poll_id}/vote`
存在 check-then-act 竞态窗口（检查 `x-user-id` 是否已投与记录投票之间有
`await`），同一用户并发投票会被计入多票；顺序执行则第二次返回 409。

- `POST /polls/{poll_id}` 创建投票（`choices` 默认 `["yes", "no"]`）
- `POST /polls/{poll_id}/vote` 投票（请求头 `x-user-id` 标识用户）
- `GET /polls/{poll_id}` 查询投票状态和计票结果
- `POST /__test__/reset` 清空状态
