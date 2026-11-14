# 模型 Pilot 实际运行状态 — 2026-11-14

已在提交 `80dbb504fae1ddc78a7c92b8ca976c68f106b1f7` 的干净可信 checkout 上冻结并启动真实 Codex pilot。候选基准为第二阶段提交 `c39c26f765a4a6f62cafe196567d797a11631a41`，计划三个任务 × 三条路径，各一次，共九个 trial。

| 项目 | 实际值 |
|---|---|
| 请求模型 | gpt-6.1-sol，medium；未获得服务端模型快照 |
| CLI | codex-cli 0.167.0-alpha.4 |
| 每 trial 生成预算 | 120 秒 |
| 预检 sandbox | read-only；代码生成尚未启动 |
| 预检结果 | runtime-blocked |
| turn.started / turn.completed | 均未发生 |
| 已观察 trial | 0 |
| 未测量 trial | 9 |
| 首次通过率 | null，不能解释成 0/9 |
| token / 费用 | 未上报，不补零、不估算 |

初始化错误原文：

```text
Error: failed to initialize in-process app-server client: Read-only file system (os error 30)
```

该环境的 Codex 状态目录只读。独立诊断中，CLI 的登录状态检查返回成功；将受支持的 sqlite_home / log_dir 指向可写的临时目录后，初始化仍报同一错误。没有改动认证数据、CODEX_HOME 或权限规则，也没有产生或人工补写模型候选代码。

这些结果证明执行器正确停止了不可用批次，不能用于比较模型或技术栈的代码质量。synthetic 驱动测试与本次真实预检保存在不同结果类型中。

证据：

- [冻结计划](evidence/model-pilot-2026-11-14/plan.json)
- [批次状态](evidence/model-pilot-2026-11-14/batch.json)
- [实际预检](evidence/model-pilot-2026-11-14/preflight.json)
- [原始诊断日志](evidence/model-pilot-2026-11-14/stderr.log)

继续生成真实模型样本所需的运行条件：一个能正常完成 `codex exec` 的既有环境。也可在后续配置标准 API 凭据与安全的 CI 生成 job；当前 PR 检查只报告约定 API Key 名称对该次 run 是否配置，不自动消费模型额度。

完整运行步骤见 [模型执行器说明](model-runner.md)。
