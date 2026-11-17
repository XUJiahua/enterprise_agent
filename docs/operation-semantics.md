# 受保护的操作语义目录（opdef）、目录内核与目录绑定 Verifier（2026-11-16）

v6 的结论是：平台减少了实现错误，但把一部分错误转移到了声明层。作者照抄最相似的先例写了 `Event: action.NoEvent`，T2 qwen 漏写了职责分离，T3 的 `action.Update` 根本没被冻结 Verifier 认出来（[v6 结果](v6-confirmatory-results.md)）。本轮把这些“值是否正确”的业务语义从扩展代码里拿走，放进一份经审查的操作目录；内核和 Verifier 都只认这份目录。

**本轮没有调用任何模型，也没有 v7 任务内容。** 复评只是对已见数据的回顾，不是样本外评估。

## 组成

| 包 | 作用 | 是否受保护 |
|---|---|---|
| `internal/opdef` | 操作目录：小型 Go 类型 + `catalog.go` 中的条目，`Lookup`/`All`/`Validate`/`Digest` | 是（改动需审查，摘要钉在单测中） |
| `internal/opkernel` | 按操作名查目录并在一个 PostgreSQL 事务中执行 | 是 |
| `internal/extcheck/operations.go` + `appctl operations` | 目录绑定的 Verifier | 是 |
| `internal/candidate/` | Agent 写的适配代码：路由、请求解析、调用 `Run(ctx, u, opdef.X, key, req)` | 可编辑 |

**命名理由。** `opdef` 意为 operation definition，与 `internal/action` 分开：`internal/action` 是 v3–v6 的内核形态，冻结计划和归档候选都引用它，不能改。新内核取名 `opkernel` 也是为了与之并存，让 Verifier 能同时检查 v6 归档（`internal/action`）和新平台（`opdef` + `opkernel`）。

## 职责划分

| 内容 | 归属 | 本轮实现 |
|---|---|---|
| 事务、完整目标检查、回滚、幂等 | 内核 | `opkernel.Run`：一个事务；目标不全部合格即统一拒绝；任一写入失败整体回滚 |
| 审计/历史、指纹组成、事件类型、职责分离、拒绝语义 | 经审查的目录条目，由内核执行 | `opdef.Operation` 字段，内核只读条目 |
| 具体业务处理与接口适配 | Agent 编写的扩展代码 | 只传 `opdef.Name` 和请求值（`opkernel.Request`） |

## 目录条目结构

```go
type Operation struct {
	Name    Name    // 带版本后缀，例如 "bulk-reassign.v6"
	Kind    Kind    // transition（状态迁移）| field-update（字段修改）
	Role    string  // approver | requester
	Subject Subject // own（本人创建）| others（他人创建）
	From, To string // transition：from → to
	Tier    *Tier   // transition 可选：金额高于 Over 时改去 Tier.To
	Status, Column, Param string // field-update：目标必须处于 Status 并保持；Column 取请求字段 Param 的值
	Response    string   // 批量响应的数组字段；"" 表示单目标
	Inputs      []string // 请求字段及顺序（ids 在首位），也是 protocol handler 在 key 之后的参数顺序
	Fingerprint []string // 参与 requestHash 的字段及顺序；未列出的字段和 Idempotency-Key 一律不参与
	Record      Record   // ApprovalAudit（状态迁移）| AssignmentHistory（字段修改）
	Event          string // 每个目标在同一事务中写一条的 PurchaseEvent 类型
	EventExemption string // 只能二选一：不发事件时必须写明理由
	Separation    Separation // distinct-from-previous-approver：与把购买单推进到 From 的审批人不同
	ConfirmAmount bool       // 请求必须确认金额
	Denial        int        // 统一拒绝状态码，固定为 409
}
```

当前五个条目（`internal/opdef/catalog.go`，摘要 `sha256:98abea36467340ad3702d12341beb3894e649d42d9129cfac4e45e46a9de0055`）：

| 条目 | 类别 | 角色/对象 | 状态或列 | 指纹 | 记录 | 事件 | 其他 |
|---|---|---|---|---|---|---|---|
| `bulk-approve.v3` | transition | approver / others | submitted → approved，>50000 → pending-second | ids | ApprovalAudit | **豁免**：早于 outbox，日报从 ApprovalAudit 读审批 | |
| `bulk-reject.v4` | transition | approver / others | submitted → rejected | ids | ApprovalAudit | `purchase.rejected` | |
| `reopen.v5`（T1） | transition | requester / own | rejected → submitted | ids（reason 不参与） | ApprovalAudit（含 reason） | `purchase.reopened` | 单目标 |
| `countersign.v6`（T2） | transition | approver / others | pending-second → approved | ids（amount 不参与） | ApprovalAudit | `purchase.countersigned` | 职责分离、确认金额、单目标 |
| `bulk-reassign.v6`（T3） | field-update | approver / others | 保持 submitted，改 assigneeId | ids、**assignee** | AssignmentHistory | `purchase.reassigned` | |

**事件默认必发。** `Validate` 要求每个条目恰好有 `Event` 或 `EventExemption` 之一；单测还要求豁免理由不少于四个词，并且只有 `bulk-approve.v3` 有豁免。豁免只是这个条目上的一个字段，扩展代码拿不到“声明”这一步：内核只接受 `opdef.Name`，`Lookup` 返回副本，修改副本不影响内核行为。指纹规则与 v6 `internal/ledger` 完全一致（`opkernel_test` 逐条目断言），所以 v6 的对账作业不用改。

### 新增业务操作的路径

1. 平台方把条目加入 `internal/opdef/catalog.go`。摘要变化，`opdef_test` 的钉住值必须同步修改，这一步需要审查。
2. 如果是新的字段修改列，还要在 `opkernel.valid` 中加入值校验，并确认历史表能记录（`ponytail:` 注释标明目前只支持 `assigneeId`）。
3. Agent 写适配代码：`executor.Run(ctx, u, opdef.NewThing, key, opkernel.Request{...})`，响应字段取 `Lookup(...).Response`。

## 内核（`internal/opkernel`）

`Run(ctx, identity, name, key, Request)` 先 `Lookup`：名字不在目录里，或请求带了条目未声明的字段（例如给 reject 传 Amount），直接报错，不写任何数据。其余的角色、对象、from/to 或列、分级、职责分离、金额确认、审计或历史、事件、指纹、拒绝码全部来自条目。启动时 `New` 会先跑 `opdef.Validate`，并检查每个 field-update 的列都受内核管理。

`TestCatalogOperationsDB` 在 PostgreSQL 上覆盖全部五个条目：分级、豁免条目不写事件、首审人不能复审、金额不符拒绝、reason 不改变指纹、assignee 改变指纹（换 assignee 重放返回 409）、未激活成员拒绝、混入本人购买单整批拒绝、指纹与 v6 ledger 一致、目录外名字和越权字段在写入前被拒、outbox 故障整体回滚。把内核改坏三处做反向确认：跳过事件、去掉职责分离、指纹去掉 assignee，测试都失败。CI 在 cohort-controls job 中以 `POC_REQUIRE_DB=1` 运行，没有数据库时测试直接失败，不会以 skip 冒充通过。

## 与 v6 的实验设计差异

| | v6 | v7（本平台） |
|---|---|---|
| 谁决定事件/指纹/职责分离 | Agent 在声明里填写（`Event` 必填但可填 `NoEvent`） | 平台方预先在目录里给定，Agent 不能填写 |
| Agent 的决策面 | 适配代码 + 语义声明 | 只有适配代码 |
| “声明层错误”（D） | 可观察，是 v6 的主要失败来源 | **按构造消除**，v7 不能再测量“声明填错率” |
| 新操作的成本 | 计入 Agent 生成 | 写条目、审查的成本在实验之外，由平台方承担 |

因此 v7 action 组的收益如果出现，含义是“语义交给经审查的目录之后，Agent 的适配是否正确”，而不是“Agent 能否自己定对语义”。两者不能与 v6 的 D 直接比较。另外，manual 组是否能看到目录（作为文档）是一个新的设计因素，应在 v7 预注册中事先确定；Verifier 对 manual 组同样以目录为准，见下节。

## Verifier 的变化（`appctl operations`）

```bash
bin/appctl operations --root <checkout> --base <trusted sha> [--legacy Operation,Update]
```

输出 JSON：`catalog`（目录摘要）、`entries`、`verdict`（pass / fail / unverifiable）、`findings`。退出码：0 通过，1 有发现，2 无法运行，**3 unverifiable（门禁阻断）**。旧的 `appctl extensions` 保持不变，以便 v4–v5b 的冻结复现继续在 CI 上逐字节比对。

| 问题（v6） | 旧规则（`extensions`，冻结在 4e286c9） | 新规则（`operations`） |
|---|---|---|
| 版本绑定 | 无 | 输出目录摘要；检出中若有 `internal/opdef`，必须与 Verifier 内置的目录逐字节一致，否则 `CATALOG_DRIFT` |
| 目录之外的操作名 | 只查版本格式和唯一性 | 扩展代码中任何带版本后缀的字符串、任何声明的 Name，不在目录中即 `OPERATION_NOT_IN_CATALOG` |
| 扩展代码自造声明 | v6 中这本身就是用法 | 平台模式下 `opdef.Operation`/`Tier` 等目录类型的字面量一律 `DECLARATION_LITERAL`；带 `EventExemption` 另报 `EVENT_EXEMPTION_NOT_IN_CATALOG`；`opdef.Name(非常量)` 报 `UNVERIFIABLE` |
| 未知声明类型（T3 `action.Update` 漏检） | 不在 Kinds 里的类型被静默跳过 | 每个内核包的**每个导出类型**都要在 Verifier 表中分类（值类型 / 目录类型 / v6 声明类型）。内核里出现未分类类型，或扩展代码出现未分类类型的字面量，都报 `UNVERIFIABLE`；字段不认识、值不是常量、解析失败也一样 |
| v6 声明是否正确 | 必填 + “事件以操作命名”的过去分词启发式 | 每个 `action.Operation`/`Update` 字面量按 Name 找到条目，逐字段比较该内核类型能表达的属性（Role、Own、From/To 或 Status/Column、Field、Over/OverTo、Separate、Confirm），种类不符也报 `DECLARATION_MISMATCH`；`NoEvent` 只对有豁免的条目合法，否则 `EVENT_EXEMPTION_NOT_IN_CATALOG`；其他事件值不符为 `EVENT_MISMATCH` |
| 手写指纹（T3 误报 11 个） | 按 handler 第 5 个参数一律视为 reason | handler 参数按条目的 `Inputs` 顺序映射（reopen 的第 5 个是 reason，countersign 是 amount，reassign 是 assignee）。`ledger.Fingerprint` 的域必须恰好是 `name + "\n" + 各指纹字段`，IDs 参数只能是目标：多了被排除的字段为 `FINGERPRINT_EXTRA_INPUT`，少了为 `FINGERPRINT_INPUT_MISSING`，形状不对为 `FINGERPRINT_SHAPE`，没绑定到该操作为 `FINGERPRINT_OPERATION_MISSING` |
| 手写事件 | 动词词形匹配 notify 中的 case | 条目有事件，包内就必须写这个事件（`EVENT_NOT_WRITTEN`）；手写的事件不在目录事件中为 `EVENT_NOT_IN_CATALOG`；事件类型不是常量为 `UNVERIFIABLE` |
| 手写审计/历史 | 不覆盖 | 条目的 `Record` 表必须有写入（`RECORD_NOT_WRITTEN`），只检查有无，不检查内容 |
| 作用域 | `PROTECTED_PATH_CHANGED` | 不变 |

规则不再从源码形状或动词词形推断语义，期望值一律来自目录。仍然依赖源码形状的是“如何找到对应的写入和 handler 参数”：按 `INSERT INTO "ActionRequest"` 中的常量操作名定位写入函数；handler 参数从写入函数本身的签名取得，或者经由 handler 形状函数对它的调用映射过去；局部变量只跟踪一层单次赋值。凡是这些步骤走不通的，一律报 `UNVERIFIABLE`，不放行。

## 正反控制（`tests/operation_controls.py`，53/53）

起点是 v6 冻结导出（T1 = phase5b、T2 = phase6a、T3 = phase6b，基线 `d8a5c57`），以及一个平台导出：T3 action 导出把 `internal/action` 换成 `opdef` + `opkernel`，扩展代码按名字引用条目。每个控制都断言发现码集合**完全相等**、退出码正确，并且能 `go build`。结果见 [controls.json](evidence/operation-semantics-2026-11-16/controls.json)。

- **正确参考（12 个，全部通过）：** T1/T2/T3 两组的 `correct` 和 `unchanged`；T3 manual 的 `correct`（v6 旧规则在这类候选上误报）；平台导出的 `unchanged`，以及分别用 `opdef.BulkReassign` 和 `opdef.Name("bulk-reassign.v6")` 写的 reassign 适配。
- **负控制（34 个，全部识别）：**
  - 扩展代码自造声明：`opdef.Operation{...}` → `DECLARATION_LITERAL`；
  - 引用不存在的操作：`"bulk-reassign.v7"` 字面量、`opdef.Name("bulk-transfer.v1")`、v6 声明 `reopen.v6` → `OPERATION_NOT_IN_CATALOG`；
  - 试图豁免事件：字面量中写 `EventExemption` → `DECLARATION_LITERAL` + `EVENT_EXEMPTION_NOT_IN_CATALOG`；改受保护目录 → `PROTECTED_PATH_CHANGED` + `CATALOG_DRIFT`；v6 声明在三个任务上写 `NoEvent` → `EVENT_EXEMPTION_NOT_IN_CATALOG`；
  - 未知声明类型被判 `unverifiable`（退出码 3）：Verifier 只认识 `action.Operation`（即 v6 冻结时的知识）时，T3 的正确候选和 NoEvent 候选都被阻断，不再静默放过；内核新增一个 Verifier 不认识的类型 `opkernel.Batch`；名字来自 `os.Getenv`；
  - 其余负控制：漏写事件、空事件、`Own` 写反、漏写职责分离、reason/amount 进指纹、T3 指纹缺 assignee、assignee 混进 IDs、漏写审计/历史（含 action 组绕过内核的手写版本）。
- **已知不覆盖（7 个，保持无发现）：** 区分 404/403/409 的拒绝（`distinguish`，三个任务两组）和 `now-timestamp`。统一拒绝属于行为语义，静态规则不宣称覆盖。

另有单测：`internal/opdef`（目录完整性：每个条目有事件或豁免、名字唯一且带版本、`Lookup` 返回副本、`Validate` 拒绝 11 种错误条目、摘要钉住），以及 `internal/extcheck/operations_test.go`。

## 复评：v6 的 228 个归档候选（回顾性，非样本外）

`python3 research/operation_semantics_replay.py`，候选恢复方式与 v6 样本外评估相同（bundle，越界的用导出加 diff 重建）。结果见 [summary.json](evidence/operation-semantics-2026-11-16/summary.json)、[trials.json](evidence/operation-semantics-2026-11-16/trials.json)。**这批数据在写规则之前就已经看过，规则也针对它做过两处修改（见下文的修改记录），所以以下数字表示拟合程度，不是泛化能力的估计。样本外评估留给 v7。**

按候选，相对于“任一冻结失败”统计：

| 配置 | TP | FP | FN | TN | 显式全过、隐含违反的补拦 | unverifiable（通过 / 失败的候选） |
|---|---:|---:|---:|---:|---:|---:|
| v6 冻结规则（`extensions`，样本外，已发表） | 104 | 11 | 38 | 75 | 76/110 | — |
| **新 Verifier（`operations`）** | **134** | **0** | **8** | 86 | **106/110** | 0 / 0 |
| 消融：只认识 `action.Operation`（语义发现） | 103 | 0 | 39 | 86 | 75/110 | 6 / 33 |
| 消融：同上，把 unverifiable 计为阻断 | 134 | 6 | 8 | 80 | — | |

v6 冻结规则的 TN 是 75，新 Verifier 是 86：v6 统计时有 11 个通过的候选被误报，计入了 FP。

按规则，相对于各自针对的冻结约定统计（新 Verifier）：

| 规则 | TP | FP | FN | 说明 |
|---|---:|---:|---:|---|
| extension-scope | 8 | 0 | 0 | |
| catalog-event | 112 | 0 | 4 | T3 action 组 32 个 outbox 违反全部拦住（v6 冻结规则只拦住 1 个） |
| catalog-fingerprint | 15 | 0 | 4 | T3 manual 组 11 个通过的候选不再误报（v6 冻结规则在这里误报 11 个） |
| catalog-record | 0 | 0 | 17 | 违反都是行内容错误（例如 `fromAssignee` 为空），存在性检查查不出 |
| catalog（声明不符等） | 7 | 0 | — | T2 qwen 5 个漏写 `Separate`，正是 v6 局限第 2 条所说“kernel 类约定依赖声明”的那批；T3 qwen 2 个把 reassign 声明成 `action.Operation` |

消融说明：如果 Verifier 只认识 v6 冻结时的 `action.Operation`，T3 action 组 39 个候选全部被判 `unverifiable` 并阻断（其中 33 个失败、6 个通过）。也就是说，未知类型不会再像 v6 那样被静默放过，代价是门禁需要人工处理；把 `Update` 加入分类表之后，这 39 个候选才得到语义判定。

**剩余 FN（8 个）**：

- 4 个只违反了统一拒绝（T1 haiku manual-007/009、T2 luna manual-010、T2 qwen manual-006），静态规则不覆盖；
- T2 qwen manual-010 的指纹是 `[]string{id, amountStr}`，`amountStr := strconv.Itoa(amount)`。这是派生变量，排除字段的检测没有跟踪它；
- T3 haiku manual-006 的历史行内容错误；
- T1 luna manual-001 只在幂等和并发各失败 1 个用例（503），隐含约定都满足；T1 luna manual-009 的大部分请求返回 503，隐含约定的失败是连带出现的，静态代码中这些约定都已写出。

### 修改记录（每次修改都在查看 v6 数据之后，理由如下）

第一次运行（修改前，[run1-summary.json](evidence/operation-semantics-2026-11-16/run1-summary.json)）的结果是 semantic TP 128 / FP 0 / FN 14；unverifiable 有 8 个，其中 2 个是通过的候选，6 个是失败的。只做了两处修改，之后不再调整：

1. **handler 参数经由调用点映射。** T2 opus manual-001、002 是通过的候选：handler 闭包把参数原样转给带额外 `pool` 参数的 helper。原来的兜底逻辑会比较包内所有五参数 handler，reopen 的 `reason` 和 countersign 的 `amount` 冲突，于是报了 `unverifiable`。改为：沿 handler 对写入函数的实际调用，按位置映射参数。这是通用的拆分写法，不针对某个候选；单测为 `TestManualHelperAndHandRolledHash`。
2. **自写摘要中出现被排除的字段时，判为违反。** 6 个失败候选（T1 haiku/luna、T2 haiku）没有调用 `ledger.Fingerprint`，而是写了 `reopenFingerprint(id, reason)` 这类自定义摘要，原来一律判 `unverifiable`。改为：找到 ActionRequest 写入中绑定到 `requestHash` 列的表达式（跟踪一层局部变量）。如果其中引用了目录排除的字段，报 `FINGERPRINT_EXTRA_INPUT`；否则仍然报 `unverifiable`，因为自写摘要无法证明与目录公式等价。

我们没有为 qwen manual-010 的派生变量 `amountStr` 再改规则，它保留为 FN。v7 如果要扩展到跟踪派生变量，应在预注册中写明。

## CI

- verifier job 新增两步：53 个控制，以及 228 个候选的复评。`scripts/report.py` 要求控制计数为 34/12/7/4，复评 `summary.json` 与入库版本完全一致，并且 `out_of_sample` 为 false。
- cohort-controls job 新增一步：`opkernel` 的 PostgreSQL 测试（`POC_REQUIRE_DB=1`）。`report.py` 要求输出中有 `PASS` 且没有 `SKIP`。

## 平台事件规则（v9 验证）

v9 把两条在实验中验证过的事件规则从私有叠加层移进受保护目录，落在 `internal/opdef/registry.go` 和 `Validate` 的 `eventRule` 中。registry.go 带两份受保护列表：

- `consumedEvents`：有注册消费者处理的 `PurchaseEvent` 类型；
- `exemptOperations`：可以用 `EventExemption` 代替事件的操作，目前只有历史上早于 outbox 的 `bulk-approve.v3`。

两条规则（`Validate` 对每个目录条目执行 `eventRule`，与既有的逐条 `validate` 并列）：

- **R1 — 事件必须有注册消费者。** 不在 `exemptOperations` 上的条目必须声明一个 `Event`，且该事件必须出现在 `consumedEvents` 里。缺事件、或事件没有注册消费者都会被拒绝。既有的“一个事件类型不能被两个条目使用”检查保留。
- **R2 — 豁免是受保护名单的特权。** 只有 `exemptOperations` 上的操作可以带 `EventExemption`；任何其他条目自行声明豁免都会被拒绝。条目不能靠给自己加豁免来跳过 outbox。

`registry.go` 已加入 `//go:embed` 和 `SourceFiles`，所以 `appctl operations` 的目录漂移检查要求候选 checkout 的 `registry.go` 与 Verifier 内置版本逐字节一致；改动注册表等同于改目录，需要所有者审查。

**受保护边界与 notify 的关系。** `consumedEvents` 对应通知 outbox 的消费者（notify 包的 `audience()` 分支）。该消费者不是本仓库 `internal/` 里的运行时包，只存在于冻结的分期 benchmark 任务源码（`benchmark/phaseN/source/notify.go.txt`），且每期只列该期子集（phase6a 有 `purchase.countersigned`，phase6b 有 `purchase.reassigned`，phase5/5b 都没有）。因此没有单一运行时 notify 可供对比，冻结的 fixture 也不能改——`registry.go` 就是公开目录这一侧权威的消费者清单，把之前只在部分 fixture 里出现的 `purchase.countersigned`、`purchase.reassigned` 都登记进受保护边界。一致性改由 `registry_test.go` 对目录双向校验（每个非豁免条目的事件都已登记、每个登记事件都被某条目使用、豁免只限 `exemptOperations`），取代之前的人工审查。

## 局限

- **可信代码模式。** 规则只做语法和常量分析，有意规避（反射、`unsafe`、经变量拼接 SQL）很容易绕过，所以它是辅助防线，不是安全边界。内核只接受目录名，这一点在运行时也成立。但持有数据库凭据的代码仍可直接写表，这与 README 的“信任边界”一节相同。
- **目录、内核、Verifier 与候选在同一仓库。** “受保护”依靠 `PROTECTED_PATH_CHANGED`、摘要钉住和审查约定，不是独立的发布治理。
- **一个名字只能对应一种语义。** `bulk-approve.v3` 在 v6a 加入了分级，但没有改版本号。v6 的 T1/T3 内核类型没有 `Over` 字段，所以 Verifier 只比较该内核类型能表达的属性。新平台中这种情况必须新开版本。
- **覆盖范围。** 手写代码的审计/历史只检查存在，不检查内容；统一拒绝、并发、幂等的重放行为仍由运行时验收负责；手写事件只在包级别检查有无，不检查是否写在同一个事务里。
- **复评是回顾性的。** 规则设计参考了 v6 的失败类型，并针对这批数据修改过两次；FP=0 和 106/110 都不能当作 v7 的预期。
- `notify` 消费者与目录事件的一致性，v9 起由 `internal/opdef/registry.go` 的 `consumedEvents` 登记，并由 `eventRule`（运行时拒绝未登记事件）和 `registry_test.go`（目录双向校验）保证，不再只靠人工审查。注册表是公开目录这一侧的权威消费者清单；运行时 notify 消费者本身仍在 benchmark 源码中，不在本仓库的 `internal/`，两者的同步仍属审查约定。
