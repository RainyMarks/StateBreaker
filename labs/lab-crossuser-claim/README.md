# lab-crossuser-claim

教学靶场：邀请单跨用户领取。`POST /invites/{slug}/accept` 存在 check-then-act
竞态窗口（检查 `state == "open"` 与写入 `"taken"` 之间有 `await`），两个不同
用户并发领取同一张邀请都会成功、双方账户都被加分；顺序执行第二人返回 409。

- `POST /invites/mint` 创建邀请；`GET /invites/{slug}` 查邀请状态
- `GET /members/{user}` 查成员积分（按身份观察）
- `POST /__test__/reset` 清空状态
