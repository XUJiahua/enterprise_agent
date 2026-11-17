# v4b：扩展点提示能减少多少越界

运行时间 2026-11-15 10:23–10:48（Asia/Shanghai）。harness `ad85b9d`，计划变体 `v4b`。

## 改了什么

在 [v4 队列 001](phase4-kiro-cohort-001-results.md) 里，haiku 和 qwen 的 action 组 20/20 都把 `var Reject = Transition{...}` 写进了受保护的内核，结果全部判为越界。v4b 只改了一处：在 action 组提示词的能力说明末尾加一句

> Declare any new Transition value in internal/candidate (e.g. var reject = action.Transition{...}); action.Approve lives in the protected kernel only as the existing example.

manual 组的提示词、两组的导出文件、Oracle 和 108 项计分都和 v4 完全相同。单测 `test_v4b_changes_only_action_capability_line` 确认两个变体之间只有这一行不同。计划里写入 `variant: v4b`，并使用新的私有 seed。每个模型 10 块，两个模型并行运行。

## 结果

| 模型 | 组 | v4（无提示） | **v4b（有提示）** | 越界 v4 → v4b |
|---|---|---|---|---|
| claude-haiku-4.5 | manual | 10/10 | 10/10 | 0 → 0 |
| | action | 0/10 | **7/10** | 10 → 3 |
| qwen3-coder-next | manual | 9/10 | 10/10 | 0 → 0 |
| | action | 0/10 | **6/10** | 10 → 4 |

- v4 与 v4b 的 action 组通过率用 Fisher 精确检验比较：haiku p≈0.003，qwen p≈0.011。这是跨队列比较，seed 不同，属于事后、探索性的检验。
- v4b 中剩下的 7 个越界，改动和 v4 一样，都是在内核里加 `Reject` 声明。
- 没有越界的 13 个 action 候选全部 108/108，四条隐含约定全部遵守。
- manual 组 20/20 全部通过。
- action 组仍然更快、更省：haiku 中位耗时 43.5 秒对 54.5 秒，credit 0.50 对 0.70；qwen 中位耗时 31.8 秒对 55.3 秒，credit 0.29 对 0.51。

## 解读

- 一句明确的扩展点说明，就能让较弱模型把越界从 100% 降到 30–40%。这支持 v4 的解释：失败主要来自**扩展点设计**，而不是集成能力本身。
- 即便有提示，仍有约三分之一的人会去改平台文件。这说明光靠提示不够，平台还需要：在可编辑的包里放声明示例（v5 已经这样做），或者提供注册 API，并保留作用域/CI 拦截作为兜底。
- 这里的 manual 组可以照抄完整的既有实现，所以和 v4 一样几乎都通过。这条结论限定在"有同形范例"的起点上。

## 证据

证据在 `docs/evidence/phase4b-kiro-cohort-001-models/<model>/`，格式与 v4 相同；越界 trial 另附 `scope-status.txt` 与 `scope-violation.diff`。汇总分析在 `analysis.json`。
