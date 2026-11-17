# v4 隐含约定实验：在既有 Action 上新增批量驳回

状态（2026-11-15）：**只有 harness 和人工/假 CLI 控制，真实模型样本为 0。** v1–v3 的计划、归档、评分以及 `benchmark/phase3/`、`tests/acceptance.py` 均未修改。

## 为什么做 v4

v3 队列 002 里两组 20 个候选全部 66/66，正确性没有拉开。pilot 001 中 manual 组的失败，来自 Oracle 检查了一条只存在于参考内核、契约里没写的约定（requestHash 公式）。由此得到的假设是：**当企业约定只存在于既有代码、没有写进任务说明时，平台能力可能带来正确性收益。** pilot 001 的问题在于那条约定连导出代码里都看不到。v4 要求每条隐含约定都能从导出给模型的代码中读出来，并单独计分，不与显式契约混在一起。

## 任务

起点是 v3 完成后的状态：`POST /purchases/bulk-approve` 已经带审计和幂等。任务是新增 `POST /purchases/bulk-reject`，把 submitted 驳回为 rejected。任务文本（`cohort.json` 的 `requirements`）写明了权限、全批原子性、响应 `rejectedIds`、同 key 重放/冲突、ActionRequest 的 operation/租户/用户/key/response、503 回滚重试、并发以及状态生命周期。它**不写**审计、requestHash、幂等作用域是否包含 operation、不合格目标的具体状态码。`test_phase4.py` 会检查任务文本和两组提示词都不含 `forbidden_in_task` 里的词（ApprovalAudit、审计、requestHash、Fingerprint、ledger、作用域等）。

两组的提示词相同，都要求“先读既有代码并遵循其约定”；唯一的差别是能力说明那一行。

## 分组

| 组 | 既有批量审批的实现 | 新增驳回时的工作 |
|---|---|---|
| go-manual | `internal/candidate/approval.go` 中手写的事务、审计、指纹和幂等（可编辑） | 自己写驳回，约定需要从既有代码推断 |
| go-action | 受保护的平台内核 `internal/action/action.go`：`Transition{Operation,From,To,Field}` + `Executor.Run`；候选只是用 `action.Approve` 调用一次 | 声明一个新 Transition 并注册路由 |

两组共享且字节一致的文件：`go.mod/go.sum`、`internal/app`、`cmd/server/main.go`（复用 phase3 模板）、多路由 `internal/protocol`、`internal/ledger`、`SCHEMA.sql`（v3 结构 + `benchmark/phase4/schema.sql`）和 TASK.md。差异只有 `internal/candidate/approval.go` 的起点，以及 action 组多出的内核文件（单测校验）。两组都只允许修改 `internal/candidate/**.go`。

沿用 manual/action 两组，但把内核从 v3 的专用 `BulkApprove` 改成通用的 Transition 内核，理由是：v4 要比较的是“约定写在可复用平台里”与“约定只存在于既有手写代码里”。如果 action 组的内核仍然只有 BulkApprove，两组都得手写驳回，差别只剩下“约定在受保护文件还是可编辑文件里”，与假设无关。代价是 action 组对这四条约定**按构造就会遵守**；实验真正要测的是 manual 组在自然条件下能否推断出这些约定，以及推断失败的频率。

## 四条隐含约定及其可推断来源

每条约定的来源都以 `(文件, 原文片段)` 的形式写在 `cohort.json` 的 `conventions` 中，单测会逐条确认这些片段确实存在于该组的导出文件里。

| 约定 | 内容 | manual 组可读到的来源 | 自然出错的方式 | Oracle 用例 |
|---|---|---|---|---|
| implicit-audit | 每个驳回目标在同一事务中写一条 ApprovalAudit（approvedBy=actor，operation=bulk-reject.v4，idempotencyKey=key，submitted→rejected）；审计写入失败时整批回滚 | approval.go 的注释 “Every status transition leaves one ApprovalAudit row per target.” 和 INSERT 语句；SCHEMA.sql 的 v4 段把 toStatus 放宽为 approved/rejected | 任务没提审计，就不写；或者在事务外写 | 审计行 2、审计故障 503 + 同 key 重试 1 |
| implicit-operation-scope | 幂等作用域包含 operation：同一 actor 在审批里用过的 key，不会阻塞或重放驳回 | SCHEMA.sql 的主键 `("tenantId","actorId",operation,key)`；approval.go 的查找条件 `operation=$3 AND key=$4` 和锁键。**没有注释**，只能从代码和结构推断 | 按任务文本的“同一 actor 同一 key”查询，不带 operation | 先审批后驳回、驳回重放各 1 |
| implicit-request-hash | `requestHash = ledger.Fingerprint("bulk-reject.v4", ids)`，保证对账任务接受驳回行 | approval.go 的注释 “Same digest as ledger.Fingerprint(opApprove, ids); reconciliation recomputes it.”；`internal/ledger` 的 `Verify` 按每行自己的 operation 重新计算 | 直接复用把 `opApprove` 写死在里面的 `fingerprint(ids)`，或者自创公式 | 2 目标、100 目标各 1 |
| implicit-uniform-409 | 缺失、跨租户、本人创建、已审批、已驳回的目标一律返回 409，不泄露存在性 | approval.go 的注释 “indistinguishable (409), so a response never reveals whether an ID exists.” | 缺失返回 404、跨租户返回 403 | 五种混合批次各 1、新 key 再次驳回 1 |

action 组的来源是内核里对应的代码和注释，ledger 与 SCHEMA 两组相同。这些约定都没有被数据库强制：不写审计、换一种指纹算法、查找时不带 operation、返回 404，数据库都照样接受。schema 与 FK 只约束“写审计时必须用什么格式”。

## 计分

每次验收共 **108 项 = 显式 95 + 隐含 13**，按组报告（`cohort.json` 的 `groups`）：

- 显式：驳回的功能 5、授权 3、原子性 5、输入 4、幂等 6、并发 3、故障回滚 1、生命周期 2；冻结的 v3 审批 Oracle 50 项（`benchmark/phase3/cohort_oracle.py` 原样复用，按块生成 ID）；原有 API 16 项。
- 隐含：上表四组，共 13 项。

隔离原则：显式用例只检查任务文本写明的效果，即 HTTP、Purchase 状态，以及 ActionRequest 的作用域行和 response；不检查审计、requestHash 和具体的 4xx 码。拒绝类用例要求“数据库全部不变”，这一点写在任务里。每个隐含用例只依赖一条约定。

`result.json` 中包含 `groups`、`explicit`、`implicit`、`explicit_passed` 和逐条约定的 `conventions`。summarize 每组报告 `passed_all`、`passed_explicit`、`conventions_followed`（逐约定计数）、`first_pass_rate` 与 `explicit_pass_rate`。任一 trial 未测量时，两个 rate 都为 null。`passed` 仍然要求 108 项全部通过；主要比较的是隐含约定的遵守率，并在显式全通过的条件下比较。

## 控制结果（真实 PostgreSQL 17.11，`docs/evidence/phase4-implicit-convention-controls-2026-11-15/`）

| 组 | 控制 | 显式 | 隐含 | 未遵守的约定 |
|---|---|---|---|---|
| manual | correct（手写参考） | 95/95 | 13/13 | — |
| manual | omit-audit | 95/95 | 10/13 | audit |
| manual | ignore-operation | 95/95 | 11/13 | operation-scope |
| manual | approve-hash | 95/95 | 11/13 | request-hash |
| manual | distinguish-404 | 95/95 | 7/13 | uniform-409 |
| manual | skip-completeness（显式负控制） | 89/95 | 8/13 | uniform-409 |
| manual | unchanged（显式负控制） | 74/95 | 0/13 | 全部 |
| action | correct（内核 Transition） | 95/95 | 13/13 | — |
| action | omit-audit / ignore-operation / approve-hash / distinguish-404（绕过内核手写） | 各 95/95 | 10、11、11、7 /13 | 各自只有目标约定未遵守 |
| action | wrong-field（显式负控制） | 80/95 | 5/13 | 全部 |
| action | unchanged（显式负控制） | 74/95 | 0/13 | 全部 |

- 两份参考 108/108。
- 8 个隐含负控制：显式全部通过，并且**恰好**只有目标约定失败（driver 断言的是集合相等）。
- 4 个显式负控制都被识别。
- 另有 2 次 `prepare → phase3 独立 worker（假 kiro-cli）→ restore → evaluate` 交接验收，均为 108/108。
- 完整分母、缺失伙伴组（rate=null）和重复结果拒绝都已检查。
- `python3 -m unittest discover -s benchmark/tests`：43 项通过，其中新增 phase4 共 9 项。
- driver 全程约 100 s。

这些都是人工或假 CLI 控制，不计入模型样本。

## 运行真实队列（尚未执行）

```bash
export PRIVATE=/abs/operator-owned/phase4-private W=/abs/poc-v4-cohort
python3 benchmark/phase4/ctl.py fixtures --output "$PRIVATE/fixtures.json"
python3 benchmark/phase4/ctl.py plan --engine kiro --model claude-opus-5.5 --reasoning-effort medium \
  --time-budget 600 --repetitions 10 --base d8a5c576cc3c293ee2da884f5c618958df407b51 \
  --fixtures "$PRIVATE/fixtures.json" --output "$W/plan.json"
python3 benchmark/phase4/ctl.py prepare --plan "$W/plan.json" --trial go-manual--001 --output "$W/kits/go-manual--001"
# 生成：沿用 benchmark/phase3/isolate.py（Seatbelt），deny 列表同 phase3/run_kiro_cohort.sh
python3 benchmark/phase3/isolate.py --kit "$W/kits/go-manual--001" --output "$W/results/go-manual--001" --deny ... --allow-write ...
python3 benchmark/phase4/ctl.py restore  --plan "$W/plan.json" --trial go-manual--001 --bundle .../candidate.bundle \
  --receipt .../receipt.json --manifest "$W/kits/go-manual--001/manifest.json" --candidate "$W/eval/go-manual--001"
python3 benchmark/phase4/ctl.py evaluate --plan "$W/plan.json" --trial go-manual--001 --candidate "$W/eval/go-manual--001" \
  --manifest ... --receipt ... --fixtures "$PRIVATE/fixtures.json" --output "$W/evaluations/go-manual--001"
python3 benchmark/phase4/ctl.py summarize --plan "$W/plan.json" --inputs "$W/evaluations" --output "$W/summary.json"
```

要求和 v3 相同：真实计划要求 harness 已提交且工作区干净；验收需要 `ADMIN_DATABASE_URL`、`DATABASE_URL`、`POC_DB_RESET=1` 和空闲的 `POC_HTTP_PORT`；退出码 0/2/3/1 分别表示通过、候选失败、基础设施阻塞、配置或完整性错误。worker 与 receipt 沿用 phase3 的 v3 格式，phase4 计划冻结 worker 的 hash。不提供同宿主的 `generate` 命令。

## 局限与未解决问题

- **按构造的优势。** action 组的内核实现了全部四条约定，因此 action 的遵守率是预期结果，不是发现。v4 能测出的只是 manual 组的自然违反率。即使 manual 违反得多，也只说明“把约定放进平台可以避免这些具体遗漏”，不能推出通用平台的收益。
- **约定由 harness 作者挑选。** 四条约定都是先验认为可能被违反的“陷阱”，存在选择偏差。它们在任何模型运行之前就已冻结；不能在看到结果后增删约定。
- **可推断强度不一致。** 三条约定有代码注释，operation-scope 只能从代码和结构推断。每条约定在每组只有一个起点，不能据此分析注释的作用。提示词里的“遵循既有约定”会提高 manual 组的推断率，这是有意保留的现实条件。
- action 组的隐含负控制是绕过内核的手写代码，只证明 Oracle 的识别能力与导出无关，不代表 action 组可能出现的自然错误形态。
- 显式原子性组对“未实现路由”的候选也会通过（fallback 返回 404 且数据库不变），单看这一组不能说明能力；功能组能识别。审批与驳回的并发用例每次运行只覆盖胜者的一个分支（取决于竞争结果）。
- request-hash 的 Oracle 是 `ledger.Fingerprint` 的 Python 独立实现。两者等价的证据是两份参考（使用 Go 版 ledger）都通过，Oracle 没有直接调用 Go 代码。
- 隔离条件与 v3 队列 002 相同：只有文件系统 Seatbelt，网络不受限，生成与验收在同一宿主；公开仓库里有本 Oracle 和参考控制，因此**不能称为盲测**。
- 本地 PostgreSQL 为 17.11，不是 CI 的 17.6。phase4 控制**尚未接入 public CI**（需要修改 `.github/workflows/poc.yml`，本次按约束只新增文件）。还没有 phase4 专用的 lane 脚本（`phase3/run_kiro_cohort.sh` 写死了 phase3 ctl）。

## 注册真实队列前的修订（2026-11-15）

根据 v3 队列 003 暴露的两个 harness 问题，在注册任何 v4 真实计划之前做了两项修订：

- 导出的 `.gitignore` 增加 `/server`。在 v3 中，模型执行 `go build` 后留下的二进制文件曾被判为作用域违规。
- 验收时，如果 Oracle 报告连接被关闭，同时候选进程已退出或日志中出现 `panic`，则判为 `candidate-error`，不再判为 infrastructure-blocked。

`driver_smoke.py` 新增了一项崩溃控制：一个驳回处理函数故意 panic，并在候选目录中放置一个游离的 `server` 文件。期望结果是判为 candidate-error，且 `server` 文件不算越界，实测结果符合预期。全部控制已重跑通过，结果见 `control-r2-crash-and-gitignore.json`。v4 的 lane 运行沿用 `benchmark/phase3/run_kiro_cohort.sh`，使用时设置 `CTL=benchmark/phase4/ctl.py`。
