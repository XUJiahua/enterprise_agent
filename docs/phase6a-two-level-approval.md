# v6a 分级审批实验：二次审批（countersign）

状态（2026-11-15）：**只有 harness 和人工/假 CLI 控制，真实模型样本为 0。** 这是预注册确认性实验中的一个任务：沿用 [v5b](phase5b-kiro-cohort-001-results.md) 的设计原则，换一个新业务任务，检验 v5/v5b 的发现能否复现。`benchmark/phase3`、`phase4`、`phase5`、`phase5b`、`tests/acceptance.py` 和已有证据均未修改，入口是 `benchmark/phase6a/ctl.py`。

## 任务

起点是 v5b 的完成态：批量审批、批量驳回和重新提交（reopen）都已存在。此外，批量审批**已经实现金额分级**：amount 大于 50000 的采购单经过一审后只进入 `pending-second`（仍列在 `approvedIds` 中）。新任务是二次审批：

`POST /purchases/{id}/countersign`，请求体 `{"amount":N}`

- 只有 approver 可以调用，目标必须是本租户的 `pending-second` 采购单，不能是调用者本人创建的。
- 调用者不能是把该单推进到 `pending-second` 的那位 approver。
- N 必须等于当前金额，否则返回 409；被拒绝的请求不占用 key。
- 成功后状态变为 `approved`，响应 `{"id","status":"approved"}`。
- 幂等、ActionRequest、503 回滚和并发的要求与 v5 相同；并发场景另加“两位不同 approver 同时二审”。

任务全文见 `cohort.json` 的 `requirements`。

**方向调整及理由。** 原方向是由候选同时完成“一审进入中间态”和“二审”。我把一审分级放进了起点，候选只实现二审，原因有三：

1. 修改 bulk-approve 时，action 组必须改受保护内核或另开扩展钩子。这会把 v4 的“扩展点可发现性”问题重新混进来，而 v5b 已经把这个变量固定住了。
2. 一审分级写进起点之后，可以作为回归（`tier-regression`）冻结。
3. 二审接口仍然满足“形状不同”的要求。它与三个既有 Action 的差别如下：
   - 单目标，但调用方是 approver，不是 requester；
   - 起始状态是 `pending-second`；
   - 需要职责分离（SoD），要从审计历史查出一审人，这是新的查询；
   - 请求体是金额确认，不是 reason，因此有新的共享适配器 `protocol.Confirm`；
   - 金额不一致要单独返回 409。

**平台护栏（两组相同）。** v6a schema（`benchmark/phase6a/schema.sql`）新增状态 `pending-second`，替换了 v5 的 UPDATE 策略（Purchase 仍然只有三条策略），并新增 `purchase_tier_guard` 触发器，在行状态迁移时强制两条规则：

- 金额分级：大额不能直接从 submitted 变为 approved；
- 二审人不能是一审人。

这样，原有单条 approve 接口和任何写错的候选都只会失败（503），不会越权批准。`no-separation` 控制验证了这一点：首审人二审时返回 503，数据库不变。

## 分组

| 组 | 既有 Action | 新增 countersign 的工作 |
|---|---|---|
| go-manual | `approval.go`（分级审批）、`reject.go`（v5b 原样）、`reopen.go`，均为手写 | 手写单目标事务：查一审人、核对金额，约定要从多个文件推断 |
| go-action | 受保护内核 `internal/action/action.go`。`Operation` 新增 `Over/OverTo`（分级）、`Separate`（职责分离）、`Confirm`（金额确认）；`Run(ctx,u,op,key,Request{IDs,Reason,Amount})`；`Event` 必填，可填 `NoEvent`；只传 Amount 而不设 Confirm 时，Run 直接报错（fail-loud）。三个既有 Operation 都声明在可编辑的 `internal/candidate/operations.go` 里 | 在候选包中声明 `countersignOp`，并通过 `protocol.Confirm` 注册 |

两组共享且字节一致的文件包括：

- `go.mod/go.sum`、`internal/app`、`cmd/server/main.go`、`internal/protocol`、`internal/ledger`、`internal/notify`；
- `SCHEMA.sql`、`TASK.md`；
- `.gitignore`（含 `/server`）。

两组都只允许修改 `internal/candidate/**.go`。候选包和内核里都不出现 `countersign` 字样，内核不声明任何 Operation 值（`test_phase6a.py` 检查）。

## 四条隐含约定与线索分布

`cohort.json` 中每条约定都有 `action_enforcement`：`kernel` 表示内核实现、action 作者无需做事；`declared` 表示要靠作者在声明里正确填写。预注册的主假设直接使用这个字段。

`test_phase6a.py` 检查以下各项：

- 每个 `(文件, 原文片段)` 都存在于该组的导出文件中；
- 每条约定每组至少涉及 2 个文件，且至少 1 个在 `internal/candidate` 之外；
- `action_enforcement` 只取 kernel/declared，kernel ≥ 2 条，declared ≥ 1 条；
- `commented=false` 的约定，其 `comment_words` 不出现在任何导出文件的注释中；
- 任务文本和提示词不含 `forbidden_in_task`。

| 约定 | action_enforcement | 内容 | manual 线索 | action 线索 | 注释 | 自然出错的方式 |
|---|---|---|---|---|---|---|
| implicit-audit | kernel | 同一事务写 1 条 ApprovalAudit：actor、`countersign.v6`、key、pending-second→approved、reason 为 NULL；审计写入失败时整体回滚 | approval.go、reopen.go 的 INSERT；reconcile.go 的 Drift 说明；SCHEMA 的 fromStatus 含 pending-second | action.go 的 INSERT；reconcile.go；SCHEMA | 有（reconcile） | 只改状态和 ActionRequest，不写审计 |
| implicit-request-hash | kernel | `requestHash = ledger.Fingerprint("countersign.v6",[id])`，金额不参与 | approval.go、reopen.go 的 Fingerprint 调用（reopen 不含 reason）；ledger.go 的 Verify 代码和单目标 `body["id"]` | action.go；ledger.go | **无**（v6a 去掉了 ledger 的摘要注释，test 检查 fingerprint/digest/hash/subject 不出现在任何注释中） | 把 amount 拼进摘要、自写 sha256 |
| implicit-outbox | **declared** | 同一事务写 1 条 PurchaseEvent：`purchase.countersigned`、actor、key；outbox 写入失败时整体回滚。action 组即新声明的 `Event` 值 | reject.go 的 INSERT；reopen.go 的 `'purchase.reopened'`；notify.go 的 `case "purchase.countersigned":` 和冲突说明；SCHEMA | operations.go 的 `Event: "purchase.reopened"` 与 `Event: action.NoEvent`；action.go 的 "Event is required"；notify.go | 有（notify） | 照抄 approveOp 写 NoEvent；类型名写成 purchase.approved 等 |
| implicit-uniform-denial | kernel | 缺失、他租户、submitted、approved、本人创建、首审人六种目标，返回与缺失相同的 4xx | approval.go 的计数比较和 v5b 的 "Actions never reveal existence"；reject.go；reopen.go 的合并判断；server.go 的 ErrNoRows→404 | action.go；server.go | 有（v5b 注释） | 首审人返回 403（看起来像授权错误）、缺失返回 404、状态不符返回 409 |

**线索之间不冲突（v5 的教训）。** 新旧风格并存的两处都在代码里说明了以哪个为准：

- **事件**：approveOp 和 bulk-approve 不发事件，而 countersign 也是审批，容易照抄。notify.go 写明“一审早于 outbox，这个例外是历史原因；outbox 之后新增的 Action 每个目标写一个事件，类型要列在 audience 中”。单测锁定这段文字。
- **拒绝**：legacy 单条接口与 Action 的拒绝方式不同。沿用 v5b 在 approval.go、reject.go 中的注释。

摘要和审计这两条在既有代码中没有不一致的写法。

**RLS 兜底范围。** v5 发现，加锁读取会让统一拒绝“碰巧”成立。v6a 中 approver 的 UPDATE 策略接受 submitted 和 pending-second，因此 `FOR UPDATE` 读取只能隐藏他租户、本人创建和 approved 三类目标。submitted 和首审人两类必须由候选自己判断，兜底比 v5 弱。

## 计分

每次验收 **193 项 = 显式 180 + 隐含 13**。

- **countersign 显式部分**：功能 3、授权 3、资格 6（含首审人）、金额 2、输入 7、幂等 3、并发 3、ActionRequest 故障回滚 1、生命周期 3（二审后终态；“创建→一审→二审”端到端；“驳回→重开→换人一审→原驳回人二审”，用于确认 SoD 只针对推进到 pending-second 的那位 approver）。
- **tier-regression 4**：起点的分级行为，包括大额进入中间态、混合批次、等于阈值时直接批准、中间态不接受批量操作。
- **回归**：冻结的 v5 Oracle 145 项原样复用，分为 reopen 37、reject 42、v3 approve 50、原有 API 16。v5 的隐含约定在 v6a 中属于既有行为，计入显式回归。
- **隐含部分**：audit 3、request-hash 2、outbox 3、uniform-denial 5。

显式检查只看 HTTP、Purchase 状态，以及 ActionRequest 的作用域行和 response；拒绝类检查接受任意 4xx（契约写明的除外），并要求数据库全部不变。契约里没有写明的实现细节都不检查：

- 不检查锁的方式、SQL 写法、错误码细节和摘要公式；
- 对“被拒绝的请求不占用 key”的检查，依据是任务文本中写明的那句话。

审计和 outbox 的故障用例中，不写对应行的候选在注入故障时会返回 200，因此只有该隐含组失败。两份参考都按照契约实现，并都通过了全部用例。

结果字段和 summarize 的输出与 v5 相同，只是 version 改为 6。候选崩溃（日志出现 panic 或进程退出）判为 candidate-error。

## 控制结果

环境为真实 PostgreSQL 17.11（`postgres:17-alpine`），证据在 `docs/evidence/phase6a-two-level-approval-controls-2026-11-15/`，harness 提交为 `0bec75b`，运行时工作区干净。

| 组 | 控制 | 显式 | 隐含 | 未遵守的约定 |
|---|---|---|---|---|
| manual | correct（手写参考） | 180/180 | 13/13 | — |
| manual | omit-audit / amount-hash / omit-event / distinguish | 各 180/180 | 10、11、10、9 /13 | 各自只有目标约定 |
| manual | no-separation（显式负控制） | 178/180 | 12/13 | uniform-denial（首审人得到触发器返回的 503） |
| manual | unchanged（显式负控制） | 156/180 | 5/13 | audit、hash、outbox |
| action | correct（内核 + 候选包内的 `countersignOp`） | 180/180 | 13/13 | — |
| action | no-event（内核声明，`Event: action.NoEvent`） | 180/180 | 10/13 | outbox |
| action | omit-audit / amount-hash / distinguish（绕过内核手写） | 各 180/180 | 10、11、9 /13 | 各自只有目标约定 |
| action | empty-event（显式负控制，`Event` 留空） | 159/180 | 0/13 | 全部（每次 Run 都 503） |
| action | no-separate（显式负控制，漏写 `Separate`） | 178/180 | 12/13 | uniform-denial（503） |
| action | unchanged（显式负控制） | 156/180 | 5/13 | audit、hash、outbox |

- 两份参考均为 193/193。
- 8 个隐含负控制（每组每条约定各一个）显式全过，且未遵守的约定集合**恰好**等于目标约定，driver 断言两个集合相等。
- 5 个显式负控制都被识别。
- 全部 15 次控制的五组回归（4/37/42/50/16）均全过。
- 崩溃控制：countersign 处理函数 panic，同时目录里有游离的 `server` 文件，结果判为 `candidate-error`，`server` 未被判越界。
- 交接控制：两组各做一次 `prepare → phase3 generation_worker.py（kit 中的 worker.py，假 kiro-cli）→ restore → evaluate`，均为 193/193，receipt 记录 engine=kiro、credits=0.25。
- 部分分母（rate=null）和重复结果拒绝均已检查。
- `python3 -m unittest discover -s benchmark/tests`：71 项通过，其中 phase6a 新增 17 项。
- driver 全程 172 s。

这些都是人工或假 CLI 控制，不计入模型样本。

## 运行真实队列（尚未执行）

```bash
python3 benchmark/phase6a/ctl.py fixtures --output $W/private/fixtures.json
python3 benchmark/phase6a/ctl.py plan --base d8a5c576cc3c293ee2da884f5c618958df407b51 --engine kiro \
  --model <model> --repetitions 10 --fixtures $W/private/fixtures.json --output $W/plan.json
# 每个 trial：ctl.py prepare --plan ... --trial ... --output $W/kits/<trial>
CTL=benchmark/phase6a/ctl.py benchmark/phase3/run_kiro_cohort.sh $W <evaluator-checkout>
python3 benchmark/phase6a/ctl.py summarize --plan $W/plan.json --inputs $W/evaluations --inputs $W/results
```

plan 会冻结全部 harness 文件的 hash（含 phase5/phase4/phase3 Oracle、`tests/acceptance.py`、phase3 worker 和所有源码模板）。真实计划要求 harness 已提交且工作区干净。验收需要 `ADMIN_DATABASE_URL`、`DATABASE_URL`、`POC_DB_RESET=1` 和空闲的 `POC_HTTP_PORT`。每条 lane 使用独立的数据库和端口。

## 局限与未解决问题

- **按构造的优势仍在。** action 组中 audit、hash、denial 由内核实现，按定义遵守率接近 100%。这是预期结果，不是发现；可检验的部分是 declared 那条约定（outbox）的遵守率，以及 manual 组的自然违反率。
- **declared 只有 1 条。** 主假设中 declared 一侧只有一个约定、一个任务，统计力有限。`Separate` 和 `Confirm` 也是声明字段，但它们属于显式契约（漏写会被显式测试抓到），没有计为隐含约定。
- **约定由 harness 作者挑选**，存在选择偏差，必须在任何模型运行前冻结。
- **可照抄性。**
  - 起点的 reopen.go 是单目标范例，展示了审计、事件和 `Fingerprint(op, []string{id})` 的写法。v6a 降低的是“整段复制即正确”：SoD 查询、金额核对、新适配器都没有范例。附近代码仍然可以模仿，可照抄的程度没有量化。
  - action 组的 operations.go 中有 reopenOp 这个单目标示例。
- **uniform-denial 的空过。** 路由没实现时（unchanged），所有请求都落到 fallback 的 404，这条约定会显示为“遵守”，只能结合显式结果解读，与 v5 相同。首审人和 submitted 两类目标不受 RLS 兜底，他租户、本人创建和 approved 三类仍受 `FOR UPDATE` 兜底。
- **触发器带来的耦合。** 漏做 SoD 的候选得到的是 503，不是 200。这对安全有利，但会让一个缺陷同时表现为显式失败（资格、生命周期）和隐含失败（uniform-denial）。这样的候选本身已经显式失败，不影响“隐含约定只在显式全过时有意义”的解读。
- **request-hash 无注释的代价。** 为满足“至少一条无注释”，v6a 的 ledger.go 去掉了 v5 的摘要注释。因此 ledger 这一线索比 v5 弱，与 v5 的 request-hash 列不是同一条件。
- **隔离条件与 v5 相同**：只有文件系统 Seatbelt，网络不受限，生成和验收在同一宿主，公开仓库里有 Oracle 和参考答案，**不是盲测**。本地数据库为 17.11，不是 CI 的 17.6。phase6a 控制**尚未接入 public CI**（按约束未修改 `.github/workflows/`）。
