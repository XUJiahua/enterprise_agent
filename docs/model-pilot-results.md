# 真实 AI Coding pilot — 2026-11-14

当前 Linux 接手环境已解除原运行阻塞，Codex CLI 0.166.0 的真实 READY 预检通过。没有迁移认证文件、修改 HOME/CODEX_HOME 或降低现有权限规则。前一环境 CLI 0.167.0-alpha.4 的只读错误仍按 [历史记录](model-pilot-status.md#前一环境的阻塞记录) 保留为 0 个观测、9 个未测量，不能改写为失败样本。

## 冻结设计与证据

请求模型 `gpt-6.1-sol`，medium，每项生成预算 120 秒；不自动修复，每项仅 attempt 1。CLI 没有在事件中报告不可变服务端模型快照或账单费用，两者均未知；usage 为 CLI 实际报告值，缓存输入是输入的子集，不能相加后当总输入。

候选基准为 `70fb44504af2e711a19ccd82c1c67636ce253cfe`，在接手快照上仅增加本机隔离端口和本地汇总修正，业务任务及预期值不变。真实生成请求使用 `workspace-write`，预检使用 `read-only`。数据库为独立 Docker PostgreSQL 17.6，宿主 loopback 55434，模型候选服务端口 18084。原有机制/浏览器回归使用另一个 `_poc` 数据库和 18085，互不重置模型验收数据。

- [环境记录](evidence/model-pilot-success-2026-11-14/environment.json)
- [真实 doctor](evidence/model-pilot-success-2026-11-14/doctor-handoff-001/preflight.json)
- [首批三路径计划](evidence/model-pilot-success-2026-11-14/pilot-handoff-001/plan.json)、[batch](evidence/model-pilot-success-2026-11-14/pilot-handoff-001/batch.json)、[完整分母汇总](evidence/model-pilot-success-2026-11-14/pilot-handoff-001/summary.json)
- 九项试验的计划、batch、逐项验收、原始事件、补丁和 bundle 归档在同级 `pilot-handoff-003/`。

`pilot-handoff-002-plan.json` 曾在本地冻结，但没有启动执行；发现候选依赖准备问题后，用新 harness 冻结 003，未将 002 伪装成已执行试验。

首批三路径的 harness 为 `70fb445`。其中 ZenStack worktree 缺少被 Git 忽略的生成 schema，模型的 typecheck 报缺文件；独立验收在生成 schema 后通过。该样本保留并标注，不能因准备缺口丢弃。随后提交 `4b65cb6` 修复 runner：在模型预算开始前调用已安装的 ZenStack CLI 生成 schema。003 使用这一 harness，候选业务基准保持 `70fb445`；准备日志单独归档。两个队列分别报告，不混合成有相同条件的十二次重复。

## 首批租户汇总 pilot

| 路径 | 计划 | 独立验收 | 首次通过 | 未测量 |
|---|---:|---:|---:|---:|
| baseline | 1 | 1 | 1 | 0 |
| zenstack | 1 | 1 | 1 | 0 |
| guarded | 1 | 1 | 1 | 0 |

三个候选均通过任务 Oracle 和原有 16 项回归。三路径首次通过率均为 1/1，仅为链路 pilot。

## 三任务 × 三路径完整队列

| 任务 | 路径 | 状态 | 验收用例 | 生成秒 | 验收秒 | 输入 token（含缓存） | 缓存输入 | 输出 token |
|---|---|---|---:|---:|---:|---:|---:|---:|
| tenant-summary | guarded | passed | 20/20 | 48.044 | 1.019 | 186499 | 175488 | 1140 |
| tenant-summary | baseline | passed | 20/20 | 34.26 | 0.999 | 103615 | 87040 | 994 |
| title-search | guarded | passed | 23/23 | 30.442 | 1.009 | 97947 | 88576 | 819 |
| status-filter | guarded | passed | 22/22 | 50.967 | 1.102 | 164690 | 144768 | 1277 |
| status-filter | zenstack | passed | 22/22 | 60.609 | 4.562 | 100424 | 85120 | 1934 |
| status-filter | baseline | passed | 22/22 | 40.212 | 1.143 | 118380 | 98432 | 951 |
| title-search | zenstack | passed | 23/23 | 56.451 | 5.605 | 119121 | 100736 | 1293 |
| title-search | baseline | passed | 23/23 | 35.949 | 1.046 | 124952 | 114560 | 946 |
| tenant-summary | zenstack | passed | 20/20 | 48.882 | 4.7 | 99642 | 84992 | 1584 |

计划 9、已观察 9、通过 9、未测量 0；完整队列首次通过率 9/9。无人工修复、无重试或挑选。三个路径在这三个读任务上都通过，因此本次 pilot 无法区分护栏收益。

[完整计划](evidence/model-pilot-success-2026-11-14/pilot-handoff-003/plan.json)、[批次](evidence/model-pilot-success-2026-11-14/pilot-handoff-003/batch.json)、[按路径分母](evidence/model-pilot-success-2026-11-14/pilot-handoff-003/summary.json)。每项候选 SHA、原始 usage、模型事件、补丁和 bundle 保存在 `generation/<trial>/`；逐用例结果及运行日志保存在 `attempts/`。

表中只计算生成回合，不包含 doctor、依赖准备和验收调用的时间/token。预检 usage 单独保存在 preflight.json。费用未上报，不能由 token 推算成实付金额。

## 原有验证与解释边界

本地重新执行 Go race tests、vet、12 项 Python 协议/状态测试、7 组静态对抗探针、三条路径机制实验、共享 Next.js production UI 的 18 项 Playwright 验收，以及 4 次独立验收器 synthetic 控制和 1 次 runner synthetic 控制。完整本地汇总正常生成，CI Secret 状态明确为 unknown。[机制汇总](evidence/model-pilot-success-2026-11-14/regression/comparison.md) 与真实模型队列分开。

健康业务验收各 16/16；人工注入的 baseline 阻断 0/5、ZenStack 和 guarded 各 5/5，直接 SQL 6/6、错误启动拒绝 2/2。人工注入只证明已覆盖缺陷的机制行为；synthetic 只检验工具链，二者都不计入真实模型样本。

这些任务仅是三个读查询扩展，Oracle 公开且与代码同仓库，候选仍是可信代码模式；路径检查和 worktree 不提供不可信代码沙箱。模型可能看到当前用户安装的全局 Codex 配置/技能，执行环境还没有完全封装成可移植镜像。小样本结果不能推断统计显著性、整体缺陷率下降或生产安全认证，也不能证明 RLS 对持有凭据的同进程恶意代码不可绕过。

后续工作见 [#5：分批恢复、合并与安全 CI](https://github.com/XUJiahua/enterprise_agent/issues/5)、[#6：流程对照、重复样本与独立隐藏 Oracle](https://github.com/XUJiahua/enterprise_agent/issues/6)。

## 重放

先 checkout 对应计划的 `harness_sha` 并准备 README 中的依赖、可销毁测试数据库及空闲端口。003 的候选 bundle 为相对于 `base_sha` 的增量，不能脱离包含基准的仓库单独 clone。示例：

```bash
# 在包含 base_sha 的独立 candidate checkout 中
# BUNDLE 使用归档目录下某个 generation/<trial>/candidate.bundle 的绝对路径
git bundle verify "$BUNDLE"
git fetch "$BUNDLE" HEAD
git checkout --detach FETCH_HEAD
# ZenStack candidate 需要已安装 node_modules；验收器会生成 schema。
# 在可信 harness checkout 执行，plan / candidate / output 使用绝对路径：
python3 benchmark/benchctl.py evaluate --plan "$PLAN" --trial "$TRIAL" \
  --candidate "$CANDIDATE" --attempt 1 --output "$NEW_OUTPUT"
```

已实际从首批 baseline 的归档 bundle 恢复候选，并在冻结的 `70fb445` harness 上重新执行独立验收，20/20 通过。重放结果保存在 `replay/`，不计为新增模型样本。

原始证据目录不可覆盖。归档未包含认证文件、node_modules 或编译后的 server 二进制；它们由固定基准、补丁/bundle 和依赖锁重建。
