# v3 Kiro 队列 002：修正契约后的 10 块重复实验

运行时间 2026-11-15 05:18–05:40（Asia/Shanghai）。这是新注册的队列，与 [pilot 001](phase3-kiro-pilot-results.md) 和历史 Codex v1/v2 队列都分开报告，不合并计算。

## 与 pilot 001 的差别

- **契约修正：** pilot 001 暴露出 Oracle 校验了任务说明里没写的 `requestHash` 公式。本队列在 `cohort.json` 的任务要求中写明了这个公式，以及 `ActionRequest.response` 的形状，并核对过它与 Oracle 的 `fingerprint` 一致。Oracle 本身没有改动，两组提示词因此同步变化。
- **生成端隔离：** 每个 trial 的 worker 都在 macOS Seatbelt（`benchmark/phase3/isolate.py`）下运行。
  - 禁止读写：所有 evaluator checkout（`~/.paseo` 下的 worktree 以及 `~/cil/ainative/enterprise_agent`）、私有 seed、验收目录、运维日志、其他 trial 的包和结果、`~/.kiro` 与 `~/.codex`。
  - 允许写入：只有本包、本输出目录、CLI 和 Go 的缓存目录。
  - 控制：`isolation_control.py` 用真实 deny 列表验证了 evaluator 和 seed 不可读、HOME 不可写、包可写，并在沙箱内跑通了一次真实 Kiro READY（见 `isolation-control.json`）。
  - **网络未限制**：模型 API 需要网络，Seatbelt 也无法按主机过滤，所以公开仓库在网络上仍然可达。

## 冻结条件

- 计划：`docs/evidence/phase3-kiro-cohort-002/plan.json`。harness `5b1d430`，业务源 `d8a5c57`。
- 模型与引擎：engine kiro，kiro-cli 3.3.0，模型 `claude-opus-5.5`，effort medium，预算 600 秒。
- 规模与顺序：10 个重复块，共 20 个 trial；块内顺序由种子 20261115 打乱，严格按计划顺序串行执行（`order.txt`）。
- 执行方式：用 `run_kiro_cohort.sh` 逐个 trial 执行：隔离生成 → 恢复到新目录 → 私有 66 项验收。
- 反馈条件：两组都没有公共测试服务，模型可以运行 `go test`。

## 结果

| 组 | 计划 | 已观察 | 首次通过 | 未测量 | 生成耗时 中位（范围） | credits 中位（范围） | 新增行数中位（含测试） | 自写测试 |
|---|---|---|---|---|---|---|---|---|
| go-manual | 10 | 10 | **10/10** | 0 | 74.8 s（57.6–91.7） | 2.51（1.85–3.38） | 110 | 7/10 |
| go-action | 10 | 10 | **10/10** | 0 | 34.7 s（29.9–43.3） | 1.28（1.11–1.56） | 19.5 | 1/10 |

- **验收：** 20 份候选全部 66/66，没有出现 provider 错误、超时或作用域违规。每份结果都保存了逐项明细（`<trial>/evaluation.json`）。
- **模型确认：** 所有 trial 上报的模型都是 claude-opus-5.5。
- **credit 合计：** manual 25.47，action 13.06。
- **工具调用审计：** 每个 trial 4–14 次工具调用，都在本包内，0 次被沙箱拒绝。shell 命令中没有出现 curl/wget/git clone、外部 URL 或 `/Users/` 路径。全部 20 个 trial 都运行过 `go test`。

## 解读

- **正确性：** 在这一任务、这一模型上，**首次正确性没有差异**（10/10 对 10/10）。冻结的 66 项验收在两组都没观察到自然缺陷，不能据此说平台 Action 提升了正确性。
- **耗时与 credit：** 两组的范围完全不重叠，action 每一次都比 manual 更快、更省，中位约为 manual 的一半。这是同一任务下集成现成内核与手写事务的工作量差异，不能外推到其他任务或平台。credit 是 Kiro 的计量值，不等于账单；token 数未知。
- **代码量：** manual 的新增行数包含它更常写的测试，所以不能直接当作“业务代码量”比较。
- **与 pilot 001 的关系：** pilot 001 的 manual 失败已确认是 harness 缺陷，本队列修正契约后 manual 10/10。这说明那次失败来自契约，与能力无关；pilot 001 的冻结成绩保持不变。

## 局限

- 只有一个任务和一个模型，重复 10 次也只能说明这个场景。66 项全部通过意味着这套验收对该任务可能已接近“上限”，要区分两组，需要更难或更开放的业务演进任务。
- 隔离只覆盖文件系统，不覆盖网络；生成端和验收端在同一宿主、同一用户下运行，Seatbelt 也不是 VM 级隔离。不能称为确认性盲测。
- 本地 PostgreSQL 为 17.11（`postgres:17-alpine`）。`postgres:17.6` 在本机拉取时持续阻塞超过 30 分钟，与 CI 版本不同。环境为 macOS 15.7.7 arm64，Go 1.26.1。

## 证据

`docs/evidence/phase3-kiro-cohort-002/` 包含：

- 冻结计划、汇总（`summary.json`）、执行顺序、状态日志和运行脚本；
- 隔离控制结果；
- 逐 trial 分析（`analysis.json`，由 `research/analyze_kiro_cohort.py` 生成）；
- 每个 trial 的 manifest、job、预检与生成阶段的原始 ACP 事件、invocation、receipt、patch、bundle 和逐项验收结果。

私有 seed、Kiro 会话目录和认证均未入库。
