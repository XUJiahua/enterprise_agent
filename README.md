# Enterprise Agent

面向 AI Coding 的企业应用内核与验证工具原型。以采购审批为例，研究如何通过确定性的权限、事务和业务规则，提高 Agent 开发的可靠性。

技术栈：**Go · PostgreSQL · Next.js**。当前是可运行的研究 POC。

## 核心能力

- **权限护栏**：对照手写授权、PostgreSQL RLS 和 ZenStack Policy 三种方案。
- **业务内核**：类型化 Action 和受保护的操作目录，集中执行批量原子性、审计、幂等与领域约束。
- **确定性验证**：`appctl` 检查策略、迁移、扩展代码和操作声明。
- **独立评测**：支持 Codex / Kiro 候选生成、冻结计划、归档、独立验收与完整分母报告。
- **共享界面**：采购单列表、创建、审批及 demo 身份切换。

## 快速开始

需要 Go 1.25+、Node.js 24、Python 3.12+。先按 [本地运行指南](docs/getting-started.md) 启动可销毁的 PostgreSQL 17 测试数据库。

在仓库根目录启动默认的 RLS 示例：

```bash
export ADMIN_DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/enterprise_agent_poc
export DATABASE_URL=postgres://app_runtime:poc-only@127.0.0.1:5432/enterprise_agent_poc
export POC_DB_RESET=1
make deps build
node scripts/db.mjs reset guarded
bin/server --mode guarded
```

另一终端启动界面：

```bash
npm ci --prefix apps/web
npm run build --prefix apps/web
POC_ROUTE=guarded npm start --prefix apps/web
```

打开 [本地界面](http://127.0.0.1:3000)。其他运行模式、API 示例和验证命令见 [本地运行指南](docs/getting-started.md)。

## 文档

| 目的 | 入口 |
|---|---|
| 运行示例、切换方案、执行验证 | [本地运行](docs/getting-started.md) |
| v0.1 Go 操作内核与采购参考应用 | [发布说明](docs/release-v0.1.md) · [内核说明](docs/kernel.md) · [issue #36](https://github.com/XUJiahua/enterprise_agent/issues/36) |
| 理解业务语义与内核 | [操作目录与 Verifier](docs/operation-semantics.md) · [事务化 Action](docs/transactional-action.md) |
| 查看研究结果与适用边界 | [实验总结](docs/experiment-summary-2026-11-15.md) |
| 继续开发、复现实验或接手任务 | [当前交接](docs/HANDOFF.md) |
| 查看自动验证结果 | [GitHub Actions](https://github.com/XUJiahua/enterprise_agent/actions) |

## 当前范围

示例采用固定 demo 身份；原有 UI 尚未接入全部新 Action。权限与内核基于可信应用代码假设，尚未提供生产认证或不可信扩展沙箱。研究结果限于已测试的任务与约束，具体证据和后续工作见上述文档。
