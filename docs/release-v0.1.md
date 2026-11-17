# v0.1 发布说明

范围与设计决定见 [issue #36](https://github.com/XUJiahua/enterprise_agent/issues/36)，实现细节见 [内核说明](kernel.md)。交付由 PR #37（M1a）、#38（M1b）、#39（M2）、#40（M3）组成，按顺序合并。

## 交付内容

| 部分 | 位置 | 说明 |
|---|---|---|
| 操作内核契约 | `kernel/` | Principal、版本化 Definition、Input/Outcome、错误分类、请求指纹；只依赖标准库 |
| PostgreSQL 执行 | `kernel/pg/` | `Runner.Run` 唯一受管写入口、`Read`、`Drain/Consume`、`Describe`；`schema.sql` + `inbox.sql` |
| 采购参考应用 | `internal/procurement/`、`cmd/product` | create@1、approve@1、receive@1、close@2，通知消费者（独立角色），HTTP API，golang-migrate migrations；本地运行见 [getting-started](getting-started.md#运行-v01-产品cmdproduct) |
| 开发工具 | `cmd/appctl` | `migrate up`、`describe`、`validate [--base]`、`verify --base`（含产品 migration 基线） |
| 界面 | `apps/web/app/procurement` | 创建、批准、验收登记、批量关闭（必填原因）、审计、通知 |
| 外部导入示例 | `examples/external` | 独立 module，非 owner 角色运行 |

## 在其他 Go 项目中使用

```bash
go get github.com/XUJiahua/enterprise_agent@<tag 或 commit>
```

1. 把 `pg.Schema`（需要消费者时再加 `pg.InboxSchema`）加入宿主的 migrations，按文件头注释给运行角色授权。运行角色不能是 superuser、BYPASSRLS 或表 owner。
2. 领域模块为每个操作版本实现 `pg.Operation`（`Definition / Normalize / Authorize / Execute`），为每个事件实现 `pg.Consumer`。
3. 启动时 `pg.New(ctx, pool, pg.Registry{...})`。定义无效、事件和消费者不一一对应、运行角色权限过大时启动失败。
4. 入口层从可信认证结果构造 `kernel.Principal`，调用 `Runner.Run`，用 `kernel.CodeOf` 映射传输层错误码。

已验证：在仓库外新建 module，用 `GOPROXY=direct go get` 获取已推送的提交（无 `replace`），构建后以非 owner 角色运行，第一次返回 `replayed=false`，第二次返回 `replayed=true`。

## 一次真实需求变更：关闭必须填写原因

需求：审计要求每次关闭都有原因。按版本规则，Normalize 的语义变化发布为新版本 `procurement.close@2`（原因必填，空白视为缺失），`close@1` 停止注册，旧客户端会收到 `Invalid: unknown operation`，已存的 close@1 账本行和事件保留，消费者照常处理 `procurement.purchase.closed@1` 事件。

这次变更只改了 `internal/procurement`、`apps/web`、测试和 smoke 脚本，`kernel/` 无改动（提交 `3babdf5`，`git diff --stat HEAD~1 -- kernel/` 为空）。`appctl describe` 重新生成 `contract.json`。如果把已发布的 `close@2` 原地改为 `MaxTargets: 50`，`appctl validate --base` 会报 `CONTRACT_CHANGED_WITHOUT_VERSION`。

开发者反馈：

- 改动集中在领域模块，Runner 与事务/幂等/审计代码不需要碰。
- `contract.json` 只能发现定义层面的变化（事件、目标上限、豁免）。“原因必填”属于 Normalize 行为，原地修改不会被 validate 发现，只能靠版本纪律、代码审查和测试。这是 v0.1 的已知上限。
- 退役旧版本会让老客户端立即失败；需要过渡期时，应同时注册两个版本。

## 验收证据

每个 PR 都附带了真实 PostgreSQL 17.6 的证据，CI `kernel` job 在出现 `--- SKIP` 时直接失败。

| 验收项（issue #36 §6） | 证据 |
|---|---|
| 外部 module 导入，不依赖 internal/实验/oracle | `TestKernelImportsNoInternalCode`；`examples/external`；上面的 `go get` 验证 |
| 未知操作、缺失身份、跨租户混合目标、字段越权按契约拒绝 | `TestRejectionsWriteNothing`、`TestApproveRefusalsChangeNothing`、`TestDatabaseFloor` |
| 幂等作用域与并发同 key | `TestRunWritesAndReplays`、`TestIdempotencyScope`、`TestConcurrentSameKeyExecutesOnce` |
| 零部分提交（目标不合格、写后失败、审计/outbox/账本失败） | `TestOutcomeContractAndFailuresRollBack`、`TestCloseOutboxFailureRollsBack` |
| 父单锁协议，barrier 控制双方先后 | `TestCloseBeforeReceiveRefusesReceipt`、`TestReceiveBeforeCloseCountsReceipt`，以及两项变异对照 |
| 排序规则与 ID 配对、锁顺序 | `TestApproveUnderICUCollation`；`lockPurchases` 统一 `COLLATE "C"` |
| 连接池不串身份；非 owner 角色；RLS/列授权正反控制 | `TestPooledConnectionDoesNotLeakIdentity`、`TestDatabaseFloor`、`TestNewRejectsPrivilegedRuntimeAndBadDefinitions` |
| 事件与注册消费者一致；崩溃恢复；重复消费不重复投影 | `TestRegistryMatchesEventsAndConsumers`、`TestDrainProjectsOnceAndRecovers`、`TestConcurrentDrainsProjectOnce`、`TestDrainRejectsVersionAndPayloadMismatch`；smoke 中消费者关闭→重启→再重启 |
| 产品 migrations 从空库初始化、重复运行、dirty 拒绝、历史篡改识别 | `TestMigrateIsRepeatableAndRefusesDirty`、`TestProductMigrationsAgainstBaseline`、smoke |
| 原 API/UI、历史回放、干净启动、一次真实扩展 | CI `runtime` ×3、`cohort-controls`、`verifier` 未改动且通过；干净浅克隆中构建、测试、`validate`、smoke 通过；close@2 变更 |
| 浏览器 E2E | `scripts/product_browser.sh`，5 项用例，含创建→批准→验收→关闭→通知的完整流程 |
| 管理员/应用/消费者角色分离 | `TestConsumerRoleSeparation`：应用角色 drain 被拒，消费者角色不能执行业务操作 |

## 已知限制

- 可信宿主模型：身份由宿主绑定，领域模块是经审查的同进程 Go 代码。`Session` 只防误用，不是沙箱。
- 直接写 SQL 不会获得操作级约束（父单锁协议、完整目标检查、审计）；数据库只兜底租户 RLS 与列授权。
- `contract.json` 只覆盖定义层面；Normalize/Execute 的行为变化需要人工保证版本纪律。
- migration 内容不可变只在可信 Git 基线下检查（`appctl verify --base`）；不发现线上手工 DDL。
- `kernel.pending_tenants` 的 owner 必须是 superuser 或 BYPASSRLS 的迁移角色。
- 单消费者进程、轮询间隔 1 秒；没有租约、Worker 池或外部投递。失败事件不阻塞其后的事件（按 id 分页），但会在每次轮询时重试并报错，没有死信表。
- 跨表不变量依赖采购模块的父单锁协议（Read Committed）。新增影响金额、状态或验收的写路径必须遵守它。
- 采购示例使用固定 demo 身份。原 `/` 页面仍是三条 POC 路线的共享界面、走旧 API，未迁移；产品的完整流程在 `/procurement`。旧 `internal/opkernel` 保留用于历史回放。
- golang-migrate 用 v4.19.1（v4.20.1 会连带升级 pgx 与 Go 版本，见 kernel.md）。
- 第二业务域、多合法事件、多轮维护成本属于后续研究，不在 v0.1。
