# 原子批量审批：流程 × Runtime 实验 v2

2026-11-14。接续 PR #8 的研究结论，提供独立的四组 Go 实验入口、完整目标集合的业务验收，以及真实 PostgreSQL 下的人工正反控制。**本页前置控制阶段新增真实模型生成数为 0。** 后续四组真实生成与独立验收单独记录在 [真实 pilot 报告](phase2-model-pilot-results.md)。 参考实现只存在于 evaluator 的控制材料中，原应用尚未新增批量审批接口，保留未来模型需要完成的任务。

## 试验要回答的问题

对于同一份 Go 源码和完整业务要求，额外的开发流程指导、RLS Runtime，以及两者组合，是否改变首次实现正确性？本轮先证明执行器和评分能识别缺陷；真实效果与样本规模仍待后续模型 pilot 验证。

| Cell | Runtime | 额外流程指导 |
|---|---|---|
| go-ordinary | baseline，手写授权 | 无 |
| go-process | 同一 baseline | 列不变量/失败场景、运行可用检查、检查 diff |
| go-runtime | guarded，RLS/列权限 | 无 |
| go-runtime-process | 同一 guarded | 同上 |

四组导出完全相同的源码、TASK.md 和可见 smoke。提示词只在 Runtime 标识和流程指导处变化；每一重复块使用同一套私有 fixture，块内顺序由固定 seed 打乱。这里的“无额外流程指导”不限制模型自发测试。ZenStack 参考组暂未实现，不纳入这四组 Go 比较。

## 冻结契约与独立验收

[contract.json](../benchmark/phase2/contract.json) 是机器可读契约。任务为 `POST /purchases/bulk-approve`：1..100 个不同的非空字符串 ID，严格输入对象；只有当前租户 approver 能审批他人创建的 submitted 记录。任何目标不满足条件，整批拒绝且全部数据不变。成功返回排序后的完整 ID 集合。并发重叠批次最多一个成功，失败批次独有的目标也不得更新。

| 评分组 | 用例数 | 检查内容 |
|---|---:|---|
| authorized-functional | 4 | 正常批次、100 个目标、引号/反斜杠 ID、另一审批人 |
| row-authorization | 5 | requester、匿名、跨租户、自批、伪造上下文 |
| batch-all-or-nothing | 5 | 混合无权/自批/已审批/不存在目标，以及重复审批 |
| input-validation | 15 | 重复、空、超量、错误类型、超长、未知字段、错误根对象和无效 JSON |
| concurrency | 1 | 两个重叠并发批次，检查胜者完整成功与败者完整回滚 |
| original-regression | 16 | 冻结的原有 API 验收 |
| 合计 | **46** | 每个候选都需全部通过 |

Oracle 使用 HTTP 和管理员数据库快照独立判断结果，不调用候选代码的授权函数。快照检查所有行、所有现有字段，错误响应和数据库副作用同时验收。HTTP 5xx、不可用、只返回部分成功，都不能算安全通过。原有单条审批并发与身份切换检查仍包含在 16 项回归中。

执行器每个候选先重建可销毁数据库，再逐项重置 fixture。数据库初始化/RPC 故障标记为基础设施阻塞；构建/启动失败标记候选错误。端口冲突等配置错误退出，不能产生通过报告。模型生成预算与验收耗时分开记录。

## 为什么需要正反控制

`benchmark/phase2/smoke.py` 在真实 Go + PostgreSQL 上运行三个人工实现 × 四个 cell，共 12 次候选验收，且不调用模型：

| 人工控制 | 预期 |
|---|---|
| correct-reference | 四组均通过 46 项；事务内锁定目标、检查完整集合、更新后再次检查 |
| eligible-subset | 四组均被发现缺陷；逐行授权和正常功能可过，但混合批次部分提交，并发败者也可能提交独有行 |
| reject-all | 四组均被发现缺陷；全部拒绝无法完成授权业务操作 |

本地 [控制证据](evidence/phase2-controls-2026-11-14/controls.json) 和 [驱动控制](evidence/phase2-controls-2026-11-14/driver-control.json) 记录了环境、冻结文件 hash 与分组结果。正确参考的四组全部通过，八个负控制全部被发现。

这些结果证明评分有能力区分所列控制，**不是 AI 首次通过率**。另外 `driver_smoke.py` 用明确标记的假 CLI 检查预检、提示词、候选提交、patch/bundle、独立验收与缺失样本汇总；该控制也没有模型调用。

Public CI 在 baseline job 内顺序执行上述控制。该 job 名称不表示只测 baseline Runtime；控制实际上覆盖四个 cell。最终 report 要求全部控制证据存在，缺失即失败。原有三路径、UI、归档审计和 v1 验收继续运行。

## 导出与信任边界

v2 不复用整个仓库的 worktree 作为模型输入。候选目录位于 evaluator checkout 之外，只包含：

```text
go.mod
go.sum
cmd/server/main.go
internal/app/server.go
TASK.md
visible_smoke.py
.gitignore
```

每个导出建立一个新的单提交 Git 仓库，无 remote、无旧历史；不能通过 `git show` 读取此前仓库中的答案。可信端另存导出 hash、原源码 SHA、初始候选 SHA 和 plan hash，并验收仅 `internal/app/` 被修改。生成阶段不传入私有数据库 URL、管理员 URL 或重置许可。候选运行进程只收到运行时数据库身份。

**这仍然不是安全沙箱，也没有实现严格盲测。** 同一宿主/同一用户可能读取导出目录外的文件；公开仓库也包含 evaluator 源码和人工参考实现。路径排除、提示词约束和 hash 校验不能阻止主动外部检索或同宿主读取。确认性隐藏验收需要独立 OS/容器/CI job，只把候选导出挂载给生成端，并使私有验收和数据库在生成结束后才可用。当前机制适合可信代码的工程 pilot，不适用于执行任意不可信代码。

私有 seed 由操作员生成，只把文件 checksum 写入计划；同一重复块的四组使用相同 seed 派生数据。这避免把具体测试 ID 直接交给模型，但不代表新增了不同业务场景，重复任务的外推范围仍有限。

## 本地及 CI 控制复现

依照 README 准备 Go、Node、Python、ZenStack 的 `pg` 依赖以及可销毁 PostgreSQL，并设置 `ADMIN_DATABASE_URL`、`DATABASE_URL`、`POC_DB_RESET=1`：

```bash
python3 -m unittest discover -s benchmark/tests -v
python3 benchmark/phase2/smoke.py
python3 benchmark/phase2/driver_smoke.py
```

输出分别位于 `reports/phase2-controls/` 和 `reports/phase2-driver-control/`。程序拒绝覆盖既有输出；复现时使用新 checkout。`POC_HTTP_PORT` 默认 8080；整个实验顺序占用同一个数据库和端口，不能并发共用。

## 真实模型 pilot 的运行方法

在可正常执行 `codex exec` 的环境操作。保留正常认证配置，不迁移凭据、不修改 HOME/CODEX_HOME 来绕开权限。先提交并冻结 evaluator 源码；真实队列拒绝 dirty harness。PR CI 无需模型密钥，也不会自动调用模型。

以下 `MODEL_ID` 使用事先选定且可用的模型。`PRIVATE_DIR` 应是生成端不能访问、可长期保存的操作员目录；当前同宿主路径本身不提供隔离。真实确认性试验需先补齐上一节的隔离部署。

```bash
export PRIVATE_DIR=/absolute/operator-owned/phase2-private
export MODEL_ID=your-fixed-model-id
python3 benchmark/phase2/ctl.py fixtures --output "$PRIVATE_DIR/fixtures.json"
python3 benchmark/phase2/ctl.py plan \
  --base f6c8dc27bf2a00ff74f16d0b9041a94a8e79f1c7 \
  --model "$MODEL_ID" --fixtures "$PRIVATE_DIR/fixtures.json" \
  --repetitions 1 --time-budget 180 --reasoning-effort medium \
  --output reports/phase2-pilot-plan.json
python3 benchmark/phase2/ctl.py run \
  --plan reports/phase2-pilot-plan.json --fixtures "$PRIVATE_DIR/fixtures.json" \
  --output reports/phase2-pilot-run
python3 benchmark/phase2/ctl.py summarize \
  --plan reports/phase2-pilot-plan.json \
  --inputs reports/phase2-pilot-run/evaluations \
  --output reports/phase2-pilot-summary.json
```

`--base` 固定为原功能源码所在提交，`harness_sha` 和文件 hash 另行冻结；两个身份不应混淆。默认是一块四次独立生成，每次180秒，推理参数一致。一个批次最多八项；可用 `run --trial go-runtime-process--001` 选一项，输出到新的目录。候选目录默认另建在系统临时目录，也可通过 `--candidate-root` 选择 evaluator checkout 之外的持久目录；patch、完整 bundle 和事件则归档到输出目录。

若希望模型运行可见 HTTP smoke，操作员需另外启动只含公开 fixtures 的测试服务；`PUBLIC_DATABASE_URL` 只能指向单独的公共测试数据库。CLI 不会自动创建该服务，不应把独立验收使用的私有数据库借给模型。

`run` 退出 0 表示所选批次已完成执行，**不表示所有候选验收通过**；判断代码质量需查看 `evaluations/*/result.json`。退出 3 表示预检、生成服务或验收基础设施阻塞，退出 1 表示配置/完整性错误。`evaluate` 单独运行时以 0/2/3 表示通过/候选失败/基础设施阻塞。

原始 usage 缺失仍记 unknown，不补零。汇总验证计划和候选身份，拒绝重复 trial 和外来队列；可多次传入 `--inputs` 合并不相交的批次。未完成和基础设施阻塞保留在计划分母中，某 cell 未全部测量时首次通过率为 null。synthetic 与真实计划不能混用假驱动。

当前没有自动 crash resume、合并多次 attempt 或自动重试调度。进程中断后保留已有记录，明确检查未测量 trial，再选择该项写入新的输出目录；不要自动重跑失败候选来改写首次通过结果。还需要记录 CLI/全局配置、模型服务版本与缓存条件，评估可用的不可变模型快照，并在真实 pilot 后决定确认性任务集和样本规模。

本轮不改写 PR #7 的旧计划、候选或首次通过率，也不把 12 次人工候选验收与一次假驱动控制加入真实模型样本。
