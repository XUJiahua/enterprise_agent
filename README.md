# Enterprise Agent — 可验证企业应用 POC

接手入口：[完整技术 POC 交接文档（Kiro CLI）](docs/HANDOFF.md)，包含目标、合并后的进展、实验事实、运行命令和待办验收标准。

采购审批业务的三条可运行对照路径。目标是检验确定性护栏能否阻断常见实现缺陷，先验证机制，再开展真实 AI Coding 对照实验。

| 路径 | 实现 | 权限执行位置 |
|---|---|---|
| baseline | Go + pgx，手写授权及 SQL 条件 | 业务 Handler |
| zenstack | ZenStack **3.10.2** + PostgreSQL | ZModel + PolicyPlugin |
| guarded | 与 baseline 共用 Go HTTP 实现，启用最小 Policy 编译器 | PostgreSQL RLS + 列权限；Go 绑定可信上下文 |

三条路径执行同一份独立 HTTP 验收：**16 个用例**，包含两租户、角色、字段、身份伪造、创建、审批、状态检查、8 请求并发审批和 60 次并发身份切换。另有 **5 类运行时缺陷注入**、ZenStack 禁用插件的边界对照、Go 路径的直接 SQL 探针与两组启动配置探针和 **7 组 Verifier 对抗探针**。

## 在 public CI 查看结果

[Actions](https://github.com/XUJiahua/enterprise_agent/actions) 中的 `POC verification` 会在 PR 自动运行。三个 Runtime job 各有独立 PostgreSQL 17.6 服务，无需模型 API Key。

- 健康版本必须全部通过；普通 Go 的注入版本必须被验收测试发现缺陷。
- ZenStack / guarded 的五种注入必须仍满足对应安全不变量。
- ZenStack `disable-policy` 对照必须暴露越权，证明测试能识别策略未启用。
- **500、服务启动失败、探针异常、缺失报告均使 CI 失败，不能算作成功拦截。**
- 最后的 `report` job 输出对照表，`poc-comparison` artifact 包含 JSON 结果、日志、静态探针结果及 `comparison.md`。

`Faults blocked = 5/5` 只表示这五种人工注入被阻断，不能解读为缺陷率降低 100%。其中敏感字段过度读取：guarded 返回 403，ZenStack 返回该字段为 null；可用性语义不同，报告单独说明。

## 本地复现

需要 Go 1.25+、Node 24、Python 3.12+、一个**可销毁的 PostgreSQL 17+ 测试数据库**。以下口令只用于本地和 CI 测试。

```bash
docker run --rm --name enterprise-agent-poc \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=enterprise_agent_poc -p 5432:5432 postgres:17.6
```

另一终端运行：

```bash
export ADMIN_DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/enterprise_agent_poc
export DATABASE_URL=postgres://app_runtime:poc-only@127.0.0.1:5432/enterprise_agent_poc
export POC_DB_RESET=1
make deps test
make experiment
make web-deps
python3 scripts/browser.py --route baseline
python3 scripts/browser.py --route zenstack
python3 scripts/browser.py --route guarded
python3 benchmark/smoke.py | tee reports/benchmark-smoke.json
python3 benchmark/runner_smoke.py | tee reports/runner-smoke.json
python3 -m unittest discover -s benchmark/tests -v
python3 scripts/model_access.py
python3 research/audit_pilot.py
python3 research/replay_followup.py
python3 benchmark/phase2/smoke.py
python3 benchmark/phase2/driver_smoke.py
python3 scripts/report.py reports
```

`model_access.py` 的本地默认值为 `null`，报告显示 unknown，表示未检查 CI Secret；不会把本地状态伪装成 CI 未配置。输出不可覆盖，重复运行 smoke 请使用新 checkout。

如默认端口被占用，可将 Docker 映射改为 `127.0.0.1:55434:5432`，同步修改两个数据库 URL，并设置 `export POC_HTTP_PORT=18084`。Go、ZenStack、独立 Oracle、浏览器健康检查和 UI 代理使用同一端口，仍只连接 loopback。

`db.mjs reset` 会删除测试表并重建，只接受数据库名以 `_poc` 结尾且显式 `POC_DB_RESET=1` 的环境。不要指向有业务数据的数据库。各路径顺序运行，共用 127.0.0.1:8080；三个路径不能共用同一数据库同时运行。

## 手动体验 API

```bash
node scripts/db.mjs reset guarded
bin/server --mode guarded
# 新终端
curl -H 'Authorization: Bearer demo_alice' http://127.0.0.1:8080/purchases
curl -X POST -H 'Authorization: Bearer demo_carol' \
  http://127.0.0.1:8080/purchases/a-other/approve
```

ZenStack 路径先 `node scripts/db.mjs reset zenstack`，再 `npm start --prefix apps/zenstack`。普通 Go 使用 `reset baseline` 和 `bin/server --mode baseline`。

## Verifier

```bash
bin/appctl compile                  # 从 Policy 生成 generated/guard.sql
bin/appctl check                    # 严格策略语法、生成漂移、迁移锁及 SQL 子集
bin/appctl plan --base origin/main  # 检查角色权限扩大、历史 Migration 改写
bin/appctl verify --base origin/main
```

`check`、`plan`、`verify` 当前共享验证管线，输出 JSON 和非零退出码；并非通用应用编译器。首次 PR 的真实 base 尚无 Policy，建立初始基线；此后 PR 与 GitHub 提供的精确 base SHA 比较。权限扩大要求策略所有者审查，当前采用阻断方式；尚无审批豁免通道。

迁移检查故意保守：只自动接受有限的 `CREATE TABLE` / `CREATE INDEX` 子集，其余包括 `ALTER` 要求人工扩展验证器。它不宣称能分析任意 PostgreSQL DDL 的兼容性、锁表或回滚风险。

## 代码导航

- `tests/acceptance.py`：独立手写业务 Oracle，不导入任何实现或 Policy。
- `scripts/experiment.py`：重置数据库、启动应用、注入缺陷、采集结果。
- `apps/zenstack/zenstack/schema.zmodel`：真实 ZenStack V3 模型与策略。
- `internal/app`：Go HTTP 实现，baseline 与 guarded 对照。
- `internal/policy`、`cmd/appctl`：闭合 Policy 语法、RLS 编译、语义变更及迁移检查。
- `scripts/db.mjs`：隔离数据库初始化和不经过 API 的 RLS/列权限探针。
- `tests/verifier.py`：实际修改文件与 Git 历史，尝试绕过验证器。
- [首轮结果](docs/poc-results.md)、[业务约定](docs/business-contract.md)、[实验设计和限制](docs/experiment-design.md)。

## 信任边界

这是可信代码模式的工程 POC。测试身份由固定 Bearer Token 映射而来；没有生产 OIDC、会话、密钥管理，也没有给任意不可信 Go 代码提供沙箱。应用始终只监听 loopback。RLS 的身份来自可信应用绑定，持有数据库凭据的任意代码可以伪造 `app.*` 会话变量，因此不能宣称 RLS 对同进程恶意扩展不可绕过。

CI、Policy、测试目前位于同仓库，拥有仓库写权限的人可以修改它们。生产发布必须另设独立可信验证器、受保护规则与审批；本 POC 不声称已经实现该治理边界。

当前已有共享 Next.js UI 和三路径真实浏览器验收，另提供可复现的真实模型试验计划及独立候选代码验收器。现已有真实 AI Coding 小样本 pilot，见 [真实结果报告](docs/model-pilot-results.md)；尚不能推断总体首次通过率或性能/成本优势。人工注入和 synthetic-smoke 结果不替代模型可靠性结论。

## 第二阶段：共享界面与模型试验工具

- [共享 UI 启动与浏览器验收](docs/shared-ui.md)：三路径使用同一个 Next.js production UI，每条六个 Chromium 用例，提供桌面和移动截图。
- [模型试验操作指南](docs/benchmark-guide.md)：三个独立任务、随机可复现计划、固定提交/Oracle、追加式 attempt 和完整分母。
- CI 另外执行四次真实候选提交的 synthetic-smoke 验收，验证验收器本身可区分未完成任务和正确修复；这些不是模型测量。

## 第三阶段：真实模型运行入口

[模型执行器](docs/model-runner.md) 支持真实 Codex 预检、固定推理参数、生成预算、候选源码范围检查、Git 快照与独立验收。预检会实际发起 READY 请求，避免只凭“已登录”就认定可运行。

PR CI 仅运行不调用模型的协议测试和 synthetic 驱动正反控制。真实 trial 必须在能正常执行 `codex exec` 的环境运行；运行环境、服务或凭据阻塞保留为未测量，不计为模型代码失败。

接手环境已通过真实预检并完成三路径 pilot；完整结果、原始模型事件、候选 bundle 和独立验收见 [真实结果报告](docs/model-pilot-results.md)。前一环境的九项预检阻塞仍单独保存在 [历史运行记录](docs/model-pilot-status.md)，保持未测量状态。

## PR #7 后续研究

[研究结论与复现实验](docs/research-followup.md)：九个真实候选全部重放、195 项冻结验收和 45 项追加验收通过；人工移除新接口租户条件后，普通 Go 泄露而策略路径保持隔离。另通过实际数据库探针明确了 RLS 与批量业务全批成功的边界，并给出流程 × Runtime 的下一轮实验草案。本轮没有新增模型样本。

## 原子批量审批实验 v2

[四组 Go 实验与操作指南](docs/phase2-atomic-bulk.md) 将额外流程指导 × RLS Runtime 拆为 2×2 对照。每个候选接受 30 项新业务验收和原有 16 项回归，检查授权功能、逐行权限、全批回滚、输入和并发。模型候选仅获得新的最小源码仓库，原 Git 历史与 evaluator 文件不随导出；同宿主读取与公开参考实现的边界仍需独立隔离环境解决。

CI 执行 12 次人工正反候选控制和一次假 CLI 链路控制，不调用模型、不计入真实 AI 样本。原应用暂不添加批量审批接口，以便后续由真实模型完成该任务；参考答案只用于验证评分器。v1 计划与 PR #7 归档保持原样。

四组真实生成的冻结条件、完整分母和逐组验收见 [原子批量审批真实 pilot](docs/phase2-model-pilot-results.md)；与前置人工/假驱动控制及 v1 样本分开。

## PR #10 后续审计与研究

[审计、追加重放与代码差异](docs/atomic-pilot-followup.md)：50 个归档文件、四个 bundle 与原始评分/usage 核对一致；四个既有候选在新公开 fixture 上通过 184 项验收和 28 项追加边界检查。查询形状与报文上限差异单独报告，旧的 46/46 首次结果不重判。本轮无新增模型生成；Public CI 新增了四个真实归档候选的自动重放。

## 事务化 Action 内核 POC

[审批、审计与幂等操作指南](docs/transactional-action.md)：新增独立 `cmd/action-server`，将批量审批状态、每目标审计记录和完成态幂等结果放入同一个 PostgreSQL 事务。包括同 key 并发串行、重放/冲突、完整目标集合检查和明确的 64 KiB 报文边界。

独立验收参考为 66/66，五个人工负控制均被识别，另有 12 项元数据 SQL 和 7 项启动检查。新增能力是领域 Action 原型，未迁移原有单条审批或 UI，也不是通用工作流/跨系统 exactly-once。v3 尚未注册真实模型队列；历史 v1/v2 评分与归档不变。

## v3 业务演进对照实验

[手写事务与平台 Action 实验指南](docs/phase3-action-evolution.md)：两组共用原子批量审批起点、RLS、SQL 结构和 HTTP 适配器，只有 Action 能力文件不同。新增冻结计划、最小候选导出、独立生成包、bundle/receipt 恢复、私有验收和完整分母汇总。六次人工候选控制与三次假 CLI/交接验收均使用 66 项评分；真实模型数据另行采集。生成包不包含 Oracle/私有 seed，实际主机及网络隔离仍需部署验证。

首个真实 v3 两组 pilot 使用新增的 Kiro CLI 引擎完成：manual 50/66，action 66/66。manual 的失败全部来自 Oracle 检查了契约未声明的 requestHash 公式，属于 harness 缺陷，不是能力收益证据。见 [Kiro pilot 结果](docs/phase3-kiro-pilot-results.md)。
