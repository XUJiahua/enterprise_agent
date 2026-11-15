# v3 Kiro 队列 003：三个较弱模型的并行重复

运行时间 2026-11-15 08:33–09:01（Asia/Shanghai）。在 [队列 002](phase3-kiro-cohort-002-results.md) 中，claude-opus-5.5 两组都是 10/10，分不出差异。本轮用同一 harness（`97fe41c`，冻结文件与队列 002 完全相同）和同一契约，换成三个更便宜、可能更弱的模型，各注册一个独立队列，三个队列并行运行。

## 结果

| 模型（计费倍率） | 组 | 计划 | 已观察 | 首次通过 | 结局 | 生成耗时中位 | credits 中位 / 合计 |
|---|---|---|---|---|---|---|---|
| claude-haiku-4.5（0.4） | manual | 10 | 10 | **5** | 失败 4，越界 1 | 59.3 s | 0.69 / 7.52 |
| | action | 10 | 10 | **8** | 失败 1，越界 1 | 55.6 s | 0.65 / 6.72 |
| gpt-5.6-luna（0.6） | manual | 10 | 10 | **3** | 失败 7 | 48.7 s | 0.68 / 7.08 |
| | action | 10 | 10 | **10** | — | 38.0 s | 0.55 / 5.26 |
| qwen3-coder-next（0.05） | manual | 10 | 9 | **1** | 失败 7，越界 1，未测量 1 | 84.7 s | 0.75 / 7.32 |
| | action | 10 | 9 | **6** | 越界 3，未测量 1 | 52.3 s | 0.49 / 4.91 |

参照：claude-opus-5.5（队列 002）两组都是 10/10。

**按块配对**（每块里两组用同一个 fixture seed）：

| 模型 | 仅 action 通过 | 仅 manual 通过 | 精确符号检验 p |
|---|---|---|---|
| haiku | 4 块 | 1 块 | 0.375 |
| luna | 7 块 | 0 块 | **0.016** |
| qwen | 5 块 | 1 块 | 0.219 |
| 三个模型合并 | 16 块 | 2 块 | 约 0.0013 |

qwen 的两块含未测量 trial，已排除在配对之外。这些检验**事后选定，没有预注册**，只作探索性参考。详见 `paired-stats.json`。

## 失败的是什么

- **manual 失败集中在幂等与并发：**
  - 重排目标后重放被当成新请求（返回 409），或得到 503；
  - 同 key 不同集合没有返回 409；
  - 并发同 key 请求中，一方因唯一键冲突或序列化失败得到 503，没有被转为重放或 409；
  - 还有 3 份（luna 2 份、qwen 1 份）各只得 40/66：整个新 Action 都返回 503，功能组 0/6。
  - 少数失败落在输入组的 `NUL absent target`。
- **action 组唯一的验收失败**（haiku go-action--008，55/66）：没有把内核的 `action.Rejection` 转换成 `protocol.Rejection`，导致所有业务拒绝都变成 503。提示词里专门提醒过这一点。这是"集成现成能力时也会出的错"。
- **安全侧：** 所有失败候选在授权和原子性用例里都没有改动数据（`state_changed=false`），属于"拒绝过度、该成功的失败"，没有观察到越权写入或跨租户写入。这与 RLS 和整批事务的兜底一致。
- **作用域违规（计入已观察的失败）：**
  - 3 份只是在包里留下了 `go build` 产物 `server`（haiku manual-002、haiku action-004、qwen action-006）。这些产物不在 `.gitignore` 中，所以按冻结规则判为越界。这条规则偏严，未来的 harness 应忽略构建产物，或在提示词里说明。
  - 3 份真正修改了受保护文件：qwen action-005 和 action-008 改了 Action 内核，qwen manual-004 改了 HTTP 适配器。
- **未测量：**
  - qwen go-action--003：Kiro 服务端返回 `BedrockValidationError`，属于 provider 错误。
  - qwen go-manual--001：冻结 Oracle 判为 infrastructure-blocked（连接被关闭）。但候选日志显示，候选服务出现了 nil pointer panic。这实际上是候选缺陷，被 Oracle 错分类了。按冻结规则它仍算未测量；如果按候选失败计，qwen manual 为 1/10。

## 解读

- **对应 HANDOFF 的核心问题：** 这是本项目第一次在真实模型候选上观察到平台能力带来的**首次正确性差异**。能力越弱的模型，手写事务、审计和幂等越容易出错；调用受保护的 Action 内核能明显降低这类错误。最强的模型（opus）不需要这种帮助。
- **差异主要来自幂等与并发语义**，也就是内核已经封装好的那部分难点，不是授权或租户隔离。在授权和租户隔离这一层，RLS 让失败的候选也保持在安全一侧。
- **action 组的效率优势仍在**（luna 和 qwen 的耗时与 credit 更低），但在这些模型上差距没有 opus 那么大。
- **不能外推：** 只有一个任务，内核又是为这个任务专门写的，所以这是"能力辅助"的综合效果，不能推出通用平台的收益。三个模型的结论方向一致，但每个模型只有 10 块。

## 执行与隔离

- **执行方式：** 三条通道各有独立的冻结计划、私有 seed、PostgreSQL 容器（17.11）和端口，全部由 `benchmark/phase3/run_kiro_cohort.sh` 串行执行各自的计划顺序。
- **Seatbelt 文件隔离：** 与队列 002 相同的 deny 列表，额外加上另外两个模型的目录和旧队列目录。沙箱拒绝 0 次，没有出现网络类 shell 命令。luna manual-007 尝试读取过一个不存在的错误路径。
- **模型参数：** haiku 和 qwen 不支持 effort，计划请求的 `medium` 实际没有生效；luna 生效。
- **并行的影响：** 三个模型同时运行会互相抢 CPU，耗时不能直接和队列 002 比较。块内的两组承受相同的负载。
- **脚本 bug：** 运行期间 `status.log` 里的退出码恒为 0，原因是 `$(date)` 覆盖了 `$?`，现已修复。权威结果以各 trial 的 `evaluation.json` 和 `outcome.json` 为准。

## 证据

`docs/evidence/phase3-kiro-cohort-003-models/<model>/` 包含：

- 冻结计划、汇总、执行顺序、状态日志、逐 trial 分析；
- 每个 trial 的 manifest、job、原始 ACP 事件、receipt、patch 和 bundle（越界 trial 改为保存 `scope-status.txt` 与 `scope-violation.diff`）、逐项验收结果和候选服务日志。

私有 seed、Kiro 会话目录和认证均未入库。
