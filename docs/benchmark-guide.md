# 真实 AI Coding 对照实验：任务与独立验收

本轮交付可执行的试验计划和候选代码验收器，还没有真实模型试验数据。`benchctl` 不调用模型；开发者选择的 Agent 在独立 candidate worktree 中执行任务，验收始终从可信 harness checkout 运行。

## 已支持的任务

| 任务 | 需要实现 | 新验收重点 |
|---|---|---|
| tenant-summary | 租户采购总数、总金额接口 | 两租户汇总、审批角色、匿名拒绝 |
| title-search | 不区分大小写的标题子串查询 | 租户隔离；% / _ / 引号为字面字符 |
| status-filter | submitted / approved 筛选 | 正确结果、空/未知参数拒绝、租户隔离 |

每次还执行原有 16 项回归验收。任务不要求迁移数据或扩充 Policy DSL，因此独立验收使用冻结基线的 schema、fixtures 和已批准规则，不执行 candidate 自带的数据库初始化脚本。

## 冻结计划

在可信 checkout 提交所有改动后运行：

```bash
python3 benchmark/benchctl.py plan \
  --base HEAD --model YOUR_EXACT_MODEL_VERSION \
  --repetitions 10 --seed 20261114 --time-budget 300 \
  --output reports/model-experiment/plan.json
```

生成 3 个任务 × 3 条路径 × 10 次重复，共 90 个独立 trial，随机执行顺序可复现。计划固定 base commit、模型标识、提示词、生成预算和验收代码 SHA256。输出不可覆盖。

`--time-budget` 记录模型生成预算，须由实际模型运行器执行；CLI 不会假装已经限制模型推理时间。独立构建有 120 秒超时。`evaluation_seconds` 只统计验收耗时，不能当作 AI 生成时间或费用。

## 执行一个 trial

1. 从计划的 `base_sha` 创建单独 worktree，并把该 trial 的 prompt 和 route 提供给 Agent。不要让 Agent 修改可信 harness checkout。
2. 使用同一模型、推理参数、工具权限和生成预算完成该任务。保留原始模型日志；安装 candidate 的依赖。
3. 将候选代码提交到该 worktree。验收器拒绝脏目录和不以计划 base 为祖先的候选提交。
4. 从可信 checkout 运行验收。数据库环境与 README 一致，须为可销毁测试数据库。

```bash
# 从 plan.json 取精确 trial id；此处示例为第一轮 summary / Go
python3 benchmark/benchctl.py evaluate \
  --plan reports/model-experiment/plan.json \
  --trial tenant-summary--baseline--001 \
  --candidate /absolute/path/to/candidate \
  --attempt 1 --output reports/model-experiment/attempts
```

退出码：`0` 通过，`2` 候选未通过（包括构建/启动错误），`1` 验收配置或基础设施错误。每个 attempt 记录候选 SHA、base SHA、harness SHA、plan checksum、独立用例结果和日志。不得覆盖已有 attempt。修复后提交新版本，以 `--attempt 2` 再验收。

候选只获取运行时数据库地址，不继承管理员连接信息或全部宿主环境变量。此处仍是可信代码实验：同一 OS 账户不是安全沙箱，也不保证恶意代码无法读取磁盘或发现测试口令。

```bash
python3 benchmark/benchctl.py summarize \
  --plan reports/model-experiment/plan.json \
  --results reports/model-experiment/attempts \
  --output reports/model-experiment/summary.json
```

汇总逐路径保留 planned / evaluated / missing / first_pass / latest_pass。缺失项仍在计划分母里；乱序修复历史、重复 attempt、混用计划和验收漂移都会报错。当前没有自动解析 token、账单费用或模型生成耗时，不能据此报告成本优势。

## CI 中验证的是什么

`benchmark/smoke.py` 生成显式标为 `synthetic-smoke` 的计划，并真正执行四次候选验收：

- 三条路径各一次未实现新任务的提交：新任务必须失败，但原有 16 项回归必须通过。
- 在一个候选提交中加入正确的租户汇总实现，再次验收必须通过。
- 同时检查计划可复现、缺失分母、不可覆盖结果、冻结 Oracle 被篡改时拒绝验收。

这些是验收器自身的正反控制。**它们不是 Agent 生成结果，不进入真实模型通过率统计。** CI 不使用模型 Key，也没有运行付费模型。

## 仍需后续补齐

当前任务集公开且只有三个读查询扩展，不是盲测；模型可能看到仓库中的 Oracle。研究性实验应把可信 checkout 放在 Agent 无权限访问的环境，加入隐藏样本、更多写入/关系任务、独立治理规则，并预注册统计方法后再比较可靠性。
