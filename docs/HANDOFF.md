# Enterprise Agent 技术 POC 完整交接文档

面向接手工具：Kiro CLI。状态核对：2026-11-14，Asia/Shanghai。

仓库：<https://github.com/XUJiahua/enterprise_agent>。

本文的代码/实验基线是 **`5bd655e1270e887262b1517ee7642ef15ef1bf59`**：原有 #1、#2、#3、#7、#8、#9、#10、#11、#12、#13 已全部合入 main。原始提交历史保留，合并后代码树与 #13 的 `8831744718f09cb2af9d20f651fa1b3b2ae02039` 相同。本文通过后续文档 PR 交付；接手时以最新 `origin/main` 为起点，并记录实际 HEAD。

## 1. 接手摘要：先做什么

**POC、真实模型小样本试验和 v3 实验工具已经存在，不需要重新搭建一遍平台。下一步优先完成 v3 的真实模型 pilot，以及生成/验收环境的实际隔离。**

目前已经完成：

- Go、ZenStack V3、Go + Policy/RLS 三路径采购审批、共享 Next.js UI、独立验收、故障注入与 public CI。
- 真实 Codex 生成：早期三路径预试验 3 个候选、三任务完整队列 9 个候选、四组原子审批队列 4 个候选；三个队列分别报告，均首次验收通过。
- 既有候选的独立审计、bundle 重放、追加边界测试与机制研究。
- 审批状态、审计日志、幂等结果同事务提交的领域 Action 原型。
- “手写事务 vs 调用平台 Action”v3 的冻结计划、最小源码导出、独立生成包、bundle/receipt 交接、66 项验收和完整分母汇总。

当前还没有：

- **真实 v3 模型样本或 v3 模型收益结论。** 最近 Codex Work 环境的真实 CLI 预检在初始化阶段报只读文件系统错误，没有进入模型回合。
- 已部署并验证的生成/验收主机与网络隔离、确认性隐藏 Oracle 实验。
- Kiro CLI 模型生成后端适配器；现有生成器调用并解析的是 Codex CLI。
- 通用企业应用平台、完整业务 DSL、生产身份系统、不可信扩展沙箱。

建议接手顺序：读本文 → 验证 main/环境/回归 → 确认由 Codex 生成还是新增 Kiro 后端 → 完成一块两组真实 v3 pilot → 分析失败机制 → 再设计重复样本与后续平台能力。

## 2. 技术 POC 的目标和已接受的设计方向

最初问题是：AI Coding 在已经沉淀企业最佳实践的低代码平台上开发，是否会更稳定？研究收敛为可验证的工程问题：

> 在相同业务要求下，额外开发流程指导、确定性权限护栏，以及可复用事务化 Action，分别能改善哪些错误？收益能否通过真实模型候选和独立验收重复观察？

当前方向是 **Agent-Native Enterprise Guardrails / Application Kernel + Deterministic Verifier**：保留 Go、PostgreSQL、Next.js 和 Git/CI 工作流，把必须全局成立的约束变成机器可执行的检查与运行边界。先证明收益，再决定是否扩展为完整低代码平台。

已经采用的取舍：

| 领域 | 当前选择 | 当前边界 |
|---|---|---|
| 业务与数据 | 采购申请、审批、多租户；PostgreSQL + pgx | 闭合的领域 POC |
| 最小策略 | `policies/purchase.json` → `generated/guard.sql` | 有限语法、RLS、列权限；不是通用 DSL |
| 验证器 | `appctl compile/check/plan/verify` | 生成漂移、权限扩大、迁移锁和保守 SQL 子集 |
| 业务 Action | Go 类型化 `Executor.BulkApprove` | 单数据库事务内的审批/审计/幂等 |
| 前端 | 共享 Next.js UI、真实 Chromium 验收 | 固定 demo 身份，尚未接入新 Action |
| 交付 | 分支/worktree、PR、public GitHub Actions | 默认不直接 push main；合并遵循用户授权 |
| 实验 | 冻结输入、独立 Oracle、正反控制、完整分母 | 仍需实际隔离部署与更强研究设计 |

不要优先扩展拖拽编辑器、自研 ORM 或通用工作流引擎。也不要通过减少对照组需求、弱化验收或人工修复后覆盖首次结果来制造差异。

## 3. 进展与证据：四类结果必须分开

### 3.1 运行机制与工具控制

| 阶段 | 已验证结果 | 主要资料 |
|---|---|---|
| 首轮三路径 | 健康业务各 16/16；人工缺陷阻断 baseline 0/5、ZenStack 和 guarded 各 5/5 | [首轮报告](poc-results.md)、[实验设计](experiment-design.md) |
| 其他基础控制 | Verifier 7/7、直接 SQL 6/6、错误启动 2/2、三路径浏览器合计 18/18 | README、CI artifact |
| v2 工具控制 | 12 次人工候选，各 46 项；4 个正确参考通过，8 个负控制被发现；另有假 CLI 链路控制 | [v2 指南](phase2-atomic-bulk.md) |
| Action 机制 | 参考 66/66，5 个负控制被发现；新元数据 SQL 12/12、启动/开关 7/7 | [Action 设计](transactional-action.md) |
| v3 工具控制 | 6 次人工候选 + 3 次假 CLI/交接验收，每次 66 项；2 个正确参考通过、4 个负控制被发现、3 次 bundle 恢复通过 | [v3 指南](phase3-action-evolution.md)、[控制证据](evidence/phase3-cohort-controls-2026-11-14/controls.json) |
| 当前轻量测试 | Python 28 项；Go race test、vet 均通过 | `.github/workflows/poc.yml` |

人工缺陷注入、假 CLI、参考实现、事后追加测试都不属于新的真实模型样本。负控制“被发现”是控制成功，不是缺陷版本通过业务验收。

### 3.2 已完成的真实模型队列

| 队列 | 条件 | 真实候选数与首次结果 | 解释 |
|---|---|---|---|
| v1 初始预试验 | 一个读任务 × 三路径 | 3/3 | ZenStack 依赖准备有已披露差异，单列报告 |
| v1 完整队列 | 三个读任务 × 三路径 | 9/9，原始验收合计 195 项 | 各路径样本很少，简单任务未区分收益 |
| v2 原子批量审批 | 额外流程指导 × RLS Runtime，四组各一次 | 4/4；每份 46/46 | 无人工修复/候选重试，未观察到首次正确性差异 |
| v3 业务演进 | 手写事务 vs 平台 Action | **0，尚未注册/执行真实队列** | 仅控制通过，真实 CLI 预检阻塞 |
| v3 Kiro 队列（2026-11-15） | 同上，引擎 kiro-cli 3.3.0 / claude-opus-5.5 | 2 个；manual 50/66 失败，action 66/66 | manual 失败全部来自契约未规定的 requestHash 公式；放宽该项的诊断两组均 66/66。见 [Kiro pilot](phase3-kiro-pilot-results.md) |
| v3 Kiro 队列 002（2026-11-15） | 契约写明 requestHash；Seatbelt 文件隔离；10 块 | 20 个；manual 10/10，action 10/10，每份 66/66 | 正确性无差异；action 耗时与 credit 约为一半，范围不重叠。见 [队列 002](phase3-kiro-cohort-002-results.md) |
| v3 Kiro 队列 003（2026-11-15） | 同 002，换 haiku-4.5 / gpt-5.6-luna / qwen3-coder-next，各 10 块并行 | 60 个；manual 5/10、3/10、1/10，action 8/10、10/10、6/10（qwen 各 1 未测量） | **首次出现正确性差异**，集中在幂等/并发；失败均为拒绝过度、无越权写。配对检验为事后探索性。见 [队列 003](phase3-kiro-cohort-003-results.md) |
| v4 Kiro 队列 001（2026-11-15） | 隐含约定 bulk-reject；opus/haiku/luna/qwen 各 10 块 | 80 个；manual 10、10、9、9/10，action 10、**0**、10、**0**/10 | manual 照抄既有实现几乎都遵守约定（仅 1 次自然违反 hash）；haiku/qwen action 20/20 把 Transition 写进受保护内核而越界，事后移位诊断 20/20 行为正确。见 [v4 队列 001](phase4-kiro-cohort-001-results.md) |
| v4b Kiro 队列（2026-11-15） | 扩展点提示 | haiku/qwen action 7/10、6/10（v4 为 0/10） | 见 PR #19 / docs/phase4b-kiro-cohort-001-results.md |
| v5 Kiro 队列 001（2026-11-15） | 分散约定 reopen；四模型各 10 块 | 80 个；全过 manual 0/2/3/3，action 10/2/0/0（opus/haiku/luna/qwen） | 内核封装的约定 action 117/117 遵守、manual 有自然违反；未封装的 outbox（可省略 Event 字段）action 弱模型 2/29。见 [v5 队列 001](phase5-kiro-cohort-001-results.md) |
| v5b Kiro 队列（2026-11-15） | Event 必填（NoEvent）+ 统一拒绝线索去冲突 | 80 个；全过 manual 9/3/4/5，action 10/8/9/1 | 必填字段让 haiku/luna outbox 2/10、0/10→8/10、9/10，qwen 改为主动选 NoEvent；opus manual 统一拒绝 0→10/10。见 [v5b](phase5b-kiro-cohort-001-results.md)；**全部实验汇总见 [experiment-summary-2026-11-15.md](experiment-summary-2026-11-15.md)** |
| **v6 确认性（预注册，2026-11-16）** | T1 reopen / T2 countersign / T3 reassign × 4 模型 × 10 块；网络受限 | 240 个；115 个可配对块 | H1、H2、H3 均成立（Holm）；A 持平；需要声明的事件字段 action 0.39；Verifier 样本外 FP 11 / FN 38。见 [v6 结果](v6-confirmatory-results.md) |
| **v7 确认性（预注册，私有 Oracle，2026-11-16）** | 2 个未公开任务 × 3 组（手写 / 手写 + 目录文档 / 内核按目录执行）× 4 模型 × 10 块；生成与验收分主机 | 240 个；A：55% / 76% / 99% | H1、H2、H3 均成立；credit/正确 2.32 → 0.81；手写组 double_apply 8 个 trial，opdef 组 0；Verifier 样本外误拦 0/184。见 [v7 结果](v7-confirmatory-results.md) |
| **v8b 确认性（预注册，2026-11-17）** | 3 个更开放的未公开任务 × manual/given × 3 模型 × 8 块 | 144 个；A：46% → 97% | H1 37:0，p≈7×10⁻¹²；每个正确结果的 credit 4.51 → 1.16。见 [v8b](v8b-confirmatory-results.md) |
| **v8a 确认性（预注册，两阶段，2026-11-17）** | 2 个未公开任务 × manual/given/drafted/drafted-review × 3 模型 | 288 个；A：60/98/75/92% | H1（drafted-review 不劣于 given）不成立，差 −7.3 pp；H2、H3 成立；错误全部在 event 字段。见 [v8a](v8a-confirmatory-results.md) |
| **v9 确认性（预注册，2026-11-17）** | 2 个未公开任务（hold / bulk-project）× given / drafted-norule / drafted-rule × 3 模型 × 16 块；事件规则 R1、R2 和消费者注册表 | 288 个；A：96 / 70 / 97% | H1 成立（drafted-rule 不劣于 given，+1.0 pp，区间 [−4.6, 7.0]）；H2 27:1；event 错误 28 → 0；382.8 credit。见 [v9 结果](v9-confirmatory-results.md) |

已完成真实生成合计为 3 + 9 + 4 = 16 个候选；条件不同，**不能合并计算成一个统一处理条件的成功率**。早期 9 个 trial 的环境阻塞计划仍保留为未测量，不是 0/9 通过。

真实队列详见 [v1 报告](model-pilot-results.md)、[v2 报告](phase2-model-pilot-results.md)。历史 CLI 使用 0.166.0，请求 `gpt-6.1-sol`，medium；v1 每项 120 秒，v2 每项 180 秒。实际服务端不可变模型快照和账单费用未知。usage 使用原始上报值，缓存输入是总输入的子集，不重复相加。

### 3.3 重放与人工干预得到的认识

- v1 九份既有候选：139 个归档文件核验，195 项冻结验收重放、45 项追加验收均通过。人工移除新汇总接口的租户条件后，普通 Go 泄露跨租户统计，RLS/ZenStack 仍隔离。见 [后续研究](research-followup.md)。
- RLS 会过滤无权行，但不会自动保证批量请求的“所有目标成功，否则全部不变”；必须在同一事务中检查完整目标集合并回滚。
- v2 四份既有候选：50 个归档文件核验，新公开 fixture 重放 184/184、追加边界 28/28，四份候选自写 Go 测试通过。见 [四组审计](atomic-pilot-followup.md)。
- 四组都自发写过测试，所以 ordinary 的含义是“无额外流程提示”，不是“没有测试”。
- 100 目标的锁查询形状有差别；70 KiB 空白 JSON 的容量行为也有差别。这些是具体候选观察，不是框架性能结论；原 v2 契约未规定的边界没有事后改判原成绩。

### 3.4 能支持的结论与尚不能支持的结论

现有证据支持：所列权限护栏可以阻断所列人工实现缺陷；领域 Action 能提供已验收的事务、审计与幂等边界；独立 Oracle 能识别正常路径测试可能漏掉的部分提交与故障回滚问题。

尚不能推出：总体 AI 缺陷率降低多少、某技术路径普遍更快/更便宜、流程无效/有效、通用平台成熟，或者任意不可信代码不能绕过权限。真实样本都通过，当前主要通过率指标尚未产生可比较差异。

## 4. 当前运行架构与接口归属

三路径共用业务结构与独立验收：baseline 在 Handler 手写授权；guarded 使用同一 Go Handler，并依赖 RLS/列权限和事务上下文；ZenStack 3.10.2 使用 ZModel + PolicyPlugin。共享 UI 通过本地代理访问选定后端。

| 入口/产物 | 当前实际能力 | 接手时不要误解 |
|---|---|---|
| `cmd/server` | 原有采购单列表/详情/创建、单条审批；baseline/guarded | 合并候选归档没有把读扩展或批量审批补丁应用到这里 |
| `apps/zenstack` | 原有对应 API | 真实模型的扩展保存在候选归档 |
| `apps/web` | 创建、单条审批、demo 身份切换、租户展示 | 尚未迁移到新 Action 的审计/幂等协议 |
| `cmd/action-server` | 新 `POST /purchases/bulk-approve`；其他接口委托原服务 | 新事务保证仅覆盖该 Action |
| `benchmark/phase3/source/*.go.txt` | 两组相同起点和 HTTP 适配器的导出模板 | `.txt` 是实验模板，不是主应用已部署的新代码 |
| `docs/evidence/**/candidate.bundle` | 已提交的模型候选与历史 | 需要按对应计划恢复和验收 |

Action 原型在同一个 PostgreSQL 事务中更新 `Purchase`、插入每目标 `ApprovalAudit`、保存完成态 `ActionRequest`。作用域为 operation + tenant + actor + key；同 key 同规范化集合重放，同 key 不同集合冲突；完整目标锁定与更新行数检查；两种受控 INSERT 故障均要求三表不变，故障移除后同 key 可重试。

HTTP 约束已在 v3 明确：严格 JSON、1..100 个不同 ID、每项 UTF-8 至多 100 字节；body 至多 65,536 字节；必需单个合法 `Idempotency-Key`；成功返回排序后的 `approvedIds` 与 replay header。完整定义见 `benchmark/phase3/contract.json`。

两张元数据表启用强制 RLS 和追加权限，但 SQL 目前手写，尚未接入旧 Policy 编译/语义审批管线。幂等没有 TTL 清理；网络/提交确认丢失时，客户端以同 key 重试，错误响应不能证明数据库未提交。当前不涉及外部消息投递或跨系统 exactly-once。

## 5. 代码和证据地图

| 路径 | 用途 |
|---|---|
| `internal/app/`, `cmd/server/` | 原有 Go 服务、可信上下文、固定身份 |
| `internal/policy/`, `cmd/appctl/` | 有限策略语法、RLS 编译、验证、语义差异与迁移锁 |
| `policies/purchase.json`, `generated/guard.sql`, `db/` | 策略、生成 SQL、结构/迁移/公开 fixture |
| `internal/action/`, `cmd/action-server/`, `poc/transactional-action/schema.sql` | 新 Action 与元数据 SQL |
| `apps/zenstack/`, `apps/web/` | ZenStack 路径、共享前端与 Playwright |
| `tests/acceptance.py`, `tests/verifier.py`, `scripts/` | 独立业务 Oracle、Verifier 对抗、故障/浏览器/汇总控制 |
| `benchmark/benchctl.py`, `benchmark/codex_runner.py` | v1 计划、Codex 预检/生成与验收 |
| `benchmark/phase2/` | 四组 Go、46 项原子批量审批实验 |
| `benchmark/phase3/ctl.py`, `cohort.json` | 两组能力对照、冻结/导出/恢复/验收/汇总 |
| `benchmark/phase3/generation_worker.py` | 不依赖仓库导入的独立生成 worker |
| `benchmark/phase3/oracle.py`, `cohort_oracle.py`, `db.mjs` | 冻结机制 Oracle、按块 fixture、管理员侧 SQL/故障 helper |
| `benchmark/phase3/smoke.py`, `driver_smoke.py`, `controls.py` | 机制控制、交接控制、人工参考；不是模型结果 |
| `docs/evidence/model-pilot-success-2026-11-14/` | v1 两个已执行队列、原始事件、补丁/bundle、验收 |
| `docs/evidence/phase2-model-pilot-2026-11-14/` | v2 四组真实计划、usage、候选、验收和 checksum |
| `docs/evidence/research-followup-2026-11-14/`, `atomic-followup-2026-11-14/` | 审计、重放、追加检查与人工干预 |
| `docs/evidence/transactional-action-2026-11-14/` | Action 机制结果 |
| `docs/evidence/phase3-cohort-controls-2026-11-14/` | v3 控制、源码 hash 和最近真实预检错误 |
| `.github/workflows/poc.yml` | public CI 的权威完整执行顺序 |

旧 [Issue #4](https://github.com/XUJiahua/enterprise_agent/issues/4) 已关闭，其正文停留在早期状态，不能作为当前进展依据。[Issue #5](https://github.com/XUJiahua/enterprise_agent/issues/5) 和 [Issue #6](https://github.com/XUJiahua/enterprise_agent/issues/6) 仍开放；部分能力后来已实现，剩余缺口见第 10 节。

## 6. 开发环境与第一轮回归

### 6.1 从 main 开始工作

```bash
git clone https://github.com/XUJiahua/enterprise_agent.git
cd enterprise_agent
git fetch origin
git worktree add -b feat/phase3-next ../enterprise-agent-phase3-next origin/main
cd ../enterprise-agent-phase3-next
git rev-parse HEAD
git status --short
```

使用独立 worktree/分支和 PR；不要覆盖历史证据。先确认 Git、make、Go 1.25+、Node 24、Python 3.12+、可销毁 PostgreSQL 17+、必要 npm 依赖与 Chromium。CI 固定 PostgreSQL 17.6、Node 24.11.1，Go 依据 `go.mod`。本地已验证过 PostgreSQL 17.6 和其他报告所列环境，不能把环境差异悄悄抹掉。

### 6.2 可销毁数据库与依赖

以下账号口令只是测试 fixture。使用全新 `_poc` 数据库；reset 会删表重建，不能连接业务数据。同一数据库和端口下各阶段顺序运行。

```bash
docker run --rm --name enterprise-agent-handoff-poc \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=enterprise_agent_poc \
  -p 127.0.0.1:55434:5432 postgres:17.6
```

另一终端（同一 worktree）：

```bash
export ADMIN_DATABASE_URL=postgres://postgres:postgres@127.0.0.1:55434/enterprise_agent_poc
export DATABASE_URL=postgres://app_runtime:poc-only@127.0.0.1:55434/enterprise_agent_poc
export POC_DB_RESET=1
export POC_HTTP_PORT=18084

make deps test
python3 -m unittest discover -s benchmark/tests -v
bin/appctl verify --base origin/main
```

`make test` 不代表执行了全部模型、浏览器和 Action 控制。生成端不应获得上面的私有验收变量。若要开放给模型测试，另外建立公共 fixture 服务与数据库，保持两组反馈条件一致。

### 6.3 完整本地控制顺序

推荐对照 CI YAML；下面补齐了旧 README 聚合命令未列全的后续阶段。脚本输出默认位于 `reports/`；多数 smoke 拒绝覆盖已有输出，复跑优先使用新 worktree。

```bash
make experiment
make web-deps
# Linux 缺 Chromium 系统依赖时：
# apps/web/node_modules/.bin/playwright install --with-deps chromium

python3 scripts/model_access.py
python3 benchmark/smoke.py | tee reports/benchmark-smoke.json
python3 benchmark/runner_smoke.py | tee reports/runner-smoke.json
python3 research/audit_pilot.py
python3 research/replay_followup.py
python3 benchmark/phase2/smoke.py
python3 benchmark/phase2/driver_smoke.py
python3 research/audit_atomic_pilot.py
python3 research/replay_atomic_pilot.py
python3 benchmark/phase3/smoke.py
python3 benchmark/phase3/driver_smoke.py
python3 scripts/browser.py --route baseline
python3 scripts/browser.py --route guarded
python3 scripts/browser.py --route zenstack
python3 scripts/report.py reports
```

本地 `model_access.py` 的 CI Secret 状态应为 unknown/null，不能伪造已检查。`scripts/report.py` 缺报告即失败；只运行部分阶段时，不能用伪造 JSON 让汇总通过。public CI 的 baseline job 承载多路径历史重放和新阶段控制，不代表这些控制只测试 baseline。

如手动启动 Action，先停止其他服务并初始化它专用的三表结构：

```bash
printf '%s\n' '{"op":"init"}' | node benchmark/phase3/db.mjs
go run ./cmd/action-server
```

共享 UI 的手动体验见 [shared-ui.md](shared-ui.md)，默认 UI 3000。普通服务与 Action 服务共享测试端口时不能同时启动。

## 7. Kiro CLI 接手方式：先确定角色

### A. Kiro 作为工程 Agent，继续运行 Codex 试验

这条路径不需要重写生成器。Kiro 负责环境准备、计划注册、生成包传递、调用已有命令、验收和提交报告；**实验中的模型生成仍必须实际由可用的 Codex CLI 完成**，不能由 Kiro 手写候选后标成 Codex 结果。

最近阻塞证据见 [model-preflight.json](evidence/phase3-cohort-controls-2026-11-14/model-preflight.json)：CLI 0.167.0-alpha.4 在 `turn.started` 前报：

```text
Error: failed to initialize in-process app-server client: Read-only file system (os error 30)
```

旧环境 0.166.0 曾可运行，只是环境事实，不证明降级就能解决。应在正常可执行的宿主中使用现有合法认证完成预检，不复制认证文件或更换 HOME/CODEX_HOME 去绕过状态目录/权限问题。

```bash
python3 benchmark/codex_runner.py doctor \
  --model gpt-6.1-sol --reasoning-effort medium \
  --output reports/phase3-doctor-001
```

预检会实际发起 READY 请求，可能有模型用量；“CLI 已登录”不等于 ready。只有 ready=true 再启动真实任务；若阻塞，保存错误并继续可独立完成的工程项，不把它算成模型代码失败。

### B. Kiro 同时作为被评测的生成引擎

当前**没有 Kiro 后端**。`benchmark/codex_runner.py` 和 standalone worker 都假设 Codex 的命令/JSONL 事件、完成标记和 usage 形状，不能直接把二进制名换成 `kiro-cli`。

若接手环境只有 Kiro，先读取本机 `kiro-cli --version`、`kiro-cli --help` 和对应官方文档，核对实际的非交互模式、结构化输出、权限与超时能力。本文不假定未验证的 Kiro 参数。

新增后端时，应注册新的 driver/实验身份，保留原 Codex 路径和历史结果，至少处理：真实预检、开始/完成判定、provider/runtime/预算分类、进程组终止、实际 usage 或 unknown、候选快照、模型/CLI/参数与计划绑定。以假引擎正反控制验证事件缺失、重复完成、非零退出、超时前后、缺失 usage 和环境错误。随后再对新的 Kiro 条件冻结独立队列，不能与旧 Codex 样本混成同一队列。

## 8. v3 真实 pilot 的具体操作

完整设计见 [phase3-action-evolution.md](phase3-action-evolution.md)。两组起点已有原子批量审批，任务新增审计与幂等；共同 RLS、SQL、HTTP 适配器保持一致，仅 go-action 包额外提供受保护的 Action 源码/API。这个设计衡量能力集成的综合效果，不是单独估计 RLS 的效果。

### 8.1 冻结可信计划

先选择可用且固定的模型、预算和生成环境。示例路径需替换为实际绝对路径。私有目录位于可信验收环境，不能挂载给生成端。真实计划要求干净且已提交的 harness。

```bash
export PHASE3_PRIVATE_DIR=/absolute/operator-owned/phase3-private
export MODEL_ID=gpt-6.1-sol
python3 benchmark/phase3/ctl.py fixtures \
  --output "$PHASE3_PRIVATE_DIR/fixtures-001.json"
python3 benchmark/phase3/ctl.py plan \
  --base d8a5c576cc3c293ee2da884f5c618958df407b51 \
  --model "$MODEL_ID" --reasoning-effort medium --time-budget 180 \
  --repetitions 1 --fixtures "$PHASE3_PRIVATE_DIR/fixtures-001.json" \
  --output reports/phase3-plan-001.json
```

`base_sha` 固定业务源，`harness_sha` 记录生成计划时的工具提交，两者不同是正常的。不要把 `--base` 随意换成新模型候选。计划中每个重复块包含 go-manual 和 go-action，按计划中已打乱的顺序执行；不能固定把某组放在第二位。

### 8.2 单 trial 导出并交给独立生成端

下面以 go-manual 为例；另一组使用自己的 trial、目录和 manifest：

```bash
python3 benchmark/phase3/ctl.py prepare \
  --plan reports/phase3-plan-001.json --trial go-manual--001 \
  --output /absolute/transfer/go-manual-001
```

包只包含 `candidate/`、`plan.json`、`manifest.json`、`job.json` 和 `worker.py`；候选是新的单提交仓库，无旧历史、remote 或 Oracle；manual 包也没有 Action 能力源码。复制整个包时保留候选 `.git`。

在独立生成环境运行：

```bash
python3 -I /absolute/transfer/go-manual-001/worker.py \
  --output /absolute/results/go-manual-001
```

生成端预先提供 Go 依赖缓存和正常 Codex 认证，不需要私有数据库、seed 或验收仓库。公开仓库有参考和 Oracle；确认性实验还必须限制外部检索，并只挂载本 trial 的包。**交接包或 workspace-write 本身不是主机/网络安全沙箱。** 同宿主便利命令 `ctl.py generate` 仍可用于明确标注的工程 pilot，不能据此宣称盲测。

### 8.3 恢复与独立验收

把生成结果和原 manifest 交回可信端；使用原计划、原私有 seed、全新候选目录和验收输出：

```bash
python3 benchmark/phase3/ctl.py restore \
  --plan reports/phase3-plan-001.json --trial go-manual--001 \
  --bundle /absolute/results/go-manual-001/candidate.bundle \
  --receipt /absolute/results/go-manual-001/receipt.json \
  --manifest /absolute/transfer/go-manual-001/manifest.json \
  --candidate /absolute/eval-candidates/go-manual-001
python3 benchmark/phase3/ctl.py evaluate \
  --plan reports/phase3-plan-001.json --trial go-manual--001 \
  --candidate /absolute/eval-candidates/go-manual-001 \
  --manifest /absolute/transfer/go-manual-001/manifest.json \
  --receipt /absolute/results/go-manual-001/receipt.json \
  --fixtures "$PHASE3_PRIVATE_DIR/fixtures-001.json" \
  --output reports/phase3-evaluations/go-manual--001
python3 benchmark/phase3/ctl.py summarize \
  --plan reports/phase3-plan-001.json --inputs reports/phase3-evaluations \
  --output reports/phase3-summary-001.json
```

两组都验收后再报告。生成失败的 `outcomes/` 目录通过额外 `--inputs` 纳入汇总；输入目录必须存在。预检阻塞或尚未验收的 trial 保留为未测量。验收退出 0/2/3 分别是通过/候选失败/基础设施阻塞；配置或完整性错误为 1。worker 的 0 仅表示生成链路完成，预算或作用域问题仍需查看 outcome，不能把退出 0 当成业务通过。

每份验收共 66 项：功能 6、授权 5、整批原子性 4、输入/容量 24、幂等 6、并发 3、写入故障回滚/重试 2、原有回归 16。保存逐用例结果，不能只保存总分。

## 9. 信任、实验与归档约束

1. **可信代码模式。** 固定 demo Token 不是生产 OIDC。持有数据库凭据的任意 Go 代码仍可伪造 `app.*` 上下文；同进程连接池不能作为恶意扩展的隔离边界。
2. **规则与 Oracle 还不是独立治理域。** 当前同仓库且公开，写权限可修改规则。生产治理需独立受保护规则、验证器和发布凭据。
3. **计划冻结后不能悄悄改评分。** 原计划绑定 evaluator 文件 hash；要重放旧计划，就 checkout 它的 `harness_sha`。要改工具，提交新 harness、注册新队列，不修改历史计划/checksum 让旧结果“通过”。
4. **候选完整保存。** 保存模型日志、原始 usage、prompt/plan/hash、初始与最终候选、patch/bundle、receipt/manifest、逐项验收和环境信息；私有 seed 与认证不进入 Git/公共 artifact。
5. **首次结果不可被修复覆盖。** 新目录记录追加 attempt/修复，并在报告中分开；当前 v3 没有自动 crash resume 或自动重试。重复 trial 会被拒绝，不能自动合并冲突结果。
6. **重放不是新样本。** 换 ID、追加 fixture、重新跑 bundle、人工删除条件或采用假 CLI，不增加真实模型样本数；事后测试不重判冻结首次成绩。
7. **私有 v2 seed 不在仓库。** 精确重放 #10 需要操作员提供原 seed 且 checksum 匹配；拿不到时使用已有公开 follow-up 重放，明确属于新 fixture，不能伪造原 seed。
8. **有限 Verifier。** Migration 只自动接受有限 CREATE TABLE/INDEX 等子集，权限扩大当前阻断等待策略所有者审查；没有通用 DDL 风险证明或成熟审批豁免通道。
9. **不以错误服务充当安全收益。** 健康用例必须能完成；5xx、服务不可用和验收异常不能算作“成功阻断”。两种故障注入中预期 503 是专门的回滚控制，必须同时验证三表不变与恢复重试。

## 10. 待办：顺序、产物和完成条件

| 优先级 | 工作 | 当前缺口 | 必须提交的证据/完成条件 |
|---|---|---|---|
| P0 | 接手环境核验与回归 | 新主机实际条件未知 | HEAD、工具版本、数据库/端口隔离记录、28 项单测及相应控制、CI |
| P0 | 确定模型执行后端 | Codex Work 预检阻塞；Kiro 后端未实现 | Codex 真实 ready=true，或新增有协议/失败分类控制的 Kiro driver；不静默换模型/引擎 |
| P0 | 实际生成/验收隔离部署 | 只有最小交接包和传递协议 | 独立挂载/账号/网络方案、无 Oracle/seed/管理员凭据暴露的核验；公开检索条件说明 |
| P0 | 首个 v3 两组真实 pilot | 真实生成数为 0 | 固定共同起点与完整要求；两组全部候选、逐项 66 检查、完整分母、usage/耗时；无隐式修复重试 |
| P0 | 真实结果分析与文档 | 尚无自然错误或收益差异证据 | 逐类错误/可用性/原子性/资源成本分开；全过也如实报告，无强行显著性结论 |
| P1 | 公共测试反馈条件 | 默认无公共 HTTP 服务 | 两组独立公共 fixture 服务可用；私有验收 DB 不借出；记录反馈条件 |
| P1 | 分批、恢复、attempt 管理（#5） | v2/v3 已有跨输入汇总及重复拒绝；缺自动恢复/重试历史调度 | 中断后不丢首次结果；按 plan/attempt 恢复；冲突/重复/漂移控制；新 backend 的环境元数据 |
| P1 | 确认性研究与更多任务（#6） | 有流程×Runtime pilot，但每组仅一次；v3 仅控制 | 预注册任务/样本与统计方法；Issue #6 提到至少十次重复，但重复次数不保证显著性；增加业务情境，保持完整分母 |
| P1 | 资源/查询预算 | 只有代码形状和容量观察 | 冻结 SQL 往返、行数/内存/耗时边界后实际测量；不要用源码计数伪装 tracing |
| P1 | 新元数据策略纳入治理 | ActionRequest/ApprovalAudit SQL 手写 | 新策略/语义 diff/审批及对抗测试；不放宽原规则来“通过” |
| P1 | 旧单条审批与 UI 接入 Action | 目前仍旧流程 | 明确兼容性与幂等 UX，真实 E2E、每条成功都有审计；实验进行时避免修改冻结起点 |
| P2 | 生产身份与发布治理 | demo 身份，同仓库规则 | OIDC、受保护验证器/规则、独立发布权限及审计；另立范围 |
| P2 | 不可信扩展与外部副作用 | 仅可信进程、单 DB 事务 | 独立执行隔离、受控 RPC/能力、资源/网络限制；如引入消息再设计 outbox/交付语义 |

#5/#6 尚未全部完成，不能仅因新增一个控制或 pilot 就关闭。下一项最有价值的交付是新的真实 v3 证据，或在缺少 Codex 的情况下完成有验证的 Kiro 适配，不是再写一轮平台愿景。

**2026-11-15 更新：** Kiro 后端已实现（`plan --engine kiro` + worker）。pilot 001 暴露了 Oracle 依赖未写入契约的 requestHash 公式；修正契约后注册的队列 002（10 块 × 2 组，Seatbelt 文件隔离）全部 20 个候选 66/66 通过，见 [队列 002](phase3-kiro-cohort-002-results.md)。仍待：网络与宿主级隔离（VM/容器）、更难的任务以区分两组、多模型重复、P1 各项。本次本地 PostgreSQL 为 17.11（17.6 镜像拉取阻塞）。

**2026-11-17 更新（v9 之后）：**
- 事件规则已在私有叠加层上验证。移植到公开 `internal/opdef` 的工作在单独的 PR 里，分支为 `poc/v9-event-rules`。
- v9 的局限：每个任务的注册表里只有一个可用事件。下一步如果继续，可考虑以下方向：
  - 注册表里放多个候选事件，看模型能否做出正确的业务选择；
  - 把同样的“确定性约定规则”推广到指纹、职责分离、拒绝语义上；
  - 扩展到第二个业务域。
- 运行注意事项：
  - READY 预检的 30 秒探针偶尔会超时，v9 中发生了 5 次。续跑脚本 `scripts/resume_v9.sh` 的归档目录名会冲突，已在附录 E 记录；下次应给归档目录加序号。
  - 验收主机根盘使用率已达 97%，只能使用 `/opt/poc-oracle`，大型归档用完立即删除。

## 11. 下一次交付的验收清单

- [ ] 从最新 main 建分支/worktree，提交范围明确；历史证据、固定计划、候选和评分未被改写。
- [ ] 环境、CLI/引擎、请求模型/推理参数、预算、起点、harness 与隔离条件完整记录。
- [ ] 发生了真实生成且可核验；若环境阻塞，保持未测量，保存原始错误。
- [ ] 两组同要求、同反馈条件、同块 fixture；全部 trial 留在分母中。
- [ ] 候选经过真实 PostgreSQL 的完整 Oracle；功能、授权、原子性、幂等、并发、故障恢复与回归分组齐全。
- [ ] 失败、未测量、修复后结果、重放和 synthetic 分开；未知 token/费用不补零、不估算账单。
- [ ] 所需正反控制与历史回归、public CI 通过；报告缺失则失败。
- [ ] 提交代码/操作指南/结果证据与 PR；更新 #5/#6 对应状态，明确剩余限制。

## 12. 已合并 PR 与 CI 导航

以下十个原有 PR 已于本次交接整理时合入 main（merge commit，保留原始 SHA）：

| PR | 内容 | main 合并提交 |
|---|---|---|
| [#1](https://github.com/XUJiahua/enterprise_agent/pull/1) | 三路径 POC、Verifier、独立验收与故障注入 | `c54f67f` |
| [#2](https://github.com/XUJiahua/enterprise_agent/pull/2) | 共享 UI、浏览器验收、模型试验工具 | `d4fbd0f` |
| [#3](https://github.com/XUJiahua/enterprise_agent/pull/3) | Codex 执行器与早期阻塞归档 | `7aadf0a` |
| [#7](https://github.com/XUJiahua/enterprise_agent/pull/7) | v1 两个真实队列和环境修正 | `fc0a9c0` |
| [#8](https://github.com/XUJiahua/enterprise_agent/pull/8) | 九候选审计、重放、租户/全批语义研究 | `1ab7a1e` |
| [#9](https://github.com/XUJiahua/enterprise_agent/pull/9) | v2 四组执行链路、46 项验收与控制 | `ac3041c` |
| [#10](https://github.com/XUJiahua/enterprise_agent/pull/10) | 四组真实原子审批 pilot | `1696ab9` |
| [#11](https://github.com/XUJiahua/enterprise_agent/pull/11) | 四候选审计、边界与查询形状研究 | `ac70b09` |
| [#12](https://github.com/XUJiahua/enterprise_agent/pull/12) | 事务化 Action、审计与幂等 | `d9fdf8c` |
| [#13](https://github.com/XUJiahua/enterprise_agent/pull/13) | v3 两组工具与独立生成交接包 | `5bd655e` |

最近完整功能快照的 [PR #13 CI](https://github.com/XUJiahua/enterprise_agent/actions/runs/43607385772) 五个 jobs 成功；[完整证据](https://github.com/XUJiahua/enterprise_agent/actions/runs/43607385772/artifacts/14118792528) 的服务端 SHA256 为 `31e5840c1b865cb98575fa54943be9ca7a577fb76683722e5c9d91c0261fe8cd`。它核验了历史回归和新增 v3 控制，不调用模型。main 合并后的运行和文档 PR 的检查以 [Actions](https://github.com/XUJiahua/enterprise_agent/actions) 实时状态为准。

CI artifact 有到期时间，长期事实优先依据 Git 中的冻结计划、归档、源码 hash 和报告；需要长期保留的新证据应随相应 PR 入库，排除认证、私有 seed、node_modules 和临时二进制。

## 13. 可直接交给 Kiro 的起始任务

```text
请接手 https://github.com/XUJiahua/enterprise_agent。
先读取最新 main 的 docs/HANDOFF.md、docs/phase3-action-evolution.md 和 CI YAML，
核验实际 HEAD、未完成 issues、运行环境和模型后端。不要重做已完成的 POC，
也不要改写历史模型归档或评分。

优先目标：完成 v3 手写事务 vs 平台 Action 的第一块真实两组 pilot，保留全部
候选、模型证据与独立 66 项验收、完整分母。若本机可用 Codex，则使用现有
Codex 工具；如果只有 Kiro，先实现经过正反控制验证的新后端，并单独注册
实验条件，不能伪装成旧 Codex 试验。

使用独立 worktree/分支与 PR。若模型运行被环境阻塞，保存原始错误并保持
未测量，同时推进不依赖该阻塞的隔离、后端适配或恢复工程。每阶段提交
代码、操作说明、实测证据及剩余待办。未经明确授权不要直接 push main。
```

**2026-11-15 更新（v4）：** 新增“隐含约定”实验 harness（`benchmark/phase4/`）：在既有 Action 上新增批量驳回，四条只存在于导出代码中的约定（审计、幂等作用域含 operation、requestHash 指纹、不合格目标统一 409）与显式契约分开计分，共 108 项（显式 95 + 隐含 13）。人工/假 CLI 控制通过，真实模型样本为 0。见 [phase4-implicit-convention.md](phase4-implicit-convention.md)。

**2026-11-15 更新（v5）：** 新增“分散约定”实验 harness（`benchmark/phase5/`）：在 v4 完成态上新增单目标、requester 侧的 `POST /purchases/{id}/reopen`，四条约定（审计含 reason、单目标 requestHash、outbox 事件类型、统一拒绝，最后一条无注释）的线索分布在候选代码、ledger、对账、notify 与 SCHEMA 多个文件；action 组的 Operation 示例改放在可编辑的 `internal/candidate`。共 145 项（显式 133 + 隐含 12），人工/假 CLI 控制通过，真实模型样本为 0。见 [phase5-scattered-convention.md](phase5-scattered-convention.md)。
