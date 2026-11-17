# v0.1 操作内核（M1a）

设计与里程碑见 [issue #36](https://github.com/XUJiahua/enterprise_agent/issues/36)。本文只记录已实现的部分。

## 包与依赖方向

| 包 | 内容 | 依赖 |
|---|---|---|
| `kernel` | `Principal`、`Ref`、`Definition`、`Input`、`Outcome`、错误分类、请求指纹 | 仅标准库 |
| `kernel/pg` | `Runner`（唯一受管写入口）、`Session`、`Schema`（kernel.request/audit/outbox）、`CheckRuntime` | `kernel`、pgx v5 |
| `kernel/pg/pgtest` | 测试用一次性数据库 | pgx v5 |
| `internal/procurement` | `procurement.approve@1`、产品 migrations | `kernel`、`kernel/pg` |
| `examples/external` | 仓库外 module 导入内核的最小示例 | `kernel`、`kernel/pg` |

`kernel/...` 不 import 本仓库任何其他包，`TestKernelImportsNoInternalCode` 检查这一点。

## Runner.Run 执行顺序

1. 查找已注册的 `Ref`；未注册返回 `Invalid`。
2. 无租户或 actor 返回 `Denied`；校验幂等 key 与请求大小。
3. 领域 `Normalize`：严格解析，产生目标与规范化 payload；`kernel.Targets` 排序并拒绝空、超限、重复。
4. 领域 `Authorize`：操作级权限。重放同样要先通过这一步。
5. 开事务，用 `set_config(..., true)` 绑定 `app.tenant/app.actor/app.roles`（事务内有效，连接归还后不残留）。
6. 以 operation+version+tenant+actor+key 取 advisory xact lock，查 `kernel.request`：同指纹返回原结果字节（`Replayed`），不同指纹返回 `Conflict`。
7. 领域 `Execute`：通过 `Session` 锁定、检查、修改，返回 `Outcome`。
8. 校验 `Outcome`：实际目标集合按 ID 等于请求集合；每目标一条审计对象；事件必须在 `Definition.Events` 中、落在请求目标上，且声明了事件时每个目标至少一条。
9. 写 `kernel.request`、`kernel.audit`、`kernel.outbox`，commit。commit 失败返回 `Retryable`（结果未知，用同 key 重试可得知）。

第 7 步之后任何一步失败，业务修改与 kernel 行全部回滚。指纹覆盖 operation、version、完整 payload（含 reason）与排序后的目标。

## 数据库兜底

- kernel 与 procurement 表都 `FORCE ROW LEVEL SECURITY`，租户条件是 `RESTRICTIVE` policy，后加的 permissive policy 无法放宽。
- runtime 角色只有 `SELECT, INSERT`（kernel 表）和列级 `SELECT`/`UPDATE (status)`（采购表）；`internal_note` 不可读。
- `pg.New` 和 `pg.CheckRuntime` 拒绝 superuser、BYPASSRLS、表 owner 以及未强制 RLS 的表。
- `Session` 不暴露 pool、Begin、Commit、Rollback。这防止误用，不是对同进程 Go 代码的沙箱；直接写 SQL 的代码不会自动获得操作级约束，只受上面的数据库兜底约束。

## 运行测试

```bash
docker run -d --name ea-pg17 -p 127.0.0.1:55417:5432 -e POSTGRES_PASSWORD=postgres postgres:17.6
export KERNEL_TEST_ADMIN_URL='postgres://postgres:postgres@127.0.0.1:55417/postgres?sslmode=disable'
KERNEL_REQUIRE_DB=1 go test -race -count=1 ./kernel/... ./internal/procurement/
```

每个测试新建并删除自己的数据库，并创建集群级角色 `kernel_test_runtime`、`enterprise_runtime`。只对可销毁的服务器使用。未设置变量时 DB 测试跳过；`KERNEL_REQUIRE_DB=1`（CI `kernel` job）下跳过视为失败。

## M1a 未包含

- `procurement.approve@1` 暂用 `NoEventReason`：同库通知消费者（M2）落地时再声明版本化事件，并在注册时校验消费者。
- migrations 采用 golang-migrate 的文件命名，测试按序直接执行；golang-migrate v4 运行器、dirty 状态与 checksum 基线检查在 M1b 加入。
- HTTP 入口、验收记录、批量关闭与父单锁协议属于 M1b；旧 `internal/opkernel`、`cmd/server` 未改动。
