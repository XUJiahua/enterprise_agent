# Oracle v9 续跑修复交接（2026-11-17）

`goodloris/oracle` 已公开。当前 GitHub 连接可以读取，但写入 Git tree 返回 HTTP 403（Resource not accessible by integration）；因此修复以本仓库的 [完整补丁](../maintenance/oracle-v9-resume.patch) 交接，尚未提交到 oracle。

补丁基于 oracle `v9` 的精确提交 `e75c744a280d13854f154f637c462f4dc4faa192`，包含六个文件：续跑脚本、标准库 helper、13 项子进程控制、CI、操作说明和 README 公开状态更新。已在干净基线上检查可应用，并逐字节核对应用后的六个文件与已测试实现一致。

修复包括：同日重复阻塞独立归档；远端/本地归档失败停止；已开始的 draft/adapter 不重跑；terminal 传输中断后可恢复；校验并原子安装结果，不覆盖不同候选；复用 1–3 号数据库槽位；后台失败传播；核对完整计划分母与实际评分。只归档明确未开始的 preflight，不修改旧 worker、READY 超时、预算、Oracle、评分或预注册。

## 在有写权限的环境应用

先确认原驱动和生成进程已经停止。以下过程只修改 checkout 和运行 synthetic 控制，不启动真实生成或验收主机：

```bash
git clone https://github.com/goodloris/oracle.git
cd oracle
git switch -c fix/v9-resume-archives e75c744a280d13854f154f637c462f4dc4faa192
git apply --check /absolute/enterprise_agent/maintenance/oracle-v9-resume.patch
git apply /absolute/enterprise_agent/maintenance/oracle-v9-resume.patch
bash -n scripts/resume_v9.sh
python3 -m unittest discover -s tests -v
git diff --check
```

核对六个文件后提交并创建以 `v9` 为 base 的 PR，不改仓库默认分支，也不直接 push v9/main。该 PR 的 resume-controls workflow 不调用模型或实际 SSH 主机。`docs/v9-resume-repair.md` 给出部署和完整局限；真实验收主机保持原计划的 harness commit，不为部署操作员脚本而修改冻结 checkout。

## 本仓库中的自动验证

```bash
python3 scripts/oracle_resume_controls.py
```

脚本从公开仓库取固定基线，在临时 checkout 应用补丁，运行真实 Bash/Python/tar 与模拟 SSH/数据库/生成器的 13 项控制；失败不产生成功报告。CI 的 oracle-resume-controls job 和最终 report 强制检查结果。

本次没有连接 `10.30.11.112`，没有产生模型样本。源码修复不等于已部署或完成真实跨主机恢复。历史 v7–v9 在采集时的隔离条件不因后续公开而改变；未来确认性盲测必须使用新的未公开任务和 Oracle。
