# 原子批量审批四组真实 pilot — 2026-11-14

本轮接续已验证的 PR #8 / #9，使用原有 v2 导出器、四组提示词、评分和固定业务基准，运行一次四组真实生成。不重复实现前置控制，不改写 v1 的十二个模型候选或既有人工控制。

## 冻结条件

- 业务源代码：`f6c8dc27bf2a00ff74f16d0b9041a94a8e79f1c7`。
- 可信 harness：`d4f79b52c8f13005524e7d524406b104f71d0aa8`，干净 checkout 冻结，文件及提示词 hash 见计划。
- 请求模型 `gpt-6.1-sol`，medium，每项 180 秒，CLI 0.166.0；实际服务端不可变模型版本、费用未知。
- 一块四次独立生成，顺序为 runtime+process → process → ordinary → runtime；各组同一导出内容、同一私有 fixture 块，无人工修复，无候选重试。
- 生成请求 sandbox 为 workspace-write；源码导出在 evaluator checkout 外，各自单提交 Git 仓库，无 remote、无旧历史；改动范围仅 `internal/app/`。
- 本机独立 PostgreSQL 17.6 测试容器，专用 `_poc` 数据库、loopback 55434；独立验收 HTTP 18084。模型进程不获得数据库 URL、重置许可或私有 fixture 文件路径。
- 四组均未提供公共测试服务或 PUBLIC_DATABASE_URL；可运行 `go test ./...`，可见 smoke 脚本随导出提供，但 HTTP smoke 需要自备公开服务。缺少这项开发反馈是本轮条件，不能直接外推到反馈完整的开发环境。

首次 doctor 发生 WebSocket TLS 重连后最终返回 READY 和 turn.completed，但既有解析器仍标记 provider-error。原始记录保留为预检诊断，不计入四组样本；第二次 doctor 和批次自己的预检均通过。没有更换模型、认证、HOME/CODEX_HOME 或修改既有解析规则来获得通过。

CLI/全局配置仅保存文件 fingerprint，不导出配置内容或认证文件。usage 逐项使用原始上报值，缓存输入是输入子集；预检消耗单列，不混入候选生成耗时。缺失 usage 不补零，不从 token 推算账单。

四组各计划 1、已观察 1、首次通过 1、未测量 0，均为 46/46。本轮未观察到流程或 Runtime 对首次正确性的差异。单次耗时和缓存条件不能用于判断平均性能。

## 首次结果

| 组 | 已观察/计划 | 首次通过 | 未测量 | 状态 | 验收用例 | 生成秒 | 验收秒 |
|---|---:|---:|---:|---|---:|---:|---:|
| go-runtime-process | 1/1 | 1/1 | 0 | passed | 46/46 | 178.048 | 2.184 |
| go-process | 1/1 | 1/1 | 0 | passed | 46/46 | 148.288 | 1.909 |
| go-ordinary | 1/1 | 1/1 | 0 | passed | 46/46 | 142.179 | 1.976 |
| go-runtime | 1/1 | 1/1 | 0 | passed | 46/46 | 117.189 | 1.892 |

| 组 | 授权功能 | 逐行授权 | 整批原子性 | 输入校验 | 并发 | 原有回归 |
|---|---:|---:|---:|---:|---:|---:|
| go-runtime-process | 4/4 | 5/5 | 5/5 | 15/15 | 1/1 | 16/16 |
| go-process | 4/4 | 5/5 | 5/5 | 15/15 | 1/1 | 16/16 |
| go-ordinary | 4/4 | 5/5 | 5/5 | 15/15 | 1/1 | 16/16 |
| go-runtime | 4/4 | 5/5 | 5/5 | 15/15 | 1/1 | 16/16 |

| 组 | 输入 token（含缓存） | 缓存输入 | 输出 token |
|---|---:|---:|---:|
| go-runtime-process | 280563 | 260992 | 5987 |
| go-process | 225108 | 192000 | 5538 |
| go-ordinary | 187652 | 153984 | 4553 |
| go-runtime | 233480 | 221696 | 4525 |

[冻结计划](evidence/phase2-model-pilot-2026-11-14/plan.json)、[完整分母](evidence/phase2-model-pilot-2026-11-14/summary.json)、[环境](evidence/phase2-model-pilot-2026-11-14/environment.json)、[批次记录](evidence/phase2-model-pilot-2026-11-14/run/batch.json)。


本地执行协议/计划测试 18/18 通过。前置人工与假驱动控制依照 PR #9 的已成功 CI（run 43596087849）作为已验证基础，本轮未重做该阶段。模型源码只保存在候选目录，可信业务源码及评分规则均未改动。

## 证据与复现

证据目录包括 plan、环境记录、所有预检日志、导出证明、每个候选的模型事件/usage/补丁/完整 bundle，以及独立验收的 46 项结果和运行日志。原始源码及评分文件可由固定 harness 提交恢复。完整 bundle 包含导出初始提交，可在新目录 clone；导出证明保存在 evaluator 端，不在候选中。

私有 seed 文件不进入 Git、PR 或公共 artifact。计划只保存 checksum；操作员的原文件持久保存在仓库外。精确独立重放需操作员使用匹配 checksum 的私有文件：

```bash
# 在冻结 harness checkout 中，按 README 准备依赖和可销毁测试数据库
# PLAN、BUNDLE、MANIFEST、PRIVATE_FIXTURES、CANDIDATE、NEW_OUTPUT 均使用绝对路径
git clone "$BUNDLE" "$CANDIDATE"
git -C "$CANDIDATE" remote remove origin # 恢复导出仓库无 remote 的约束
python3 benchmark/phase2/ctl.py evaluate --plan "$PLAN" --trial "$TRIAL" \
  --candidate "$CANDIDATE" --manifest "$MANIFEST" \
  --fixtures "$PRIVATE_FIXTURES" --output "$NEW_OUTPUT"
```

已实际从 runtime+process 的完整 bundle 克隆新候选、移除 clone 自动创建的 remote，再用冻结 harness 与原私有 fixture 验收，仍为 46/46；重放日志与结果保存在证据目录的 `replay/`。

不覆盖原结果、不将重放计为新模型样本。模型任务失败、预算超时、provider 问题和基础设施阻塞分别保留；缺失仍留在计划分母。

## 限制

每组只有一个样本，不能估计总体首次通过率或统计显著性。四组共享固定模型/业务要求/fixture 块，但全局技能配置、服务端模型快照和缓存条件尚未完全封装。源码导出不等于安全沙箱：同宿主同用户仍可能读到目录外文件，公开仓库含评分和参考实现。本轮是可信代码工程 pilot，不是确认性盲测，也不证明任意生成代码无法越权。

本轮未修改生产业务实现；模型补丁均作为实验候选归档。后续是否增加重复块、可见公共测试服务及严格隔离，需要依据逐组失败机制确定，不能通过修复后重跑覆盖本轮首次结果。
