# v4 Kiro 队列 001：隐含约定 × 四个模型

运行时间 2026-11-15 09:41–10:04（Asia/Shanghai）。harness 为 `5e36007`，含[注册前修订](phase4-implicit-convention.md#注册真实队列前的修订2026-11-15)。共四个独立队列，每个 10 块 × 2 组，四个队列并行运行，执行脚本为 `benchmark/phase3/run_kiro_cohort.sh`（`CTL=benchmark/phase4/ctl.py`）。隔离方式与 v3 队列 003 相同：Seatbelt 文件隔离，同时拒绝读取其他模型的目录和所有旧队列目录。

## 结果（冻结首次成绩）

| 模型 | 组 | 108 项全过 | 显式 95 项全过 | 结局 | 四条隐含约定遵守（已评分 trial） | 耗时中位 | credits 中位 / 合计 |
|---|---|---|---|---|---|---|---|
| claude-opus-5.5 | manual | 10/10 | 10/10 | — | 10/10 ×4 | 59.6 s | 2.12 / 21.5 |
| | action | 10/10 | 10/10 | — | 10/10 ×4 | 32.0 s | 1.24 / 12.7 |
| claude-haiku-4.5 | manual | **10/10** | 10/10 | — | 10/10 ×4 | 53.4 s | 0.66 / 6.7 |
| | action | **0/10** | — | **越界 10** | 未评分 | 52.3 s | 0.56 / 6.0 |
| gpt-5.6-luna | manual | 9/10 | 9/10 | 失败 1 | 9/10 ×4 | 45.4 s | 0.65 / 6.8 |
| | action | 10/10 | 10/10 | — | 10/10 ×4 | 34.2 s | 0.46 / 4.7 |
| qwen3-coder-next | manual | **9/10** | 10/10 | 失败 1 | audit/scope/409 10/10；**hash 9/10** | 62.3 s | 0.50 / 5.1 |
| | action | **0/10** | — | **越界 10** | 未评分 | 55.7 s | 0.58 / 5.4 |

80 个 trial 全部完成观察，没有未测量。按块配对的精确符号检验（事后、探索性）：

- haiku：10 块都是仅 manual 通过，p≈0.002；
- qwen：9 块仅 manual 通过，1 块两组都失败，p≈0.004；
- opus 和 luna 没有显著差异。

方向与 v3 队列 003 **相反**。

## 发生了什么

**1. 起点有完整的手写示例时，manual 组几乎都能遵守隐含约定。** v4 的 manual 起点是一份带注释、可用的批量审批实现，模型照着它写出驳回。四个模型的 manual 组共 40 次中，只出现 **1 次**自然违反隐含约定：

- qwen manual-004 复用了把 `opApprove` 写死在里面的 `fingerprint(ids)`，结果驳回行的 requestHash 按审批计算。这正是设计时预测的陷阱，起点注释其实已经写了 “Same digest as ledger.Fingerprint(opApprove, ids)”。

另一个 manual 失败与隐含约定无关：

- luna manual-009 在重构共享代码时，把 `"\x00"` 拼进了 advisory lock 的键，导致包括原有审批在内的所有请求都返回 503。

对照 v3：同样是这几个弱模型，在需要从零手写事务、审计和幂等时，manual 组只有 1–5/10。**这次的收益来自“可复制的既有实现”本身，而不是平台。**

**2. haiku 和 qwen 的 action 组 20/20 都修改了受保护的内核。** 改动完全相同：在 `internal/action/action.go` 中 `action.Approve` 的旁边加了一行 `var Reject = Transition{...}`。内核把唯一的 Transition 示例定义在它自己的包里，弱模型便把新的 Transition 也放到同一个位置，违反了“只改 internal/candidate”的约束。按冻结规则，这些全部记为 scope-violation，也就是已观察到的失败。opus 和 luna 则把声明放在候选包里，20/20 通过。

**3. 事后诊断（不改冻结成绩）。** `research/v4_relocate_diagnostic.py` 先检查每个越界 trial 对内核的改动是否恰好只有这一行声明，然后机械地把这行声明移到 `internal/candidate`，其余代码保持模型原样，再用独立的诊断 seed 运行同样的 108 项。结果：haiku 10/10、qwen 10/10，全部 108/108。也就是说，**这 20 份 action 候选在行为上全部正确，失败只在于把扩展写进了平台文件。**

## 解读

- **对假设的回答：**“约定只在既有代码里”本身并不足以让 manual 组失败，前提是既有代码是一个就在手边、可以照抄的完整范例。真实企业代码通常比这个起点更分散，这个 v4 起点可能低估了推断难度。
- **平台的真实风险：** 扩展点的位置是否显而易见，会直接影响较弱模型是否越界改动平台本身。这是一个需要在平台设计里正面解决的问题：比如在候选包里给出扩展示例、内核提供注册 API，或者由 CI/Verifier 直接拒绝。本实验的作用域检查恰好起到了这个拦截作用，没有让被改过的内核通过。
- **效率：** opus 的 action 组仍然约快一倍、credit 约省 40%；luna 也是 action 组更省。haiku 和 qwen 两组之间差别不大。
- **结论限于本任务和本起点：** 四条约定由 harness 作者选定，存在选择偏差；统计是事后选定的。

## 局限

- 隔离只覆盖文件系统，网络不受限，生成与验收在同一宿主，不是盲测。
- haiku 和 qwen 不支持 effort 参数；四个队列并行运行，耗时会受影响。
- 诊断使用了不同的 seed 和合成计划，只说明行为正确性，不是重新评分。

## 下一步建议

- **v4b：** 只改提示词或起点，在候选包中给出 Transition 的声明示例，或明确写“在 internal/candidate 中声明”。注册为新队列，用来检验越界是不是扩展点设计造成的。
- **更难的隐含约定：** 让约定分散在多个文件、没有可照抄的完整范例，例如约定只体现在对账任务或另一个 Action 里。

## 证据

`docs/evidence/phase4-kiro-cohort-001-models/<model>/` 包含：

- 冻结计划、汇总、执行顺序和状态日志；
- 每个 trial 的 manifest/job、原始 ACP 事件、receipt/patch/bundle 和逐项验收；越界 trial 另存 `scope-status.txt`、`scope-violation.diff` 和未跟踪文件；
- haiku 和 qwen 的 `relocation-diagnostic/`。

跨模型分析见 `analysis.json`。私有 seed 与认证均未入库。
