# v5 分散约定实验：重新提交被驳回的采购单

状态（2026-11-15）：**只有 harness 和人工/假 CLI 控制，真实模型样本为 0。** `benchmark/phase3/`、`benchmark/phase4/`、`tests/acceptance.py` 与所有已有证据均未修改。

## 为什么做 v5

[v4 队列 001](phase4-kiro-cohort-001-results.md) 中，manual 起点里有一份带注释、可照抄的完整批量审批实现，新任务（批量驳回）与它同形，四个模型的 manual 组 40 次只自然违反 1 次。v5 要检验的是：**当约定分散在多个文件、且没有与新操作同形的范例时**，模型能否把它们带进新 Action。

## 起点与任务

起点是 v4 的完成态：批量审批与批量驳回都已存在。在此之上 v5 起点新增了三处"企业里通常已经有"的周边代码，作为线索的载体：对账任务 `internal/ledger/reconcile.go`（状态漂移检查）、通知 outbox 的消费者 `internal/notify/notify.go`，以及 `PurchaseEvent` 表。驳回已经写 outbox 事件；审批早于 outbox，不写事件，由 notify 的注释说明。选择 v4 完成态而不是更早的起点，是因为线索需要至少两个形状不同的既有 Action（审批用 SELECT FOR UPDATE + UPDATE，驳回用 UPDATE … RETURNING + pgx.Batch）才能"分散"。

新任务是 `POST /purchases/{id}/reopen`：requester 把本人创建、已驳回的采购单重新提交（rejected → submitted），请求体带 `reason`。与既有 Action 形状不同的地方有：单目标、路径参数、调用方是 requester 而不是 approver、只能操作本人的单、方向反转、附带原因字段、响应是 `{"id","status"}`。共享适配器新增了 `protocol.Item`（单目标、按角色、严格 `{"reason"}` 请求体），两组相同。任务文本见 `cohort.json` 的 `requirements`；单测检查任务文本和两组提示词都不含 `forbidden_in_task` 中的词（审计、事件、outbox、ledger、requestHash、区分、存在、对账等）。

v5 schema（`benchmark/phase5/schema.sql`）：ApprovalAudit 增加可空的 `reason` 列，主键改为可重复审计，from/to 状态放宽；新增 `PurchaseEvent`（RLS、只追加）；ActionRequest、ApprovalAudit 的策略允许 requester；Purchase 仍然只有三条策略，UPDATE 策略合并了 approver 决策与 requester 本人重开。事件 `type` 和审计 `reason` **刻意不加 CHECK**，这样约定被违反时表现为隐含失败，不会变成 23514 导致全部 503、混进显式失败。

## 四条隐含约定与线索分布

每条约定的 `(文件, 原文片段)` 写在 `cohort.json` 的 `conventions` 里。`test_phase5.py` 检查：每个片段确实存在于该组的导出文件中；每条约定在每组至少涉及 2 个文件；每条约定至少有 1 条线索在 `internal/candidate` 之外，即与新操作相关的线索不能从可编辑的既有函数里抄到；候选包中不出现 `reopen` 字样；`commented=false` 的约定在全部导出文件的注释里都没有提示词。

| 约定 | 内容 | manual 组线索（文件） | 有无注释 | 自然出错的方式 |
|---|---|---|---|---|
| implicit-audit | 同一事务写 1 条 ApprovalAudit：approvedBy=actor，operation=reopen.v5，key，rejected→submitted，reason；审计写入失败时整体回滚 | approval.go、reject.go 的 INSERT；reconcile.go 的 "status is not the toStatus of its latest" ApprovalAudit；SCHEMA.sql 的 `reason` 列与放宽的 fromStatus | reconcile 有注释，候选代码无 | 只改状态、不写审计；不写 reason |
| implicit-request-hash | `requestHash = ledger.Fingerprint("reopen.v5", [id])`，reason 不参与 | approval.go / reject.go 对 `ledger.Fingerprint(op, ids)` 的调用；ledger.go 的单目标主体规则（"id" 字段）和 Verify 的重算 | 仅 ledger 有注释 | 把 reason 拼进摘要、自写 sha256、沿用驳回的 op 名 |
| implicit-outbox | 同一事务写 1 条 PurchaseEvent：type=purchase.reopened，purchaseId，actorId，key；outbox 写入失败时整体回滚 | reject.go 的 INSERT 与 `'purchase.rejected'`；notify.go 的 `case "purchase.reopened":` 和"审批早于 outbox"的说明；SCHEMA.sql 的表 | notify 有注释 | 照抄审批（无事件）；类型名写成 purchase.reopen 等 |
| implicit-uniform-denial | 缺失、他租户、他人创建、submitted、approved 五种目标返回同一个 4xx | approval.go 的 `len(eligible) != len(ids)`；reject.go 的 `changed != len(ids)`；原有 server.go 中单条审批在 RLS 下统一映射 `pgx.ErrNoRows`→404 | **完全无注释** | 单目标时很自然地写成缺失 404、他人 403、状态不符 409 |

action 组：内核实现审计、摘要和统一拒绝，线索是内核代码加上 ledger、reconcile 和 SCHEMA。outbox 仍需候选**自己选事件类型**，因为内核只在 `Operation.Event` 非空时写事件，候选包里的 `rejectOp` 是唯一示例，类型名只出现在 notify.go 里。

与 v4 相比降低的可照抄性：
- v4 manual 的单个函数同时包含四条约定，并有直接指向它们的注释，新任务与它同形。v5 的既有函数都是批量、approver、正向的；与 reopen 相关的部分（reason 列、事件类型名、单目标摘要规则、单目标的统一拒绝）只存在于候选包之外的文件。
- 候选代码里没有任何约定注释，无注释的那条在全部导出文件里都没有提示。
- 仍然存在的可照抄性：reject.go 展示了全部四种**批量形态**的写法（审计 INSERT、事件 INSERT、Fingerprint、计数不符返回 409）。模型如果照它改写，仍可能得到大部分约定。v5 降低的是"整段复制即正确"，不是"可以从附近代码模仿"。

## 分组设计（吸取 v4 教训）

| 组 | 既有 Action | 新增 reopen 的工作 |
|---|---|---|
| go-manual | `internal/candidate/approval.go`（审批）和 `reject.go`（驳回），均为手写 | 手写单目标事务，约定要从多文件推断 |
| go-action | 受保护的内核 `internal/action/action.go`（`Operation{Name,Role,Own,From,To,Field,Event}` + `Executor.Run(...,reason)`）；**审批和驳回的 Operation 值声明在可编辑的 `internal/candidate/operations.go`** | 在候选包中声明 `reopenOp` 并经 `protocol.Item` 注册 |

v4 中，haiku 和 qwen 在 action 组 20/20 把新的 Transition 写进了受保护内核，因为唯一的示例 `action.Approve` 定义在内核包里。v5 的内核**不声明任何 Operation 值**（单测用正则检查），类型文档写明"在服务包中声明"，提示词也说"declare new operations in internal/candidate as well"。这样，模仿既有示例的位置就是正确位置。代价是：位置提示变强以后，action 组无法再测"扩展点是否显而易见"，这个问题需要另开队列（例如 v4b）单独检验。

两组共享且字节一致的文件：`go.mod/go.sum`、`internal/app`、`cmd/server/main.go`、`internal/protocol`、`internal/ledger`（两个文件）、`internal/notify`、`SCHEMA.sql`、`TASK.md`、`.gitignore`（含 `/server`）。两组都只允许修改 `internal/candidate/**.go`。

## 计分

每次验收 **145 项 = 显式 133 + 隐含 12**：

- 显式：reopen 的功能 3、授权 3、资格 5、输入 5、幂等 3、并发 2、ActionRequest 故障回滚 1、生命周期 3；回归为冻结的 v4 Oracle 108 项原样复用（驳回 42 项、v3 审批 50 项、原有 API 16 项）。v4 的驳回隐含约定在 v5 中属于既有行为，计入显式回归。
- 隐含：audit 3、request-hash 2、outbox 3、uniform-denial 4。
- 显式检查只看 HTTP、Purchase 状态，以及 ActionRequest 的作用域行和 response。拒绝类检查接受任意 4xx 且要求数据库全部不变。每个隐含用例只依赖一条约定。审计和 outbox 故障用例中，不写对应行的候选会在注入故障时返回 200，因此只有该隐含组失败。
- 冻结的 v3/v4 Oracle 会比较整行审计，验收时经 `LegacyView` 去掉 v5 新增的 `reason` 列后传入，Oracle 本身不变。
- 结果字段、summarize 的 `passed_all / passed_explicit / conventions_followed / first_pass_rate / explicit_pass_rate`、未测量时 rate 为 null，均与 v4 相同。候选崩溃（日志出现 panic 或进程退出）判为 candidate-error。

## 控制结果（真实 PostgreSQL 17.11（postgres:17-alpine），`docs/evidence/phase5-scattered-convention-controls-2026-11-15/`）

| 组 | 控制 | 显式 | 隐含 | 未遵守的约定 |
|---|---|---|---|---|
| manual | correct（手写参考） | 133/133 | 12/12 | — |
| manual | omit-audit / reason-hash / omit-event / distinguish | 各 133/133 | 9、10、9、9 /12 | 各自只有目标约定 |
| manual | no-replay（显式负控制） | 131/133 | 12/12 | — |
| manual | unchanged（显式负控制） | 114/133 | 4/12 | audit、hash、outbox |
| action | correct（内核 + 候选包内 `reopenOp`） | 133/133 | 12/12 | — |
| action | omit-event（内核，`Event` 留空） | 133/133 | 9/12 | outbox |
| action | omit-audit / reason-hash / distinguish（绕过内核手写） | 各 133/133 | 9、10、9 /12 | 各自只有目标约定 |
| action | own-false（显式负控制，`Own:false`） | 121/133 | 4/12 | audit、hash、outbox |
| action | unchanged（显式负控制） | 114/133 | 4/12 | audit、hash、outbox |

- 两份参考 145/145。
- 8 个隐含负控制：显式全过，且未遵守的约定集合**恰好**等于目标约定（driver 断言集合相等）。
- 4 个显式负控制都被识别。
- 全部 14 次控制的三组回归（42/50/16）均全过。
- 崩溃控制：reopen 处理函数 panic，同时目录里有游离的 `server` 文件，结果判为 `candidate-error`，`server` 未被判越界。
- 交接控制：2 次 `prepare → phase3 独立 worker（假 kiro-cli）→ restore → evaluate`，均 145/145，receipt 记录 engine=kiro、usage=credits 0.25。
- 部分分母（rate=null）和重复结果拒绝均已检查。
- `python3 -m unittest discover -s benchmark/tests`：54 项通过，其中新增 phase5 共 11 项。
- driver 全程约 112 s。

这些都是人工或假 CLI 控制，不计入模型样本。

控制过程中得到一项设计事实：第一版 distinguish 控制用 `SELECT … FOR UPDATE` 读目标，结果 145/145 全过。原因是在 guarded RLS 下，FOR UPDATE 只能看到 UPDATE 策略允许的行，他人的单和非 rejected 的单都"不存在"，统一 404 就自然出现了。现在的负控制改用不加锁的普通 SELECT（他人 403、状态 409），这是更自然的写法。也就是说，**uniform-denial 在本 schema 下部分由 RLS 兜底**：加锁读取的候选会"碰巧"遵守，这条约定的违反率会被低估。

## 运行真实队列（尚未执行）

命令与 v4 相同，把 `phase4` 换成 `phase5`：`fixtures → plan --engine kiro → prepare → isolate.py → restore → evaluate → summarize`。lane 运行复用 `benchmark/phase3/run_kiro_cohort.sh`，设置 `CTL=benchmark/phase5/ctl.py`，并为每条 lane 使用独立的数据库和端口。真实计划要求 harness 已提交且工作区干净；验收需要 `ADMIN_DATABASE_URL`、`DATABASE_URL`、`POC_DB_RESET=1` 和空闲的 `POC_HTTP_PORT`；退出码 0/2/3/1 的含义与 v4 相同。

## 局限与未解决问题

- **按构造的优势仍在。** action 组的审计、摘要和统一拒绝由内核实现；只有 outbox 事件类型需要推断。action 组在这三条上的遵守率是预期结果，不是发现。
- **约定由 harness 作者挑选**，存在选择偏差；在任何模型运行前冻结，不能看过结果后再增删。
- **可推断性强弱不一**：uniform-denial 无注释，同时受 RLS + FOR UPDATE 兜底（见上）；在路由未实现时它会"空过"（缺失和其他目标都是 fallback 的 404），只能结合显式结果解读。其余三条都有至少一处基础设施注释作为线索。
- **reject.go 仍是近似范例**（批量形态的四种写法都在里面），降低可照抄性的程度没有量化；真正检验需要真实队列，并对比 v4 manual 的违反率。
- **提示词里的位置提示**（在 internal/candidate 中声明）让 v5 action 组不能再测扩展点的可发现性。
- request-hash 和 uniform-denial 的 Oracle 都是 Python 独立实现或基于 HTTP 观察，两份参考（使用 Go ledger 和内核）都通过，是二者等价的唯一证据。
- 隔离条件与 v4 相同：只有文件系统 Seatbelt，网络不受限，生成与验收同宿主，公开仓库里有 Oracle 和参考，**不是盲测**。
- 本地数据库为 PostgreSQL 17.11（`postgres:17-alpine`，不是 CI 的 17.6）；phase5 控制**尚未接入 public CI**（需要修改 `.github/workflows/poc.yml`，本次按约束只新增文件）。
