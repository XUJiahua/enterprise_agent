# 技术 POC 实验设计

## 当前检验的假设

在已配置正确的平台规则下，业务代码漏写常见条件时，确定性约束能否仍保护数据？

先验证健康业务可执行，再对每条路径施加相同含义的执行故障。每个注入都从全新的测试表和相同 fixtures 开始；相互干扰的自审批与其他审批使用不同记录。SQL 与 ORM 的接口不完全相同，因此是同语义故障，不能宣称逐字相同源码变更。

基线并非故意写坏：健康的普通 Go 必须通过全部验收。之后通过显式故障开关模拟 Handler 漏条件、过度读取和新增后门写入口。测试捕获的基线失败是预期对照，CI 成功不表示基线的注入版本安全。

## 证据层次

| 层次 | 当前证据 | 不能推出 |
|---|---|---|
| 编译 | Go build/test/vet；ZenStack generate + tsc | 完整运行正确性 |
| HTTP 验收 | 16 个独立业务用例 | 所有业务和输入正确 |
| 故障注入 | 5 类固定的人工错误 | 真实模型犯错概率、首次通过率 |
| DB 探针 | 非所有者角色直接 SQL、无上下文默认拒绝、列权限、事务上下文回收 | 任意代码/数据库凭据持有者无法伪造身份 |
| 静态验证 | 7 组对抗探针；真实 Git 基线和文件变更 | 任意 Go/SQL 的静态安全证明 |
| public CI | PostgreSQL 17.6 和固定依赖可复现 | 生产认证、性能或完整隔离 |

Verifier 对抗探针覆盖：安全控制、扩大角色后重新生成 SQL、改写迁移并同步改写 manifest、新增破坏性迁移且 checksum 正确、禁用 RLS 生成物、允许自审批、无效 base ref。

`plan` 当前识别有限策略语义中的新增角色权限。固定 tenant/protectedFields/approval 语义被弱化会直接拒绝解析。并未实现任意逻辑表达式的包含关系证明。

## 关键公平性与限制

- 使用同一个 PostgreSQL 表结构、同一独立业务 Oracle、同样身份及 fixtures。
- 三条路径先由同一作者正确实现，再注入固定缺陷；这不是盲测，也没有统计显著性。
- guarded 和 baseline 共用 Handler，guarded 由数据库执行策略；减少不相关实现差异。
- ZenStack 真实启用 V3 PolicyPlugin，没有用手写判断假冒 ORM 策略。
- guarded 字段过读会损失该次请求的可用性，ZenStack 字段策略保留其余结果；两者不能作为相同体验计分。
- Go 编译器只支持 Purchase，租户字段/审批状态/保护字段为固定语义；不是通用 DSL。
- 没有关系嵌套、批量跨资源写入、后台任务身份、审计 Outbox 或多步骤工作流测试。
- 没有生产认证与不可信代码隔离；保留原始 DB Client 是两条策略路线都需要治理的边界。
- CI 无 API 密钥，不调用付费模型，结果不受模型服务可用性影响。

## 下一轮：真实 AI Coding 对照

机制 POC 通过后，冻结基准 commit 和独立 Oracle，再做如下实验：

1. 每种任务在三种栈上使用同一模型版本、提示词、token/时间预算与初始 fixtures。
2. 从干净分支独立重复至少 10 次，随机分配任务顺序；保留模型运行日志、commit、成本与修复轮数。
3. 测试集在模型工作目录之外维护；开发者可见基础测试，验收保留额外隐藏样本，禁止 Agent 改写验收。
4. 分别比较首次验收通过率、最终缺陷逃逸、达到通过的修复轮数、成本与时间。
5. 加入只改善流程、不增加 Runtime 护栏的 Go 对照，分辨收益来自流程还是约束。
6. 加入策略本身写错、使用未受保护客户端和新数据表遗漏规则，避免只评价业务代码错误。
7. 加入共享 Next.js UI 和真实浏览器 E2E，随后再评估关系权限、危险升级与后台任务。

在这些重复试验完成前，允许得出的结论仅限于：哪些预先定义的故障会被哪些机制阻断，以及代价是什么。

## 上游依据

- [ZenStack V3 Quick Start](https://zenstack.dev/docs/quick-start)
- [ZenStack 3.10.2 ORM 源码](https://github.com/zenstackhq/zenstack/tree/v3.10.2/packages/orm)
- [ZenStack 官方策略参考](https://github.com/zenstackhq/skills/tree/main/skills/zenstack-access-control)
- [PostgreSQL Row Security](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)

本项目没有复制 NocoBase 或 Windmill 代码。对这两者的研究用于设计启发，本轮只实现 ZenStack 与最小 Go 方案的运行对照。
