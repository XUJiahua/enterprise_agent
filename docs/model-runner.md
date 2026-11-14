# 模型试验执行器

本轮接入 `codex exec`，执行器能完成预检、按计划生成代码、约束改动范围、提交候选快照，再调用可信 checkout 中的独立验收器。执行顺序固定为计划中的随机顺序，一次最多九个 trial，不并发，不自动修复。

## 先确认真实运行条件

```bash
python3 benchmark/codex_runner.py doctor \
  --model gpt-6.1-sol --reasoning-effort medium \
  --output reports/model-doctor-001
```

这里的模型名是请求标识，不表示服务器已经确认了不可变快照。`doctor` 真正发起一个只要求回复 READY 的 read-only 调用，不读取业务文件，也不修改认证配置。需要已有可正常执行 `codex exec` 的安装与登录状态。它不会迁移凭据、覆盖 HOME / CODEX_HOME、绕过审批或降低沙箱权限。

输出包括 CLI 版本、是否发生 `turn.started` / `turn.completed`、实际事件数、用量（若上报）和诊断日志。`codex login status` 成功不代表执行器可用，必须检查实际预检。

## 冻结并运行一个小型试验

提交可信 checkout 的代码，按 README 准备数据库及依赖。第一次建议只测租户汇总任务：三个技术路径各一次。

```bash
python3 benchmark/benchctl.py plan \
  --base HEAD --model gpt-6.1-sol --task tenant-summary \
  --repetitions 1 --time-budget 120 --reasoning-effort medium \
  --output reports/pilot-001-plan.json

python3 benchmark/codex_runner.py run \
  --plan reports/pilot-001-plan.json \
  --output reports/pilot-001
```

删除 `--task tenant-summary` 可得到三个任务 × 三条路径，共九个 trial。默认 10 次重复的 90-trial 计划仍可用于独立验收，但本执行器不会自动消耗这么大的生成批次。可用 `--trial <exact-id>` 只执行一个指定 trial。

ZenStack 候选 worktree 会先使用已安装 CLI 生成被 Git 忽略的 schema，准备日志保存在 `preparation/`，这一步发生在模型预算开始前。环境可用 `POC_HTTP_PORT` 选择非默认后端端口，服务与独立验收必须使用同一值。

模型生成统一使用 `workspace-write` 和计划中的 reasoning effort。只允许 Go 路径修改 `internal/app/`，ZenStack 路径修改 `apps/zenstack/src/`，因为当前三个任务都是现有资源的读取扩展，不要求改动全局策略、迁移或依赖。修改验收器、CI、受保护规则等文件会被独立判定为范围违规。

运行目录不可覆盖。每个 candidate 保留独立 Git worktree；原始源码不直接推送到远端。

## 证据与分类

| 文件 | 内容 |
|---|---|
| plan.json | 冻结计划副本，含提示词、base SHA、预算及 evaluator/runner checksum |
| preflight.json、probe/ | CLI 运行条件预检与原始事件/诊断 |
| generation/<trial>/events.jsonl | 模型事件流 |
| generation/<trial>/invocation.json | 请求模型、推理参数、时间、退出码及实际上报的 token |
| generation/<trial>/candidate.patch | 候选源码改动 |
| generation/<trial>/candidate.bundle | 含候选 commit 的 Git bundle；基准 commit 由计划提供 |
| attempts/ | 原有独立验收器的逐用例报告与构建/运行日志 |
| batch.json | 执行状态、已观察/未测量数量；完整队列才计算首次通过率 |

| 状态 | 解释 |
|---|---|
| runtime-blocked / provider-error / protocol-error | 运行、服务或事件协议不可用；不当成代码缺陷 |
| budget-exceeded | 已进入模型回合，但未在生成预算内结束；记录为预算失败 |
| scope-violation | 模型已完成，但改动越出试验允许范围 |
| acceptance-failed / candidate-error | 独立验收发现业务失败，或候选构建/启动失败 |
| evaluation-blocked | 独立验收基础设施不可用，保留为未测量 |

CLI 退出码：`0` 表示所选批次执行完成（不代表所有候选通过）；`3` 表示真实运行条件阻塞；`1` 表示参数/计划/输出配置错误。候选代码的通过与否见逐 trial 结果。

模型生成耗时与独立验收耗时分开记录。缺失 usage 是 null，不会补零；缓存输入与总输入分别保留。没有根据 token 或 ChatGPT 订阅推算账单费用。若服务未报告实际模型快照，只能报告 requested model。

预检失败时，例如计划九个 trial、没有发生模型回合，batch 会记录 `observed_trials: 0`、`unmeasured_trials: 9`、`first_pass_rate: null`。不会把它写成模型“0/9 通过”。只执行一个 trial 的 `selected-completed` 也不代表整个实验已经完成。

## Public CI 的作用

普通 PR 检查不运行付费模型，也不导出 ChatGPT 登录数据。CI 会：

- 检查协议与失败分类；
- 用显式 `synthetic-smoke` 驱动测试真正的候选改动、Git 快照和独立 HTTP 验收；
- 检查预检阻塞时没有候选结果、没有数字通过率；
- 检查 synthetic 驱动不能用于 model-trial 计划；
- 只记录约定名称 `OPENAI_API_KEY` 对当前 CI run 是否配置，不读取或上传它的值。

该布尔检查不证明服务可用、余额足够，也不检查其他名称的凭据。若后续在 public CI 执行模型生成，应使用官方 Codex Action 的凭据代理与独立评估 job，不把 API Key 放进执行仓库代码的 job-level 环境。

本执行器仍采用可信代码模式。不同 worktree 和允许路径检查不是恶意代码沙箱；当前公开任务/Oracle 也不是盲测。研究结论需要更完整的隔离和重复试验。

参考：[官方非交互执行说明](https://learn.chatgpt.com/docs/non-interactive-mode)、[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。
