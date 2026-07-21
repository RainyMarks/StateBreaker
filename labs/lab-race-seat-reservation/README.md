# lab-race-seat-reservation

教学靶场：同一座位重复预订。`POST /shows/{show_id}/seats/{seat}/reserve`
存在 check-then-act 竞态窗口（检查座位 `available` 与写入 `holder` 之间有
`await`），并发预订同一座位会让多个请求都成功，并产生多条 reservation 记录；
顺序执行第二个预订返回 409。

- `POST /shows/{show_id}/seats` 初始化演出座位（请求体示例：`{"seats": ["A1"]}`）
- `POST /shows/{show_id}/seats/{seat}/reserve` 预订座位，可用 `X-User-Id` 指定用户
- `GET /shows/{show_id}/seats/{seat}` 查看座位状态、持有人和预订记录数量
- `POST /__test__/reset` 清空状态
