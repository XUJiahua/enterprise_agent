# 采购审批业务约定 v1

这个约定先于三条实现，测试中的期望值独立手写。金额使用正整数（POC 不涉及币种或小数）；状态为 submitted / approved。

| 身份 / Token 后缀 | tenant | role | 业务约束 |
|---|---|---|---|
| alice | tenant-a | requester | 创建并读取本租户采购单；不能审批 |
| bob | tenant-a | approver | 可审批他人提交的单；不能审批自己提交的单 |
| carol | tenant-a | approver | 可审批 alice / bob 的 submitted 单 |
| dave | tenant-b | requester | 只能访问 tenant-b |
| erin | tenant-b | approver | 只能审批 tenant-b 的他人单 |

完整 Token 为 `demo_<name>`，仅测试身份。`X-Tenant-ID` / `X-Role` 等客户端 Header 不影响身份。

| API | 正常行为 |
|---|---|
| GET /purchases | 返回本租户采购单，禁止泄露 internalNote |
| GET /purchases/{id} | 返回本租户单；跨租户与不存在均不返回记录 |
| POST /purchases | requester 创建，输入仅 id/title/amount；服务端绑定租户/创建者，初始 submitted |
| POST /purchases/{id}/approve | 本租户 approver 审批他人 submitted 单，原子转换为 approved |
| PATCH /purchases/{id} | 不提供任意更新；不能借此绕过审批 |

审批重复请求不得再次成功。8 个并发请求恰有一个成功。拒绝审批后，原状态必须保持。权限拒绝允许 403/404；输入拒绝允许 400。测试不要求三条路径错误消息一致。

`internalNote` 代表银行账户等内部字段，两个业务角色均不能读取。字段泄露故障下，数据库拒绝整个查询（403）与 ORM 将字段置 null 都满足保密不变量；健康版本的普通列表与详情必须可用。

五种人工注入仅在启动时显式设置 `POC_ALLOW_MUTATIONS=1` 和 `POC_MUTATION` 后生效：

| 注入 | 修改的执行行为 | 靶向验收 |
|---|---|---|
| omit-tenant | 去掉普通查询的租户过滤 | 列表/详情不能跨租户 |
| skip-approval-check | 去掉 Handler 角色/自审批检查 | requester 及创建者均不能审批 |
| leak-field | 将敏感列加入正常查询投影 | 响应中没有敏感值 |
| mass-assignment | 接受客户端指定 tenant/creator/status | 伪造创建被拒绝且无记录落库 |
| bypass-action | 新增通用 PATCH，直接写 approved | 无授权状态转换被拒绝，状态不变 |

ZenStack 附加 `disable-policy` 故障，启用未安装 PolicyPlugin 的原始 Client 并移除普通租户过滤，预期发生越权。它是边界反证，不计入五项对照得分。
