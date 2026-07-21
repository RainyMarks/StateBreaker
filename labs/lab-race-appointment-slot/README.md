# lab-race-appointment-slot

教学靶场：预约名额抢占。`POST /slots/{slot_id}/book` 存在 check-then-act
竞态窗口（检查 `booked < capacity` 与追加预约用户之间有 `await`），并发预约会
突破名额上限；顺序执行第二个预约返回 409。

- `POST /slots/{slot_id}` 创建预约名额（`capacity` 默认 1）；`GET /slots/{slot_id}` 查询预约状态
- `POST /slots/{slot_id}/book` 使用 `X-User-Id` 请求头预约名额
- `POST /__test__/reset` 清空状态
