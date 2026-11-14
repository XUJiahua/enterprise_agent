# v3 业务演进实验：手写事务与平台 Action

接续 PR #12，提供可以预注册、生成、交接和独立验收的新实验链路。**本提交准备阶段仅运行人工控制和假 CLI；新增真实模型样本为 0。** 旧 v1/v2 计划、模型归档和首次评分均不修改，PR #12 的机制契约及 Oracle 源文件也保持不变。

## 比较的变量

两组从**相同的原子批量审批代码**开始，把功能演进为“审批状态 + 每目标审计 + 完成态幂等结果”。共同起点已经能完成批量审批、完整目标检查与回滚；尚无审计和幂等。两组均使用 guarded RLS、同一数据库结构、固定 demo 身份、同一严格 HTTP 适配器和全部相同的业务要求。

| 条件 | 可用能力 | Agent 需要做的工作 |
|---|---|---|
| go-manual | pgx、RLS、公开 SQL 结构 | 在候选业务代码中实现事务、审计与幂等 |
| go-action | 同上，另加只读的领域 Action 内核 | 可以调用内核，并完成返回值和错误类型的适配 |

除了 `internal/action/approval.go` 能力文件，两个初始导出的文件及字节完全一致。共同任务说明相同；两组提示词只有能力说明不同，不额外施加开发流程指导。模型都可以自发测试。仅允许修改/新增 `internal/candidate/*.go`（也允许其下 Go 子目录），HTTP 适配器、主程序、内核及依赖均受保护。

这是**能力辅助的业务演进实验**，包含可复用实现、API 和源码提供的综合效果；不是单独测 RLS，也不能区分内核源码可读性与 API 封装各自的贡献。平台内核专门解决当前领域任务，不能据此推断通用企业平台的收益。不可把一次集成任务的代码减少当成跨项目可靠性证据。

## 冻结与评分

`benchmark/phase3/cohort.json` 注册两组与共同任务。计划固定业务源提交（默认操作命令使用 #12 的 `d8a5c57`）、harness SHA、源文件/模板/能力/Oracle 的 SHA256、模型请求标识、reasoning effort、单次预算、提示词、顺序及完整 trial 分母。重复块内两组使用同一私有 seed 派生 fixture；每块顺序由固定随机种子决定。

新 `cohort_oracle.py` 为原机制 Oracle 提供按块变化的普通 ID，保留 Unicode 长度与引号边界，仍验收原来的 **66 项**：功能 6、授权 5、整批原子性 4、输入/容量 24、幂等 6、并发 3、写入失败回滚/重试 2、原有 API 16。ID 变化不代表增加了新业务场景。

验收通过 HTTP 和管理员三表快照独立判断。候选服务只获得非特权运行账号，管理员 URL、故障触发器和私有 fixture 只供验收控制器使用。通过报告必须包含完整且不重复的分组用例。失败结果、生成预算耗尽、作用域违规计入已观察；provider、CLI 和基础设施失败为未测量。某组未全部测量时通过率为 `null`，缺失 usage 保持未知。不能用重新运行后的成功覆盖首次结果。

## 生成交接包与隔离

推荐使用 `prepare` 为**一个 trial**生成一个包，其内容为：

```text
kit/
  candidate/       # 全新单提交 Git；无 remote、旧历史、Oracle 或私有 seed
  manifest.json   # 初始候选 attestation
  plan.json       # 完整冻结计划；只有私有 seed 的 checksum
  job.json        # 单个 trial 的模型参数、身份和 worker hash
  worker.py       # 独立标准库脚本，不导入仓库或验收工具
```

manual 包中也不包含另一组的 Action 源码。SQL 结构是明确提供给两组的公共任务材料，不是私有测试。worker 在生成端校验初始文件与身份，进行 CLI 预检后执行一次任务；归档原始 JSONL、stderr、已上报 usage、patch、完整 Git bundle 和绑定计划/候选/manifest 的 receipt。认证文件不进入包。

**交接包只实现最小材料传递，不自动创建安全沙箱。** 操作员应把单个包复制到独立主机/容器，仅挂载这个包和必要 CLI/认证资源；不挂载 evaluator checkout、其他组源码、私有 seed 或管理员数据库。若要称为隐藏验收，还应限制外部仓库检索：本公共仓库含 Oracle 与人工参考，同宿主目录边界或 CLI `workspace-write` 并不能阻止读取/检索。网络与宿主隔离的实际部署、模型端缓存和服务版本仍需记录和验证，不能仅凭一项自报参数宣称盲测。

`generate` 保留为同宿主工程 pilot 的便利命令，会读取控制器 checkout；它不提供上述隔离。推荐确认性运行使用独立 worker 路径。CI 的 worker 控制证明无仓库导入依赖及交接可用，**不是主机逃逸或网络隔离测试**。

## 运行步骤

先准备 Go 1.25、Node、Python、pg 依赖和可销毁 `_poc` PostgreSQL，参考 README。生成端预先准备相同依赖缓存；私有数据库不参与生成。冻结真实计划前提交所有 harness 修改，真实计划拒绝 dirty checkout。不迁移认证文件或更换 HOME/CODEX_HOME 绕过运行限制。

在可信验收环境生成并安全保存 seed，注册完整计划：

```bash
export PRIVATE_DIR=/absolute/operator-owned/phase3-private
export MODEL_ID=your-fixed-available-model
python3 benchmark/phase3/ctl.py fixtures --output "$PRIVATE_DIR/fixtures.json"
python3 benchmark/phase3/ctl.py plan \
  --base d8a5c576cc3c293ee2da884f5c618958df407b51 \
  --model "$MODEL_ID" --reasoning-effort medium --time-budget 180 \
  --repetitions 1 --fixtures "$PRIVATE_DIR/fixtures.json" \
  --output reports/phase3-plan.json
python3 benchmark/phase3/ctl.py prepare \
  --plan reports/phase3-plan.json --trial go-manual--001 \
  --output /absolute/transfer/go-manual-001
```

为每个 trial 独立准备、复制整个包（包括候选 `.git`）。按计划列出的顺序执行，不总是 manual 在先。生成主机仅需包、Go 依赖缓存及可用 Codex；不需要 Node、数据库、私有 seed 或 evaluator 源码：

```bash
python3 -I /absolute/transfer/go-manual-001/worker.py \
  --output /absolute/results/go-manual-001
```

`visible_smoke.py` 可连接单独公共服务，公共数据库需按包中的 SQL 与公开 seed 初始化并运行候选服务。默认不自动提供公共服务，也不借用私有验收数据库。若无公共服务，记录条件，两组保持一致；`go test ./...` 仍可运行。

生成完成后，只把结果目录与原 manifest 交回可信验收端；使用原计划与 seed，恢复 bundle 到控制器仓库外的全新目录：

```bash
python3 benchmark/phase3/ctl.py restore \
  --plan reports/phase3-plan.json --trial go-manual--001 \
  --bundle /absolute/results/go-manual-001/candidate.bundle \
  --receipt /absolute/results/go-manual-001/receipt.json \
  --manifest /absolute/transfer/go-manual-001/manifest.json \
  --candidate /absolute/eval-candidates/go-manual-001

export ADMIN_DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/enterprise_agent_poc
export DATABASE_URL=postgres://app_runtime:poc-only@127.0.0.1:5432/enterprise_agent_poc
export POC_DB_RESET=1
python3 benchmark/phase3/ctl.py evaluate \
  --plan reports/phase3-plan.json --trial go-manual--001 \
  --candidate /absolute/eval-candidates/go-manual-001 \
  --manifest /absolute/transfer/go-manual-001/manifest.json \
  --receipt /absolute/results/go-manual-001/receipt.json \
  --fixtures "$PRIVATE_DIR/fixtures.json" \
  --output reports/phase3-evaluations/go-manual--001
python3 benchmark/phase3/ctl.py summarize \
  --plan reports/phase3-plan.json --inputs reports/phase3-evaluations \
  --output reports/phase3-summary.json
```

对 go-action 使用其自己的包、receipt、trial ID。每个评估重置整个可销毁数据库，不能与其他任务并发共用数据库或端口。验收退出码 0/2/3 分别表示通过/候选失败/基础设施阻塞；配置或完整性错误为 1。worker 的 0 表示生成链路完成，不表示业务验收通过；预检/服务阻塞为 3。生成失败时将结果的 `outcomes/` 目录通过额外 `--inputs` 加入汇总；完整生成尚未验收的 trial 仍为未测量。

所有输出拒绝覆盖。中断后保留既有目录，从计划找未完成项，使用新目录执行；没有自动 crash resume 或重试调度。重复/冲突 trial、外来计划和 evaluator drift 会被拒绝，需明确审计部分产物后决定如何接续，不自动合并多次 attempt。

## 前置控制与 CI

```bash
python3 -m unittest discover -s benchmark/tests -v
python3 benchmark/phase3/driver_smoke.py
```

driver smoke 使用真实 PostgreSQL，执行两组 × 三个人工候选（完整参考、未演进、全部拒绝）：两份参考各 66/66，其余四份被识别；原有 API 均保持 16/16。另用明确的假 CLI 生成、恢复并验收两组候选，再独立运行一次 standalone worker 包的交接验收，共 **9 次 × 66 项**。其中第三次驱动验收是重复的 transport 控制，不能作为新 trial 加入两组汇总。还验证缺失分母和重复结果拒绝。

Public CI 在 baseline job 顺序执行这些控制，最终 report 缺失证据即失败，不调用模型。原有各阶段回归继续保留。尚未产生 v3 AI 正确性或成本收益结论；正式比较还需要可用模型环境、实际隔离部署及足够的重复样本，Issue #5/#6 仍未全部完成。

本地 [控制摘要与源码 hash](evidence/phase3-cohort-controls-2026-11-14/controls.json) 记录了 6 次人工控制、3 次驱动/交接验收、27 个源码文件及 PostgreSQL 17.6 / Go 1.25.4 环境。本次另做 [真实 CLI 预检](evidence/phase3-cohort-controls-2026-11-14/model-preflight.json)：Codex CLI 0.167.0-alpha.4 初始化返回 `failed to initialize in-process app-server client: Read-only file system (os error 30)`，未进入模型回合。没有注册或执行真实 v3 cohort，因此不报告模型首次通过率。
