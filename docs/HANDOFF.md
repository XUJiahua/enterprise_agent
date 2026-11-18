# Enterprise Agent：当前工程交接（v1–v9）

核对日期：2026-11-17（Asia/Shanghai）。仓库：<https://github.com/XUJiahua/enterprise_agent>。

本文取代旧的按时间追加交接。旧内容见 [历史交接](HANDOFF-before-closeout-2026-11-17.md)，仅供追溯，不执行其中“首次 v3 pilot / 实现 Kiro 后端”等过时起始任务。

## 接手摘要

**2026-11-17 更新：主线已转为 v0.1 产品开发（Go 内核 + 采购参考应用），权威入口是 [issue #36](https://github.com/XUJiahua/enterprise_agent/issues/36) 与 [v0.1 发布说明](release-v0.1.md)。下文“优先顺序”与“Kiro 接手任务”中先恢复 oracle / 继续试验的安排已过时，仅保留作研究背景。**

v0.1 状态：M1a–M3 已实现，作为 stacked PR #37 → #38 → #39 → #40 提交，CI 全部通过，待评审合并（逐个合并，每次把下一个 PR 的 base 改为 main）。合并后可在 main 打 `v0.1.0` tag。

v0.1.1（#41）与脚本修复（#42）已合并。Agent 扩展试验结果见 [v011-agent-trials.md](v011-agent-trials.md)。

v0.1 之后的候选工作（均未开始，按需求排期）：

| 工作 | 说明 |
|---|---|
| 原 `/` 页面与旧 API 迁移 | 三条 POC 路线的共享界面仍走旧接口；迁移时保留历史回放 |
| 生产认证 | 替换 `cmd/product` 的 demo 身份适配器（OIDC 等），Principal 仍由宿主绑定 |
| 消费者扩展 | 毒消息隔离/重试上限、多实例租约、外部投递（优先选现成 Go 队列） |
| 行为层契约 | `validate` 只看定义层面；试验中 reverse 改了 close 的语义却没升版本。候选做法：为已发布版本冻结行为用例 |
| 第二轮 Agent 试验 | 用更新后的 AGENTS.md 换新任务重跑，对照 v011-agent-trials.md 中的缺陷类别 |
| 第二业务域 | 用第二个领域模块验证内核不依赖采购，不先行抽象 |

**v3–v9 已完成真实 Kiro 模型实验。Kiro 后端、文件与网络隔离、跨主机私有 Oracle 流程都已经存在。不要重做首个 v3 pilot。** 公开工程包含受保护的操作语义目录、事务内核、目录绑定 Verifier 和 v9 事件登记规则，仍是采购审批 POC。

原 16 个 PR（#15–#25、#27–#31）已通过 [PR #33](https://github.com/XUJiahua/enterprise_agent/pull/33) 合入 main，merge commit 为 `e8248d836f086a221eae521a72068e63d140232a`，保留全部 51 个原始提交。旧 PR 已关闭并注明整合去向（#15 被 GitHub 自动标记 merged）。[合并后 main CI](https://github.com/XUJiahua/enterprise_agent/actions/runs/44048578930) 六个 jobs 全部成功。

2026-11-17 后续：`goodloris/oracle` 现已由所有者公开。已基于固定 v9 提交实现续跑修复，13 项 subprocess 控制通过；当前连接器写 oracle 返回 HTTP 403，修复以 [补丁与交接](oracle-v9-resume-handoff.md) 保存，尚未直接提交或部署到 oracle。

研究交接见 [Issue #32](https://github.com/XUJiahua/enterprise_agent/issues/32)，冻结承诺见 [Issue #26](https://github.com/XUJiahua/enterprise_agent/issues/26)。本次收尾没有调用模型，没有新增模型样本，也没有修改历史计划、Oracle、评分或 evidence。

优先顺序：在有 oracle 写权限的环境应用并提交续跑补丁 → 确认真实主机部署条件 → 提取实验叠加层的可复用平台能力 → 使用新隐藏任务开展多合法事件与持续业务演进实验。

## POC 目标与架构

问题是：在同等业务要求下，平台的确定性规则、权威领域事实与运行机制，能否提高 Agent 开发正确性，并降低得到一个正确结果的成本？

| 层次 | 已有实现 | 当前边界 |
|---|---|---|
| 权限 | Go baseline、Go + Policy/RLS、ZenStack 3.10.2 | 可信代码模式；应用绑定身份，同进程恶意代码可伪造上下文 |
| Action | `internal/action`、`cmd/action-server` | 采购状态、审计、幂等结果同事务提交 |
| 领域事实 | `internal/opdef` 的五个版本化操作 | 角色、对象、状态、指纹、记录、事件、职责分离、统一拒绝 |
| 目录执行 | `internal/opkernel` | 按名称查目录；事务、锁、完整目标检查、审计、outbox、幂等 |
| Verifier | `appctl verify/extensions/operations` | 语法和有限语义；静态通过不能替代运行验收 |
| UI | `apps/web` 的共享 Next.js 界面 | 原列表、创建、单条审批、demo 身份；尚未接入全部新 Action |

`opdef` 与 `registry.go` 由受保护源集合和审查保护。`Digest()` 仅是条目内容摘要；规则变化可能不改变它，复现还必须绑定平台 commit 和受保护源码 hash。

## 已完成实验

| 阶段 | 规模与首次结果 | 资料 |
|---|---|---|
| v1 / v2，Codex | 3 + 9 + 4 个候选通过；小样本未区分正确性收益 | [v1](model-pilot-results.md)、[v2](phase2-model-pilot-results.md) |
| v3，Kiro | pilot 2 个暴露契约缺陷；修正后 20 个全过；随后 60 个多模型 trial 出现幂等/并发差异 | [pilot](phase3-kiro-pilot-results.md)、[002](phase3-kiro-cohort-002-results.md)、[003](phase3-kiro-cohort-003-results.md) |
| v4 / v4b | 80 + 40 个计划 trial；提示扩展点缓解误改内核 | [v4](phase4-kiro-cohort-001-results.md)、[v4b](phase4b-kiro-cohort-001-results.md) |
| v5 / v5b | 80 + 80 个计划 trial；内核封装约定有效，事件声明仍易错 | [v5](phase5-kiro-cohort-001-results.md)、[v5b](phase5b-kiro-cohort-001-results.md) |
| v6 确认性 | 240 个计划 trial；H1 有效配对 98，H2/H3 115；A 为 42/120 与 44/120 | [v6](v6-confirmatory-results.md) |
| v7 确认性 | 240 个计划 trial；A：manual 44/80、doc 61/80、opdef 79/80；manual 有 1 个未测量，主比较有效配对 79 | [v7](v7-confirmatory-results.md) |
| v8b 确认性 | 144 个 trial；A：manual 33/72、given 70/72 | [v8b](v8b-confirmatory-results.md) |
| v8a 确认性 | 288 个 trial；第一阶段 A：manual 29/48、drafted 36/48、review 44/48、given 47/48；最终 96 对未证明 review 在 10 pp 内非劣 | [v8a](v8a-confirmatory-results.md) |
| v9 确认性 | 288 个 trial，均已观察；A：given 92/96、无规则起草 67/96、有规则起草 93/96；event 错误 28→0 | [v9](v9-confirmatory-results.md) |

v3–v5b 共 362 个计划 trial；v6–v9 共 1,200 个计划 trial。任务和条件不同，不合并成总体成功率。未测量不冒充失败或成功。v7 的 44/80 是报告的计划分母口径，实际已观察 79；统计推断使用有效配对。

已有证据支持：平台提供正确领域事实，并由内核执行时，固定任务上的完整验收成功率提高。v9 支持规则、登记表及即时反馈的组合效果；每任务只有一个未使用合法事件，未验证多候选业务判断。10 pp 非劣效不等于等效。跨版本成本差异不是随机对照，credit 未包含平台建设、维护和人工审阅成本。

## 公开实现与私有能力

| 位置 | 实际内容 | 不要误认为 |
|---|---|---|
| `cmd/server` / `apps/zenstack` / `apps/web` | 原 API 和共享 UI | 全部实验候选已应用进主应用 |
| `cmd/action-server` | 事务化批量审批，其他 API 委托原服务 | UI 已迁移到目录内核 |
| `internal/opdef` / `internal/opkernel` | 五个公开条目；字段修改仅管理 `assigneeId`，记录 `AssignmentHistory` | 已支持任意实体、字段和历史表 |
| `benchmark/phase3` 至 `phase6b` | 模板、Oracle、控制、导出和交接 | 已部署的新业务功能 |
| `goodloris/oracle`（原私有，现公开） | v7–v9 任务、独立 Oracle、原始证据、复杂内核叠加层 | 所有已测平台能力均已公开 |

v8b 的跨表前置条件、时间窗、逐项更新，以及其他新字段能力仍在 oracle 的实验叠加层。本次整合不自动迁移它们。后续仅提取可复用平台代码和独立公开控制，保留隐藏任务、seed、Oracle 私有。

消费者登记规则证明事件在受保护清单中；目录双向检查不证明真实消费者已部署或正确处理。实际 notify 仍是分期 fixture。见 [操作语义设计](operation-semantics.md)。

## 环境与回归

需要 Go 1.25+、Node 24、Python 3.12+、可销毁 PostgreSQL 17+。CI 使用 17.6；私有验收使用 digest 固定的 17.2 glibc。不同控制不能并发重置同一个数据库。

```bash
git fetch origin
git worktree add -b chore/next-closeout ../enterprise-agent-next origin/main
cd ../enterprise-agent-next
git status --short
git rev-parse HEAD
docker run --rm --name enterprise-agent-poc \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=enterprise_agent_poc \
  -p 5432:5432 postgres:17.6
```

在另一终端进入同一 checkout 后顺序执行：

```bash
export ADMIN_DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/enterprise_agent_poc
export DATABASE_URL=postgres://app_runtime:poc-only@127.0.0.1:5432/enterprise_agent_poc
export POC_DB_RESET=1
make deps test
python3 -m unittest discover -s benchmark/tests -v
python3 tests/extension_controls.py
python3 tests/operation_controls.py
python3 research/verifier_extension_rules.py --out reports/verifier-extension-rules
python3 research/operation_semantics_replay.py --out reports/operation-semantics
python3 benchmark/phase4/driver_smoke.py
python3 benchmark/phase5/driver_smoke.py
python3 benchmark/phase5b/driver_smoke.py
python3 benchmark/phase6a/driver_smoke.py
python3 benchmark/phase6b/driver_smoke.py
POC_REQUIRE_DB=1 go test -race -count=1 -v ./internal/opkernel/
```

完整旧路径、浏览器、v2 和 v3 控制以 [CI YAML](../.github/workflows/poc.yml) 为清单。使用干净 worktree，部分控制拒绝覆盖已有报告。`scripts/report.py` 要求完整 CI 报告集合；缺任何必需项都会失败。CI 不调用模型。

这次收尾把 v6a（15 个人工候选，每份 193 项）和 v6b（16 个，每份 190 项）接入真实 PostgreSQL CI，并强制最终 report 核对；每期另有两次假 Kiro 传输控制、8 个孤立约定负控制及 5 个显式缺陷控制。

## 生成、隔离与续跑

Kiro 后端已在 `benchmark/phase3/generation_worker.py` 实现，支持 `plan --engine kiro`，保留 Codex 路径。公开命令见 [v3 指南](phase3-action-evolution.md) 和 [隔离说明](network-isolation.md)。模型参数兼容性以相应实验记录为准，haiku/qwen 不支持 effort。v7–v9 使用私有 harness，公开 v3 命令不能直接替代它。

旧计划在原 `harness_sha` 恢复，不修改 checksum 消除漂移报错。生成采用 macOS Seatbelt 与 CONNECT 代理，验收分属独立主机；Seatbelt 不是 VM，模型服务端检索无法由出口代理完全观测，新的 Oracle/seed 必须对生成端不可访问。

最近验证的名单，顺序沿用冻结记录；端点变化时重新 discovery 和隔离控制：

```bash
export KIRO_ALLOW_HOSTS=cognito-identity.us-east-1.amazonaws.com,oidc.us-east-1.amazonaws.com,management.us-east-1.kiro.dev,runtime.us-east-1.kiro.dev
```

新队列的 READY 超时应预注册。本次不修改旧 worker 的 30 秒和历史规则。

### 未开始生成的阻塞归档

新增 `scripts/archive_preflight.py`，用 UTC 日期目录中的 `<trial>--0001/result`、`--0002/result` 保存重复阻塞。只允许 plan 中唯一 trial、明确 `ready=false`、无 generation/receipt/outcome/未知证据的目录；拒绝符号链接。移动失败非零退出，原目录保留；已有归档从不覆盖。

先停止 lane。对匹配公开 worker 输出格式的目录：

```bash
python3 scripts/archive_preflight.py --run /absolute/lane --trial given--011 && \
  bash /absolute/operator-owned/resume-lane.sh
```

后半段是操作员已有、核对同一计划的续跑入口，不是本仓库的脚本。必须用 `&&` 或检查退出码，不在归档失败后继续。归档工具不生成、不重试、不改计划，不以目录存在推断 trial 已完成。

**oracle v9 续跑源码修复已完成为补丁，尚未写回或部署。** 源码现在可读，已核对真实两阶段输出形状；补丁修复重复归档、移动失败传播、传输恢复、数据库槽位复用、后台失败和缺失评分。13 项真实 Bash/Python/tar、模拟 SSH/DB 控制通过，冻结分析脚本自检通过。写回受到 GitHub App 的 HTTP 403 限制；完整应用步骤、补丁和 CI 控制见 [oracle-v9-resume-handoff.md](oracle-v9-resume-handoff.md)。本仓库中的简易 archive_preflight 工具不替代新的 oracle 多阶段驱动修复。

历史 v7–v9 的隐藏条件以采集时为准，公开后的旧任务只能用于复现/工程控制；未来盲测必须使用新的、生成端不可访问的任务与 Oracle。

历史证据锚点（现已公开）：v7 `main` / `ea3dc6f`；v8a `v8a` / `f387120`；v8b `v8b` / `f9a0d5d`；v9 `v9` / `e75c744`。验收主机 `root@10.30.11.112` 仅使用 `/opt/poc-oracle`，原交接报告根盘 97%，使用前确认空间，不自动清理历史证据。主机无 GitHub 凭据。本次没有连接或修改该主机。

## 待办与验收

| 优先级 | 工作 | 完成标准 |
|---|---|---|
| 已完成 | 公开工程整合 | PR #33 已合入 main；保留 51 个原始提交；旧 PR 已收拢；main 六项 CI 通过 |
| P0 | Oracle 补丁写回与部署 | 补丁和 13 项控制已准备；在有写权限的环境创建 v9 PR；部署时核对冻结 harness，不重跑已有观察值 |
| P1 | 平台能力迁移 | 从 oracle 实验叠加层提取跨表条件/时间窗/逐项更新；独立控制与旧条目回归；未来新隐藏 Oracle 单独保存 |
| P1 | 消费者契约 | 实际消费者与载荷版本联动检查；识别未部署和不兼容 |
| P1 | 多合法事件 | 包含合法事件互换反例；权威判断依据独立于待审条目；新私有 Oracle 与预注册 |
| P1 | 持续演进与总成本 | 多次改阈值、权限、指纹、事件版本；计入平台扩展、审查、修复与回归成本 |
| P1 | 旧 API / UI 接入 Action | 幂等 UX、兼容性、真实浏览器回归及完整审计 |
| P2 | 第二业务域及生产治理 | 新领域独立验收；OIDC、独立发布权限、扩展隔离与资源治理 |

## Kiro 接手任务

```text
请接手 enterprise_agent 与 oracle v9 的工程收尾。先读 docs/HANDOFF.md、
docs/oracle-v9-resume-handoff.md 和 Issue #32。PR #33 已合并，旧 PR 已收拢，
main 的六项 CI 通过；v3–v9 和 Kiro 后端均已完成，不重做 pilot。

在有 goodloris/oracle 写权限的环境，基于固定提交 e75c744 应用 maintenance/
oracle-v9-resume.patch，运行 13 项控制并创建以 v9 为 base 的 PR。不要移动
验收主机的冻结 harness checkout，不重跑已有 trial，不修改历史评分或证据。
源码修复尚未部署到真实主机；先核对运行进程、槽位、空间与冻结计划。

然后独立 PR 提取实验叠加层的通用能力。oracle 旧任务已公开，只能复现；
新的确认性盲测必须准备新的隐藏任务和独立 Oracle，先冻结预注册与预算。
使用独立 worktree/分支和 PR，不直接 push main；交付实际证据和剩余限制。
```
