# v6b：批量改派审批人（非状态字段 + 参数校验）

状态（2026-11-15）：**只有 harness 和人工/假 CLI 控制，真实模型样本为 0。** 入口 `benchmark/phase6b/ctl.py`。`benchmark/phase3`、`phase4`、`phase5`、`phase5b`、`tests/acceptance.py`、`.github/workflows/` 和已有证据均未修改。

## 目的与任务选择

这是预注册确认性实验的一个新任务：沿用 v5b 的设计（两组、分散约定、必填 Event + NoEvent、去冲突线索、显式/隐含分开计分），换一个形状不同的业务操作，检验 v5/v5b 的发现能否复现。

新任务：`POST /purchases/bulk-reassign`，approver 把一批 submitted 采购单改派给本租户另一位 active approver，请求体 `{"ids":[...],"assignee":"USER"}`，响应 `{"reassignedIds":[...],"assignee":USER}`。选它而不是“追加备注/成本中心”，因为它同时满足四个形状要求，又最接近企业里的真实操作：

- **带参数**：assignee 是请求的一部分，决定幂等冲突（同 key 不同 assignee 必须 409），也进入对账指纹；
- **改非状态列**：只改 `Purchase.assigneeId`，status 不变，现有生命周期不受影响；
- **目标合法性校验**：assignee 必须是 `TenantMember` 中本租户、role=approver、active 的成员；
- **没有可整段照抄的既有函数**：既有 approve/reject 是无参状态迁移，reopen 是单目标 requester 操作。

与 v6a（按金额分级审批，仍是状态迁移）的区别：v6b 不改 status，核心难点是参数校验、旧值记录和参数绑定的指纹。

起点是 v5b 的完成态：approve、reject、reopen 三个 Action 都已存在（manual 组手写，reopen 取自 v5 的人工参考实现；action 组在 `operations.go` 中声明了 `reopenOp`）。v6b schema（`benchmark/phase6b/schema.sql`）新增：

- `TenantMember` 表，只读，开启 RLS，他租户不可见；
- `Purchase.assigneeId` 列，带外键 `(tenantId, assigneeId) → TenantMember`，并授予列权限；
- `AssignmentHistory` 表，只追加，开启 RLS；
- ActionRequest 允许 `bulk-reassign.v6`；
- 替换 v5 的 `purchase_transition` 策略，让 approver 可以在保持 submitted 的同时更新这一行。Purchase 仍只有三条策略。

`PurchaseEvent.type` 和 `AssignmentHistory` 的内容列刻意不加 CHECK，这样违反约定只会体现为隐含失败。

## 分组

| 组 | 既有 Action | 新操作的工作 |
|---|---|---|
| go-manual | `approval.go`、`reject.go`、`reopen.go` 手写 | 手写批量事务，约定需要从多个文件推断 |
| go-action | 受保护内核 `internal/action/action.go`，新增 `Update` 类型和 `Executor.Set`；既有声明在可编辑的 `internal/candidate/operations.go` | 在候选包中声明一个 `action.Update`，并通过 `protocol.Assign` 注册 |

内核的 `Update{Name,Role,Own,Status,Column,Field,Event}` 中，Name、Role、Status、Field 必填，Column 必须是内核登记的 managed 列（目前只有 `assigneeId`），否则 Set 直接报错。**Event 必填**，不发事件要显式写 `action.NoEvent`，留空时每次 Set 都返回错误（503）。内核里不声明任何 Operation/Update 值（单测用正则检查）；扩展点和示例都在候选包里。两组共享并逐字节相同的文件：`internal/app`、`cmd/server`、`internal/protocol`（新增 `Assign` 适配器）、`internal/ledger`、`internal/notify`、`SCHEMA.sql`、`TASK.md`、`.gitignore`（含 `/server`）。两组都只能改 `internal/candidate/**.go`。

## 四条隐含约定与线索分布

`cohort.json` 的 `conventions` 为每条约定记录 `(文件, 原文片段)`，并带 `action_enforcement` 字段（`kernel` 或 `declared`）。`test_phase6b.py` 检查以下内容：

- 每个片段确实存在于该组的导出文件中；
- 每条约定在每组至少涉及 2 个文件，且至少 1 个文件在 `internal/candidate` 之外；
- 每条约定都带 `action_enforcement`，其中 kernel 至少 2 条、declared 至少 1 条；
- 候选包中不出现 `assign` 字样；
- `commented=false` 的约定，其提示词不出现在任何导出文件的注释里；
- 任务文本和提示词不含 `forbidden_in_task` 中的词。

| 约定 | 内容 | action_enforcement | 注释 | manual 线索 | action 线索 |
|---|---|---|---|---|---|
| implicit-history | 每个目标在同一事务写 1 条 AssignmentHistory：fromAssignee 为改派前的值（无则 null）、toAssignee、changedBy、operation、key；并发改派形成按 createdAt 排序的一条链；写入失败时整体回滚 | **kernel** | **无** | SCHEMA.sql 的表和 `fromAssignee` 列；reconcile.go 的 `AssigneeDrift`（只有代码，按 createdAt 取最新一条）；reconcile.go 注明 ApprovalAudit 只记录状态迁移 | 内核 INSERT；reconcile.go；SCHEMA.sql |
| implicit-request-hash | `requestHash = ledger.Fingerprint("bulk-reassign.v6"+"\n"+assignee, ids)` | **kernel** | 有 | approval.go / reopen.go 对 Fingerprint 的调用；ledger.go 写明参数绑定进 domain、reopen 的 reason 不进摘要，Verify 按此重算 | 内核调用；ledger.go |
| implicit-outbox | 每个目标在同一事务写 1 条 PurchaseEvent，type 为 `purchase.reassigned`；写入失败时整体回滚 | **declared** | 有 | reject.go 的 INSERT；reopen.go 的 `'purchase.reopened'`；notify.go 的 `case "purchase.reassigned":` 和“审批早于 outbox”的说明；SCHEMA.sql | operations.go 的 `Event: "purchase.reopened"` 和 `Event: action.NoEvent`；内核的 “Event is required”；notify.go |
| implicit-uniform-denial | 未知用户、他租户 approver、requester、inactive approver 作为 assignee 时，返回同一个 4xx | **kernel** | 有 | approval.go / reject.go 的 “Actions never reveal existence”；reopen.go 单目标统一 409 的代码；SCHEMA.sql 的 `tenant_member_read` | 内核 “An unacceptable value is refused like an ineligible target” 和统一 409 代码；SCHEMA.sql |

### 消除线索冲突（v5 的教训）

v6b 中有三处新旧写法并存，都在代码里写明了以哪一种为准：

1. **审计表冲突**：既有 Action 都写 ApprovalAudit，新操作应该写 AssignmentHistory。reconcile.go 的 Drift 注释补了一句：“ApprovalAudit records status transitions only: an Action that keeps the status writes no ApprovalAudit row”。schema 中 ApprovalAudit 的 operation CHECK 也不含 `bulk-reassign.v6`，与这句注释一致。这句注释没有提到 history，所以 history 约定仍算“无注释”；但它确实指出了“这里不是 ApprovalAudit”，属于间接提示，解读时需要注意。
2. **指纹冲突**：v5 的 reopen 不把 reason 放进摘要，v6b 却要把 assignee 放进摘要。ledger.go 的 `subject` 注释同时写明了两条规则：参数绑定进 domain；reason 不存进 response，也不进摘要。统一的说法是：指纹覆盖的就是存储下来的 response 所描述的内容。
3. **拒绝语义**：legacy 单条接口对缺失返回 404。v5b 已在批量 Action 中注明“不同于 legacy，统一 409”，v6b 沿用这段注释，没有引入新的 legacy 路径。

## 计分

每次验收 **190 项 = 显式 178 + 隐含 12**。

- 显式（新操作 33 项）：功能 4、授权 3、目标资格 5（缺失、他租户、approved、rejected 各自与合格目标混在同一批，另有单独的 approved）、assignee 合法性 4、输入 7、幂等 4（含同 key 不同 assignee 返回 409）、并发 2、ActionRequest 故障回滚 1、生命周期 3（改派后可审批、可驳回，驳回后可 reopen 并再次改派）。
- 回归 145 项：冻结的 v5 Oracle 原样复用，包括 reopen 37 项（v5 的显式 25 项加隐含 12 项，在 v6b 中都属于既有行为）以及 v4 的驳回 42、审批 50、原有 API 16。复用时经过 `V5View` 去掉 `assigneeId` 列和 `history` 快照，Oracle 本身不变。
- 隐含：history 4、request-hash 2、outbox 3、uniform-denial 3。
- 显式检查只看 HTTP、`Purchase` 行（目标的 assigneeId 变化，其余全部不变），以及 ActionRequest 的作用域行和 response。拒绝类检查接受任意 4xx，并要求数据库全部不变。每个隐含用例只依赖一条约定；history 和 outbox 的故障用例中，不写对应行的候选会在注入故障时返回 200，因此只有该隐含组失败。
- 结果字段、summarize、未测量时 rate=null、候选崩溃判为 candidate-error、退出码 0/2/3/1，都与 v5 相同。phase6b 是 phase5 ctl 的包装，plan/result 格式沿用 version 5，以 `task` 字段区分。plan 冻结 `benchmark/phase6b/` 下除 controls/driver 以外的全部文件，以及实际执行的 phase5 ctl/Oracle、phase4/phase3 Oracle、worker 和 schema（有单测）。

## 控制结果（真实 PostgreSQL 17-alpine，`docs/evidence/phase6b-controls-2026-11-15/`）

| 组 | 控制 | 显式 | 隐含 | 未遵守的约定 |
|---|---|---|---|---|
| manual | correct（手写参考） | 178/178 | 12/12 | — |
| manual | omit-history / param-hash / omit-event / distinguish | 各 178/178 | 8、10、9、10 /12 | 各自只有目标约定 |
| manual | no-replay（显式负控制） | 176/178 | 12/12 | — |
| manual | unchanged（显式负控制） | 155/178 | 3/12 | history、hash、outbox |
| action | correct（内核 + 候选包内 `reassignOp`） | 178/178 | 12/12 | — |
| action | no-event（声明 `Event: action.NoEvent`） | 178/178 | 9/12 | outbox |
| action | omit-history / param-hash / distinguish（绕过内核手写） | 各 178/178 | 8、10、10 /12 | 各自只有目标约定 |
| action | omit-event（Event 留空，显式负控制） | 155/178 | 0/12 | 全部（每次 Set 都 503） |
| action | own-true（显式负控制） | 164/178 | 3/12 | history、hash、outbox |
| action | unchanged（显式负控制） | 155/178 | 3/12 | history、hash、outbox |

- 两份参考均为 190/190。
- 8 个隐含负控制：显式全过，且未遵守的约定集合**恰好**等于目标约定（driver 断言集合相等）。
- 5 个显式负控制都被识别。
- 全部 15 次控制的四组回归（37/42/50/16）均全过。
- 崩溃控制：改派处理函数 panic，同时目录里有游离的 `server` 文件，结果判为 `candidate-error`，`server` 未被判越界。
- 交接控制：两组各一次 `prepare → phase3 generation_worker.py（作为 kit 中的 worker.py）+ 假 kiro-cli → restore → evaluate`，均为 190/190，receipt 中 engine=kiro、usage=credits 0.25。
- 部分分母（rate=null）和重复结果拒绝均已检查。
- `python3 -m unittest discover -s benchmark/tests`：69 项通过，其中 phase6b 新增 14 项。
- driver 全程约 170 s。

这些都是人工或假 CLI 控制，不计入模型样本。

两份参考实现的候选包源码保存在证据目录 `references/*.go.txt`。

控制中得到的设计事实：

- **uniform-denial 部分由 RLS 兜底**。他租户的 approver 在 `TenantMember` RLS 下不可见，所以自然就和未知用户一样。distinguish 控制只在 requester 和 inactive 两项上失败（1/3）。这条约定真正有区分力的只有这两种情形。
- **uniform-denial 在路由未实现时会“空过”**：unchanged 和 own-true 中为 3/3，因为未知用户和其他情形都返回同一个 fallback 4xx 或同一个 409；omit-event 中为 0/3，因为全部是 503，不是 4xx。必须结合显式结果解读。
- **外键兜底**：候选如果漏掉本租户校验，写入他租户 assignee 会触发 23503，表现为 503（显式失败），不会写出跨租户数据。

## 运行真实队列（尚未执行）

```bash
python3 benchmark/phase6b/ctl.py fixtures --output $W/private/fixtures.json
python3 benchmark/phase6b/ctl.py plan --base d8a5c576cc3c293ee2da884f5c618958df407b51 --engine kiro \
  --model <model> --repetitions 10 --fixtures $W/private/fixtures.json --output $W/plan.json
python3 benchmark/phase6b/ctl.py prepare --plan $W/plan.json --trial <id> --output $W/kits/<id>
CTL=benchmark/phase6b/ctl.py benchmark/phase3/run_kiro_cohort.sh $W <evaluator-checkout>
python3 benchmark/phase6b/ctl.py summarize --plan $W/plan.json --inputs $W/evaluations --output $W/summary.json
```

真实计划要求 harness 已提交、工作区干净。每条 lane 使用独立的数据库和端口。复现控制：`python3 benchmark/phase6b/driver_smoke.py --out reports/phase6b-controls`（需要 `ADMIN_DATABASE_URL`、`DATABASE_URL`、`POC_DB_RESET=1`、`POC_HTTP_PORT`）。

### 建议的预注册主假设（供预注册文档采用）

H1：对 `action_enforcement=kernel` 的约定（history、hash、uniform-denial），action 组的遵守率高于 manual 组。按块配对，单侧符号检验，每个模型分别检验。

H2：对 `declared` 的约定（outbox），两组差异不做方向性预测，只报告 action 组中 `purchase.reassigned` / NoEvent / 其他值的分布，以检验 v5b 的“明确选错”现象能否复现。

显式通过率只做次要报告。

## 局限

- **按构造的优势仍在**：action 组的三条 kernel 约定由内核实现，遵守率高是预期结果。真正要检验的是 manual 组的自然违反率，以及 declared 约定的表现。
- **约定由 harness 作者挑选**，存在选择偏差；须在任何模型运行之前冻结。
- **history 约定的“无注释”不彻底**：去冲突用的 ApprovalAudit 注释间接指向了它。manual 组线索只有 SCHEMA 和 reconcile 两个文件，预计违反率很高；高违反率既可能说明“难以推断”，也可能只是“线索太少”，两者不能区分。
- **history 的并发链用例**依赖 `createdAt` 默认值（`clock_timestamp()`）。候选如果自行写入 `now()`，可能因时间戳早于前一事务而失败，结果会计入 history 约定。
- **ApprovalAudit 的 CHECK 不含新操作**：候选若照抄既有写法为改派写 ApprovalAudit，会得到 23514，表现为 503，记为显式失败，而不是隐含违反。这是有意的选择（schema 和注释一致说明不该写），但会让这类违反显示为显式失败。
- **requester 在数据库层面**可以修改本人 rejected 单的 assigneeId（列权限加 reopen 的 RLS 分支），任何路由都不会这样做，Oracle 也不检查。
- **内核的 managed 列只有一个**（ponytail：history 写入硬编码为 AssignmentHistory；新增 managed 列时需要为每列配置历史表）。
- **只有一条 declared 约定**，与 v5b 相同，便于复现比较，但无法区分“必填字段”在不同字段上的效果。
- 隔离条件与 v5 相同：只有文件系统 Seatbelt，网络不受限，同宿主生成与验收，公开仓库里有 Oracle 和参考实现，**不是盲测**。
- 本地数据库为 `postgres:17-alpine`（17.x，不是 CI 的 17.6）；phase6b 控制尚未接入 public CI（按约束未修改 workflows）。
