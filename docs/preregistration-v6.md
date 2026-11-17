# 确认性实验预注册（v6）

本文在**任何确认性数据产生之前**提交，提交时间以 Git 提交记录为准。后续如需修改，只能在文末“附录”追加并注明时间。正文一旦提交即不再改动。事后发现的分析一律标为探索性。

## 背景

v3–v5b 的探索性结果显示，平台内核带来的正确性收益只覆盖它实际封装的约定（[实验总结](experiment-summary-2026-11-15.md)）。同时，扩展点不明显、或者字段可以省略，都会引入新的失败。这些结论来自事后选择的分析，每种设计也只用了一个任务。本实验用预先确定的假设和检验方法，在三个任务上复现这些结论。

## 设计

- **任务（3 个）**
  - T1：v5b reopen 复现，使用 phase5b harness 和全新私有 seed；
  - T2：v6a 二级审批（`benchmark/phase6a/`）；
  - T3：v6b 非状态字段批量操作（`benchmark/phase6b/`）。

  三个任务都遵循 v5b 的原则：线索分散且互不冲突；扩展点放在候选包里；声明字段必填，并且必须显式表态。
- **模型（4 个）**：claude-opus-5.5、claude-haiku-4.5、gpt-5.6-luna、qwen3-coder-next。engine 为 kiro，effort 为 medium（haiku 和 qwen 实际不支持 effort，照实记录），预算 600 秒。
- **样本**：每个任务 × 每个模型各 10 块，每块 2 组（manual、action），组内顺序按冻结种子随机。共 3 × 4 × 10 × 2 = **240 个 trial**。
- **执行**：只做一次首次生成，不重试、不修复、不替换。按计划顺序执行；多个模型可以并行。
- **隔离**：生成端使用文件隔离加网络白名单（`benchmark/phase3/isolate_net.py`，见 [network-isolation.md](network-isolation.md)），每个 trial 保存网络日志。如果网络隔离控制没有通过，就退回到仅文件隔离，并在附录中记为偏离，结论中不得称为盲测。
- **冻结**：注册计划前，三个 harness 必须已合并到同一个 commit，并在附录中写明该 SHA。各任务计划冻结 harness hash 的方式与以往一致。

## 结局变量（来自冻结 Oracle 的逐 trial 结果）

- **K**（主要）：该 trial 是否遵守了所有 `action_enforcement=kernel` 的隐含约定。只对已评分的 trial 计算（passed 或 acceptance-failed）。
- **D**：该 trial 是否遵守了所有 `action_enforcement=declared` 的隐含约定。只对已评分的 trial 计算。
- **E**：显式契约是否全部通过。scope-violation 和 candidate-error 记为 E=0。
- **A**：全部用例是否通过（首次成绩）。
- **S**：生成耗时，取 worker 报告的 `generation_seconds`。
- **U**（安全）：授权类、原子性类用例中，是否出现 `state_changed=true` 的越权或部分写入。

未测量的 trial（runtime、provider、protocol、infrastructure 类失败）不计入配对，但会逐层报告。任一层中未测量比例超过 10% 时，在报告中单独标出。

## 主假设（单侧检验，α=0.05，H1–H3 用 Holm 法校正）

- **H1**：action 组的 K 高于 manual 组。检验方法：把 12 个层（任务 × 模型）的所有块合并，只取两组都已评分的块，做精确配对符号检验。不一致块的计数方式为：action K=1 且 manual K=0，对比 action K=0 且 manual K=1。
- **H2**：action 组的 E 不低于 manual 组，预期 action 更高。检验方法：同样对合并后的块做精确配对符号检验，计 E 的不一致块。
- **H3**：action 组的 S 低于 manual 组。检验方法：取两组都有 S 的块，计算块内差值 S_action − S_manual，对合并后的差值做单侧 Wilcoxon 符号秩检验（精确法或正态近似，样本量超过 50 时用正态近似）。

## 次要与描述性分析（不做校正，仅供参考）

- **D 率**：action 组按任务、按模型分别报告 D 率，并与 manual 组同一约定的遵守率对比。预期：在必填加显式表态的设计下，action 组合并 D 率 ≥ 0.6。这个阈值只作为描述，不做检验。
- **A 率**：报告每层的 A 率，以及每层的配对符号检验 p 值。
- **U**：预期为 0，逐个列出所有 U=1 的 trial。
- **越界率**：报告 scope-violation 的比例，并与 v4、v4b 对比。
- **T1 复现**：将 T1 与 v5b 的逐列结果对比，说明哪些方向一致、哪些不一致。
- **Verifier 拦截**：用 appctl extensions 规则对全部候选做拦截率分析（TP/FP/FN）。这部分是附加分析，不改变任何成绩。

## 判定规则（事先约定）

- 如果 H1 成立，可以表述为：“在本设计下，内核封装的约定在多任务、多模型条件下复现了正确性收益。”
- 如果 H1 不成立，如实报告“未复现”，并且不再通过增加分析子集来寻找显著结果。
- H2、H3 分别独立表述。任何一项假设不成立，都不影响其他假设的报告。
- 三个任务都是我们自己设计的，约定也由我们挑选。无论结果如何，都**不能**据此推出通用平台的收益。

## 附录（只追加）

（尚无）

### 附录 A（2026-11-15 13:45，确认性数据产生之前）：网络隔离的剩余缺口与审计规则

[网络隔离](network-isolation.md)（PR #21）通过了 21/21 项控制，其中包括 Kiro 的真实探针。但实测确认了一个缺口：Kiro 的服务端 `web_search` 走的是已放行的 runtime 主机，所以仍能检索到 GitHub。正式 trial 使用的 agent 没有 `web_search` 工具，模型如果要绕过，只能在 shell 中再启动一个 kiro-cli，或者调用同类客户端。为此事先约定以下规则：

- **审计**：检查每个 trial 的 ACP 事件里的 shell 命令，凡是出现 `kiro-cli`、`q chat`、`web_search`、`curl`/`wget`/`git clone`，或访问外网 URL 的，标记为 `retrieval-attempt`。另外检查 `network.log`，凡是存在被拒绝的 CONNECT 请求的 trial，标记为 `network-denied`。
- **主分析**不排除任何被标记的 trial，避免事后剔除样本。**敏感性分析**则排除所有 `retrieval-attempt` trial（连同所在块的配对 trial）后重跑 H1–H3，两种结果都报告。
- 结论里写“生成端网络受限（仅模型端点），服务端检索未经证实已排除”，不使用“盲测”这一说法。

### 附录 B（2026-11-15 14:03，确认性数据产生之前）：冻结的 harness

三个任务的 harness 与网络隔离已合并到 `poc/v6-confirmatory` 的 `bae80aa`。对应的 `benchmark/` 目录树 hash 为 `11d276234c7e41cfef3d8e9905530d3af717da00`；之后追加文档不改变这个 hash。各计划仍按文件冻结 hash，并记录注册时的 HEAD。任务入口如下：

- T1：`benchmark/phase5b/ctl.py`
- T2：`benchmark/phase6a/ctl.py`
- T3：`benchmark/phase6b/ctl.py`

生成使用 `benchmark/phase3/run_kiro_cohort_net.sh`。12 个通道（3 任务 × 4 模型）各有独立的数据库与端口，最多 6 个通道并行。每个通道内严格按计划顺序执行；每块的两组在同一通道中先后运行，承受相近的负载。

### 附录 C（2026-11-15 14:12；生成已开始，尚未查看任何验收结果，也未做任何分析）：两处补充说明

1. **T1 的约定分类。** phase5b 的 cohort.json 没有 `action_enforcement` 字段。按照 v5、v5b 报告中已经写明的分类：audit、request-hash、uniform-denial 由内核实现（kernel），outbox 由作者声明（declared）。这只是把已有分类写明，没有做新的选择。
2. **`network-denied` 标记。** 每个 trial 的网络日志里，都会有 Kiro 遥测主机被拒绝的记录：`telemetry.eu-central-1.kiro.dev`、`client-telemetry.us-east-1.amazonaws.com`。这两个主机在[网络隔离](network-isolation.md)文档中已被认定为遥测，因此不计入 `network-denied`。`network-denied` 只统计对其他主机的拒绝。这个标记仅用于描述，不影响主分析，也不影响敏感性分析。

### 附录 D（2026-11-15 14:24；尚未查看任何 v6 验收结果）：冻结 Verifier 规则，作为样本外评估

`appctl extensions` 的规则（PR #24）在调试过程中参考过已归档的 280 个候选：指纹规则第一版在 v4 manual 组有 57 个误报，修改后降为 0。因此那组拦截率只能算样本内的拟合程度。现在把规则冻结在 `4e286c9f256c29b98d7684156ad5c28c99dd3d03`，`internal/extcheck` 树 hash 为 `d6cfaaa4de66dec76e025f49fa6aeeabcceb0910`，然后**原样**运行于 v6 的全部 240 个候选，作为样本外评估。报告内容为各条规则的 TP/FP/FN，以及"显式全部通过但隐含约定被违反"的候选中有多少被补拦。规则一律不再调整；如果之后要改，必须另起一次评估，并标注为探索性。

### 附录 E（2026-11-16 09:58；尚未查看任何 v6 验收结果）：用量上限中断与续跑规则

2026-11-15 约 14:34（UTC+8）起，Kiro 账号触发月度用量上限，服务端返回 `UsageLimitReachedError`。当时 240 个 trial 的状态如下（只统计生成状态，未读取任何 `evaluation`）：

- 108 个已生成并完成验收；
- 1 个越界；
- 5 个在生成中途被上限打断，记为 provider-error；
- 126 个在 READY 预检时就被拒绝，从未开始生成。

2026-11-16 09:55 再次预检，四个模型均恢复为 READY。

处理规则：

1. 126 个预检被拒的 trial **从未开始生成**，本次运行即为它们的首次生成。每个通道内按原计划的剩余顺序执行，计划、种子和 kit 都不变。被拒时生成的结果目录归档在 `<lane>/preflight-blocked-20261115/`，不删除。
2. 5 个已开始生成、中途被打断的 trial 按正文规则记为**未测量**，**不重跑**。它们所在的块不参与配对。
3. 续跑与首轮相隔约 19 小时。服务端模型版本可能已经变化，这一点无法核实。报告中会给出按批次（首轮 / 续跑）拆分的描述性结果，主分析不按批次分层。
4. 若续跑中再次遇到预检被拒，沿用规则 1，等配额恢复后再执行。
5. 冻结之后，`benchmark/` 下新增了操作脚本 `run_v6_lanes.sh`，目录树 hash 因此由 `11d2762` 变为 `0861531`。所有冻结计划引用的 harness 文件 hash 没有变化，12 个计划都能在当前 checkout 上通过 `load_plan` 校验。

### 附录 F（2026-11-16 10:10；尚未查看任何 v6 验收结果）：服务区域变更

按附录 E 续跑时，126 个 trial 在预检阶段**全部**失败（HTTP 403），没有一个开始生成。原因是配额恢复后，Kiro 账号的服务端点由 eu-central-1 改到了 us-east-1（management/runtime/telemetry 均变为 `*.us-east-1.kiro.dev`），而网络白名单只放行 eu-central-1。这一轮结果归档到 `<lane>/preflight-blocked-region-20261116/`，按附录 E 第 1、4 条处理：这些 trial 仍视为从未开始生成。

处理过程：

1. `isolate_net.py` 新增环境变量 `KIRO_ALLOW_HOSTS`，未设置时默认值不变。该脚本不在任何冻结计划的 hash 范围内，属于操作端隔离。
2. 用 discover 模式确认 us-east-1 下的主机：management、runtime、telemetry、client-telemetry、`app.kiro.dev`。
3. 以 `management.us-east-1.kiro.dev,runtime.us-east-1.kiro.dev` 为白名单，重跑完整的网络隔离控制（含 Kiro 真实探针），21/21 通过，见 `docs/evidence/network-isolation/control-us-east-1-2026-11-16.json`。`app.kiro.dev` 不在白名单内，READY 和工具调用都能正常工作。
4. 续跑批次统一使用 us-east-1 白名单。首轮（eu-central-1）与续跑（us-east-1）的服务区域不同，报告中会按批次另作描述性拆分（与附录 E 第 3 条一致）。遥测主机被拒的认定扩展到 `telemetry.us-east-1.kiro.dev` 和 `app.kiro.dev`，均不计入 `network-denied`。
