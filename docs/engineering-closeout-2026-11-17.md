# 工程整合记录（2026-11-17）

目标：整合 v3–v9 的公开 PR 历史，消除交接矛盾，补齐遗漏的回归与安全的阻塞归档入口。此记录不引入模型样本，不重判历史实验。

## 原始 PR 与验证锚点

收尾开始时有 16 个开放 PR，合计 51 个不同的新增历史提交。下列检查均为各原始 head 的成功记录；整合后的 CI 单独检查，不能由这些记录代替。

| PR | 原始 head | 原始 base | 原始 CI |
|---|---|---|---|
| [#15](https://github.com/XUJiahua/enterprise_agent/pull/15) | `197e48194b981201fc33f5eed4bba0579ac4e9f4` | main | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43640918203) |
| [#16](https://github.com/XUJiahua/enterprise_agent/pull/16) | `66c4332f3d361aaabeb1b8158de1dc1e3ffb53c0` | poc/continue-verification-from-handoff | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43661394545) |
| [#17](https://github.com/XUJiahua/enterprise_agent/pull/17) | `afa27e9c3fc2daa78daf42a8938d946f249da5bf` | poc/v3-kiro-cohort-002 | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43666841772) |
| [#18](https://github.com/XUJiahua/enterprise_agent/pull/18) | `cea61b779941ee30d39b5e86f01cca054eb9177b` | poc/v4-implicit-convention-task | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43671714247) |
| [#19](https://github.com/XUJiahua/enterprise_agent/pull/19) | `d2f9a25fef970cf39fc125d53d4d981cb29deba4` | poc/v4-implicit-convention-task | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43669710839) |
| [#20](https://github.com/XUJiahua/enterprise_agent/pull/20) | `e9cbc945927ff1b3d9a6f5ee887ef9894a6c2691` | poc/v5-scattered-convention-task | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43674936824) |
| [#21](https://github.com/XUJiahua/enterprise_agent/pull/21) | `08413b7491e5606d5c0d31f1a4b673899930d950` | poc/v5b-required-event | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43682151649) |
| [#22](https://github.com/XUJiahua/enterprise_agent/pull/22) | `0413a2f6aab6bbfce6181047c103c492f7dcac9f` | poc/v5b-required-event | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43683509065) |
| [#23](https://github.com/XUJiahua/enterprise_agent/pull/23) | `7cc80487e85f8ce861e6f25b2c71c5e178c419ba` | poc/v5b-required-event | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43683138755) |
| [#24](https://github.com/XUJiahua/enterprise_agent/pull/24) | `4e286c9f256c29b98d7684156ad5c28c99dd3d03` | poc/v5b-required-event | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43684517345) |
| [#25](https://github.com/XUJiahua/enterprise_agent/pull/25) | `71a213de04c4644b837184b1888ff22b77c5090d` | poc/v5b-required-event | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43814530961) |
| [#27](https://github.com/XUJiahua/enterprise_agent/pull/27) | `fad8adfa89edb753c55410518d531e21489f7d9b` | poc/v6-confirmatory | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43830361902) |
| [#28](https://github.com/XUJiahua/enterprise_agent/pull/28) | `f7997d74332ee7c138c8aff62efe51bd510326f5` | poc/operation-semantics | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43869102441) |
| [#29](https://github.com/XUJiahua/enterprise_agent/pull/29) | `3038d48d4b662500a1067c03127f4346554a6593` | poc/v7-results | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/43979921315) |
| [#30](https://github.com/XUJiahua/enterprise_agent/pull/30) | `f0dc1817e0afe6fa36134f43d926c526feb539f5` | poc/v8-results | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/44034174702) |
| [#31](https://github.com/XUJiahua/enterprise_agent/pull/31) | `b8131155b0ed8fea40a8caa752bd164c49f6c2b3` | poc/v8-results | [success](https://github.com/XUJiahua/enterprise_agent/actions/runs/44035719744) |

`poc/v9-results` 含 48 个相对 main 的提交，`poc/v4b-extension-point` 另有 2 个，`poc/v9-event-rules` 另有 1 个。整合提交保留三个 head 为父提交/祖先，不 squash 原始历史。三个分支的 HANDOFF 文本冲突已处理；当前入口被重写，收尾前的文本另存历史文件。

## 收尾增量

- `archive_preflight.py`：基于 lane plan 和明确未开始的 preflight 记录，使用原子创建的序号槽位归档；失败停止、不覆盖、不自动生成。新增控制覆盖三次同日阻塞、旧同名目录、移动失败、已开始或已完成的证据、符号链接和 `&&` 停止语义。
- CI 新增 phase6a/phase6b 真实 PostgreSQL + 假 Kiro 控制，最终 report 强制核对 193/190 用例、15/16 人工候选、2/3 正确参考、负控制及传输证据。
- 重写 HANDOFF，更新 README 入口，明确公开内核/实验模板/私有叠加层和消费者登记的实际边界。
- 旧 worker、Oracle、计划、历史 evidence 与首次评分不变。新工具不改变历史 30 秒 READY 超时或续跑规则。

## 验证与未完成项

本地 Python 单元测试 91 项通过（包括合入 v4b 后的测试和新增归档控制）；Go 1.25.0 的 race test 与 vet 通过；53 个目录控制、44 个扩展控制、280 个归档候选静态重放与 228 个目录复评通过，聚合与冻结记录一致；文档相对链接和四组 Bash 示例语法检查通过。

本地补充数据库使用 pgserver 提供的 PostgreSQL 16.2，仅作辅助检查，不能冒充冻结环境或 CI PostgreSQL 17.6。完整 CI 结果以本 PR 的 GitHub Actions 为准；没有通过 CI 前不声称工程收尾完成。

后续状态：

1. PR #33 已合入 main（`e8248d8`），旧 16 个 PR 已收拢；main 的六项 CI 全部成功。
2. oracle 仓库现已公开，续跑修复已实现并通过 13 项控制，但 GitHub App 写入返回 HTTP 403，尚未直接提交或部署；补丁和步骤见 [续跑修复交接](oracle-v9-resume-handoff.md)。
3. 原私有跨表条件、时间窗、逐项更新等能力已可阅读，但仍未迁移进公开平台；当前字段修改仍仅支持 assigneeId。
4. 实际消费者/载荷契约、持续业务演进总成本和第二业务域验证待开展。旧 Oracle 公开后，后续盲测需新的隐藏任务。

详见 [当前交接](HANDOFF.md)。
