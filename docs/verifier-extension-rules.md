# 扩展 Verifier 规则与拦截率（2026-11-15）

`appctl extensions` 对一个候选 checkout 做静态检查：拿工作区（含已跟踪改动和未跟踪文件）和可信基线提交比较，输出 JSON。没有发现时退出码为 0，有发现时为 1，无法检查时为 2（例如 base 无效）。它不编译、不运行候选，也不连数据库。实现见 `internal/extcheck`，单测见 `internal/extcheck/extcheck_test.go`。

```bash
bin/appctl extensions --root <candidate> --base <initial_sha>
```

在 280 个已归档的真实候选（v4、v4b、v5、v5b）上做事后评估：所有规则合起来拦住了 97/120 个冻结失败，误报 0（在 160 个通过的候选上）。显式契约全部通过、只违反隐含约定的失败共 75 个，规则补上了其中 63 个。剩下 12 个都属于“统一拒绝”，静态规则没有覆盖。**冻结成绩没有改动。** 这些规则是对着同一批数据调过的，所以这些数字不是留出集上的估计，详见文末“局限”。

## 规则

| 规则 | 场景 | 发现码 | 检查内容 |
|---|---|---|---|
| extension-scope | 通用 | `PROTECTED_PATH_CHANGED`、`DECLARATION_OUTSIDE_EXTENSION` | `git diff --name-only <base>` 加上未跟踪文件，凡不在 `internal/candidate/` 下的都报告。凡是基线里没有、又不在 candidate 包中的 `action.Operation`/`Transition` 字面量（包括内核包里不带包名的 `Transition{...}`），也单独报告 |
| operation-declaration | 声明式 | `FIELD_MISSING`、`FIELD_EMPTY`、`FIELD_NOT_CONSTANT`、`NAME_UNVERSIONED`、`OPERATION_NAME_DUPLICATE`、`PARSE_ERROR` | 用 go/ast 找出所有 `action.Operation`/`Transition` 复合字面量。按键名写的和按位置写的都能识别，位置写法的字段顺序从内核 struct 定义读取。**新声明**的必填字段必须存在、是字符串常量（字面量、包级 const 或 `action.NoEvent`）且不为空：Operation 要求 Name/Role/From/To/Event，Transition 要求 Operation/From/To/Field。名称必须匹配 `^[a-z][a-z0-9-]*\.v[0-9]+$`，并在整个模块内唯一 |
| event-consumer | 声明式 | `EVENT_NO_CONSUMER`、`EVENT_EXPECTED_BY_CONSUMER` | 新声明的 Event 如果不是 NoEvent，就必须出现在 `internal/notify` 某个 switch 的 case 字符串里。如果新声明写了 NoEvent、`"none"`、空串或者干脆没写，而 notify 中存在一个“以该操作命名”的事件（见下节），就报告“声明不发事件，但消费者期待事件” |
| manual-event-consumer | **手写代码** | 同上两个码 | 只在 candidate 包直接写 `INSERT INTO "ActionRequest"` 时启用。新操作名来自包内带版本后缀的 const，以及 ActionRequest 写入中的常量参数。如果 notify 有以它命名的事件，包内就必须有一条写入 `"PurchaseEvent"` 的语句带上这个事件（写在 SQL 文本里或作为常量参数都可以）。手写写入的事件如果不在 notify 中，也报告 |
| manual-fingerprint | **手写代码** | `FINGERPRINT_OPERATION_MISSING`、`FINGERPRINT_EXTRA_INPUT` | 每个新增或改动过、并且写 ActionRequest 的函数，都必须调用 `ledger.Fingerprint(...)`，或者调用包内某个 helper，来计算 requestHash。这个调用要收到与写入相同的操作参数（同一个标识符或选择器，或者解析后相同的版本化常量）；如果调用的 helper 本身不接收操作参数，它的函数体里必须引用这个操作常量。除操作外，其余参数不能包含 Idempotency-Key 或 reason。key 和 reason 按 handler 签名的位置识别：第 3 个参数是 key，单目标 handler 的第 5 个参数是 reason。与基线逐字相同的函数不检查 |

“新声明”是指名称在基线中不存在的声明。基线里的旧声明不受约束，所以 v5 的 `bulk-approve.v3` 没写 Event 不会被报告，v4 manual 起点里手写的 `fingerprint(ids)` 也不会。operation-declaration 要求所有新操作都显式写出 Event，这比 v5 内核本身更严格（v5 内核允许 Event 为空），是在 Verifier 层强制做到 fail-loud。

### “以操作命名的事件”的匹配规则

匹配是通用的，没有为 reopen 写死。

1. 取操作名的动词：去掉 `.vN` 后缀，再取最后一个 `-` 之后的部分。例如 `bulk-reject.v4` 得到 `reject`，`reopen.v5` 得到 `reopen`。
2. 取事件最后一个 `.` 之后的部分，判断它是否是这个动词按英语规则变化的过去分词：
   - 直接加 `ed`：`reopen` → `reopened`
   - 词尾已有 `e`，只加 `d`：`approve` → `approved`
   - 双写末尾辅音再加 `ed`：`submit` → `submitted`

理由如下：

- 消费者代码里的 case 是现有的、可执行的契约，比提示词可靠。这个仓库里事件一直用 `<聚合>.<过去分词>` 命名（`purchase.rejected`、`purchase.reopened`），与操作名一一对应。
- 只有“新操作选择不发事件”与“某个消费者 case 的名字和它对上”同时成立时才报告。所以 `bulk-approve.v3` 写 NoEvent 不会被报告，因为没有 `purchase.approved` 这个 case；新增一个 `cancel.v5` 写 NoEvent 也不会被报告（单测覆盖）。
- 不规则动词匹配不上，例如 `pay` 和 `paid`。这时只会漏报，不会误报。另一个方向的检查（非 NoEvent 的事件必须有消费者）不依赖命名，完全确定。

## 拦截率（事后，280 个真实候选）

复现方式：`python3 research/verifier_extension_rules.py`。产物在 [evidence/verifier-extension-rules-2026-11-15](evidence/verifier-extension-rules-2026-11-15/)：`summary.json` 是汇总，`trials.json` 是逐 trial 的发现与冻结真值。

- **候选的恢复方式**：251 个从 `candidate.bundle` 恢复，并以 manifest 中的 `initial_sha` 作为 base。29 个越界 trial 用计划的导出（`ctl.files`）重建，逐文件 hash 与计划里的 `exports` 一致，然后打上 `scope-violation.diff`，再放回归档的未跟踪源码。其中 v5b qwen manual-007 的未跟踪文件 `reopen.go` 没有归档，所以这个 trial 只能用于作用域检查。
- **v4b 证据的来源**：v4b 证据不在本分支，脚本用 `git archive d2f9a25` 读取。
- **真值的定义**：extension-scope 以冻结结局是否为 `scope-violation` 为真值。其他规则以各自针对的冻结隐含约定为真值：event 类规则对应 `implicit-outbox`，指纹规则对应 `implicit-request-hash`。没有约定分数的 trial（越界、candidate-error，以及 v4 没有 outbox 约定）不计入。

### 每条规则在其适用组上的结果

| 规则 | 组 | TP | FP | FN | TN | 不计入 |
|---|---|---:|---:|---:|---:|---:|
| extension-scope | 全部 280 | **29** | 0 | 0 | 251 | 0 |
| | 其中 v4 action / v4b action / v5b（两组） | 20 / 7 / 2 | 0 | 0 | | |
| operation-declaration | v5 action | **27** | 0 | 0 | 12 | 1 |
| | v5b action | 0 | 0 | 10 | 28 | 2 |
| event-consumer | v5 action | **27** | 0 | 0 | 12 | 1 |
| | v5b action | **10** | 0 | 0 | 28 | 2 |
| manual-event-consumer | v5 manual | **7** | 0 | 0 | 31 | 2 |
| | v5b manual | **7** | 0 | 1 | 30 | 2 |
| manual-fingerprint | v4 manual | 1 | 0 | 1 | 38 | 0 |
| | v4b manual | 0 | 0 | 0 | 20 | 0 |
| | v5 manual | **7** | 0 | 0 | 31 | 2 |
| | v5b manual | **9** | 0 | 0 | 29 | 2 |

所有规则在所有组上（包括不适用的组）都是 FP=0。各组的完整混淆矩阵见 `summary.json` 的 `rules.*.by_cell`。

- **v5 action 组漏写 Event**：共 28 个声明没写 Event（26 个缺字段，2 个写了 `Event: ""`），都被 operation-declaration 和 event-consumer 同时报告。其中 27 个有分数，另 1 个是 candidate-error。
- **v5b 明确选错**：event-consumer 共报告 12 个声明。qwen 9 个写了 NoEvent 或 `"none"`（其中 7 个有分数，1 个越界，1 个 candidate-error），haiku 和 luna 各 1 个写了 NoEvent，haiku 还有 1 个写成 `purchase.resubmitted`（`EVENT_NO_CONSUMER`）。operation-declaration 在 v5b 上 FN=10 符合预期：内核已经要求必填，剩下的失败都是“填了但填错”，要由 event-consumer 兜住。
- **漏报**：manual-event-consumer 在 v5b 漏掉的 1 个（qwen manual-005）里，事件语句确实存在，但整个 reopen 的功能都是 0 分，outbox 失败是连带的。manual-fingerprint 在 v4 漏掉的 1 个（luna manual-009）是 advisory lock 的键错误，导致所有请求都返回 503，hash 约定失败同样是连带的。从静态角度看，这两个候选“没有违反该约定”。

### 原本漏掉的失败（显式契约全过、只有隐含约定失败）

| 组 | 这类失败数 | 被任一规则拦住 |
|---|---:|---:|
| v4 manual | 1 | 1（指纹：复用了写死 opApprove 的 helper） |
| v5 action | 27 | **27**（漏写 Event） |
| v5 manual | 25 | 13（只有指纹 6、只有事件 6、两者都有 1）；剩下 12 个全是统一拒绝 |
| v5b action | 10 | **10**（NoEvent 或事件名错误） |
| v5b manual | 12 | **12**（只有指纹 6、只有事件 4、两者都有 2） |
| 合计 | 75 | **63** |

与“任一冻结失败”相比，所有规则合起来是 TP 97 / FP 0 / FN 23 / TN 160。这 23 个 FN 都在 manual 组：12 个是统一拒绝，11 个是显式行为失败（并发、幂等、崩溃），这些本来就由验收测试负责。

## 控制

`python3 tests/extension_controls.py` 从 v4、v5、v5b 冻结导出的真实起点出发，套用 phase 控制模块中的人工候选，再加上复现真实失败的小改动，共 44 个控制。每个控制都断言**发现码集合完全等于预期**，并且候选能编译通过：

- 24 个负控制被拦住，例如内核里的 `var Reject`、省略 Event、NoEvent、字面量 `"none"`、未知事件、非常量 Event、缺 Role、内核里的 Reopen、reason 进 hash、`approve-hash`、手写代码漏发事件、操作名写成字面量、改 protocol。
- 7 个正控制没有发现。
- 13 个“已知不覆盖”的坏候选保持没有发现，包括 own-false、漏写审计、区分 404 和 409、未实现。这样做是为了明确 Verifier 没有宣称能查出这些问题。

结果见 [controls.json](evidence/verifier-extension-rules-2026-11-15/controls.json)。

## CI

- verifier job 运行 `go test -race ./...`，其中包括 extcheck 单测，然后运行上面的 44 个控制，以及 280 个候选的拦截率评估。`scripts/report.py` 要求评估结果与入库的 `summary.json` 完全一致。
- 新增的 cohort-controls job 使用 PostgreSQL 17.6，并先执行 `npm ci --prefix apps/zenstack`（`db.mjs` 需要从这里引入 pg）。随后依次运行 `benchmark/phase4|5|5b/driver_smoke.py`。
- `report.py` 会检查每个 `control.json` 的计数：14 个人工候选、2 次 worker 传输、参考答案通过，以及隔离的隐含违反数和显式违反数。其中 v5b 的 omit-event 必须是显式失败。上述任一文件缺失都会导致报告失败。

## 局限

- **调参与评估用的是同一批数据。** manual-fingerprint 的第一版要求每个写入函数都直接调用 `ledger.Fingerprint`，在 v4 manual 上产生了 57 个 FP，原因是 v4 起点自带手写的 `fingerprint(ids)` helper。之后看了数据才加入三项修正：不检查与基线相同的函数、识别 helper、接受选择器（`d.op`）和字面量形式的操作名。manual-event-consumer 也是看过数据后，才开始识别字面量操作名（v5 qwen manual-009）。声明式规则和作用域规则第一次运行就是现在的结果。因此这里的数字是“在本数据上的拟合程度”，新任务上需要用预注册的队列重新测量。
- **只看语法和常量。** 事件或操作名一旦经过变量、函数返回值或反射传递，规则就看不到，只能漏报，或者报 `FIELD_NOT_CONSTANT`。手写规则只追踪一层 helper。有人刻意规避时很容易绕过，因此它只适用于可信代码模式下的辅助防线，不是安全边界。
- **统一拒绝、审计、事务、并发、幂等语义都没有覆盖。** 它们需要数据流分析或运行时验收。v5 中这类问题（opus manual 0/10）都不会被拦住。
- 事件命名匹配依赖规则的过去分词和 `<聚合>.<事件>` 约定。必填字段表按内核类型写在 `extcheck.Default` 里；内核演进时要同步更新。
- 作用域只允许改 `internal/candidate/`，这是本实验的约定，不是通用的发布规则。Verifier、规则和 CI 与候选代码在同一个仓库里，治理边界和 README 中的“信任边界”一节相同。
