# v3 首个真实 pilot：Kiro CLI 引擎

运行时间 2026-11-15（Asia/Shanghai）。这是 v3「手写事务 vs 平台 Action」的**第一块真实两组样本**，生成引擎为 Kiro CLI，单独注册为新队列，不与历史 Codex v1/v2 样本合并。

## 结果

| trial | 冻结 66 项验收 | 分组 | 生成耗时 | 计量 |
|---|---|---|---|---|
| go-manual--001 | **acceptance-failed，50/66** | 功能 0/6、输入 22/24、幂等 0/6、并发 0/3、故障回滚 0/2；授权 5/5、原子性 4/4、原有回归 16/16 | 69.2 s | 2.20 credits |
| go-action--001 | **passed，66/66** | 全部通过 | 33.6 s | 1.27 credits |

完整分母为 2，已观察 2，未测量 0。按冻结计划，首次通过率 go-manual 0/1、go-action 1/1。Kiro 上报的是计量 credit，不是 token；token 数保持未知，不做估算。

**这个差异不能解读为平台 Action 提升了正确性。** go-manual 的 16 项失败全部出于同一个原因：冻结 Oracle 要求 `ActionRequest.requestHash` 与参考内核的指纹公式逐字节一致（`operation + "\n" + Σ len:id`，取 SHA-256），而任务要求、提示词和公共 `SCHEMA.sql` 都没有规定这个公式，Schema 只约束为 64 位小写十六进制。go-manual 自行选用了 `sha256(json{operation,ids})`。go-action 调用了内核，公式自然一致。

事后诊断（`research/phase3_kiro_hash_diagnostic.py`）只把这一项相等检查放宽到 Schema 约束，其余 66 项保持原样，包括幂等重放、同 key 不同集合返回 409、并发和故障回滚的 HTTP 及三表快照检查。结果 go-manual 66/66，go-action 66/66（正对照）。所以在已规定的行为上，两组都没有被观察到缺陷。诊断结果单独保存，**不改写冻结首次成绩**。

这属于 harness 缺陷：Oracle 检查了契约未声明的实现细节。之前的人工参考控制没有发现它，因为 manual 参考使用了同一个公式。下一轮应先在 `contract.json`/任务文本中写明指纹公式，或把 Oracle 改为只检查 Schema 约束加行为。提交新 harness 后注册新队列，不对本计划重判。

## 冻结条件

- 计划：`docs/evidence/phase3-kiro-pilot-2026-11-15/plan.json`。业务源 `d8a5c57`，harness `4b67667`；engine `kiro`，模型 `claude-opus-5.5`，effort `medium`，预算 600 s，重复 1 次。计划内顺序为 manual 在前，由固定种子打乱得出。
- 引擎：kiro-cli 3.3.0，`chat --no-interactive --output-format stream-json --agent-engine v3`。实际模型由事件中的 `config_option_update` 确认，为 claude-opus-5.5。预检真实发起 READY 回合，两组都是 ready=true。
- 上下文隔离：每次调用使用全新 `KIRO_HOME`，其中只有最小 agent（仅 fs_read/fs_write/execute_bash，无 MCP、无 steering/skills、memoryReflection off），不复制认证。如果不这样做，Kiro 会加载用户的全局 steering 和 MCP 服务器（包括可以派生其他 agent 的工具）。
- 交接：两组都通过 `prepare` 生成单 trial 包，用 `python3 -I worker.py` 生成，再用 `restore` 恢复到全新目录后做私有验收。两份候选都只修改了 `internal/candidate/approval.go`（manual +118 行，action +32 行）。
- 工具调用审计：两组各 4–5 次调用，路径都位于本包 `candidate/` 内，没有观察到读取包外文件、evaluator 或网络检索。
- 本地环境：macOS arm64，Go 1.26.1，Python 3.13.5。PostgreSQL 为 **17.11**（`postgres:17-alpine`），不是 CI 的 17.6：拉取 `postgres:17.6` 镜像时无输出地阻塞，因此改用本机已有镜像。

## 限制

- 只有一块、每组一次；即使没有 harness 缺陷，也无法支持统计结论。
- 生成端与验收端在同一宿主、同一账号下运行。Kiro 的 `--trust-all-tools` 没有类似 Codex `workspace-write` 的文件系统沙箱。上面的“未读取包外文件”是对事件日志的事后审计，不是隔离保证，不能称为盲测。
- 计量 credit 依赖 Kiro 服务端报告，不等同于账单费用。服务端不可变模型快照未知。

## 证据

`docs/evidence/phase3-kiro-pilot-2026-11-15/`：冻结计划、汇总；每个 trial 的 manifest/job、预检与生成的原始 ACP 事件、invocation、receipt、patch、bundle、冻结验收逐项结果（`evaluation.json`）和诊断结果（`diagnostic-hash-relaxed.json`）。私有 seed、`KIRO_HOME` 会话目录和认证不入库。

复现（需要原私有 seed；没有 seed 时只能做新 fixture 的重放）：

```bash
python3 benchmark/phase3/ctl.py plan --engine kiro --model claude-opus-5.5 --reasoning-effort medium \
  --time-budget 600 --base d8a5c576cc3c293ee2da884f5c618958df407b51 --repetitions 1 \
  --fixtures "$PRIVATE/fixtures.json" --output reports/phase3-kiro-plan.json
python3 benchmark/phase3/ctl.py prepare --plan reports/phase3-kiro-plan.json --trial go-manual--001 --output /abs/kit
python3 -I /abs/kit/worker.py --output /abs/results/go-manual-001   # 需要已登录的 kiro-cli
# restore / evaluate / summarize 同 phase3-action-evolution.md
```
