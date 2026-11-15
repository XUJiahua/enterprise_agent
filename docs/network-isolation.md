# 网络受限的生成隔离（macOS，无 VM）

## 结论

`benchmark/phase3/isolate_net.py` 在 `isolate.py` 的文件规则之外增加了网络限制：沙箱内只能连本进程启动的 127.0.0.1 代理端口，代理只放行 Kiro 的两个服务主机。github.com、api.github.com、`git ls-remote`、绕过代理的直连 IP、DNS 和本机其他端口都已实测被拒绝；真实 Kiro READY 和模型自己执行的 `curl https://github.com` 也在沙箱内实测通过（后者被拦截）。

**但它仍然不是盲测。** Kiro 的 `web_search` 在服务端执行，请求经由已放行的 `runtime.eu-central-1.kiro.dev` 发出，在沙箱内实测能返回 GitHub 搜索结果（见下文“局限”）。

## 白名单

| 主机 | 用途 | 是否放行 |
|---|---|---|
| `management.eu-central-1.kiro.dev` | 账号、配置、模型列表 | 放行 |
| `runtime.eu-central-1.kiro.dev` | 对话、生成和工具结果回传 | 放行 |
| `telemetry.eu-central-1.kiro.dev` | 遥测 | 拒绝 |
| `client-telemetry.us-east-1.amazonaws.com` | 遥测 | 拒绝 |

只允许 443 端口的 CONNECT 隧道；明文 HTTP 代理请求一律返回 403。

发现方法：kiro-cli 3.3.0 会读取 `HTTPS_PROXY`，它的子进程也一样。先用 `isolate_net.py --discover` 运行一次带工具调用的小任务：沙箱规则与正式运行相同，但代理放行所有 443 主机并逐条记录。结果见 `docs/evidence/network-isolation/discovery-network.log`，共出现四个主机。之后把两个遥测主机移出白名单，READY 和工具调用仍然成功，说明遥测不是必需的。如果更换 Kiro 版本或区域，需要重新做一次发现。

## 机制

- **文件规则**：直接调用 `isolate.profile()`，与 `isolate.py` 逐字节一致（已用 `--print-profile` 对比）。
- **Seatbelt 网络规则**：
  - 先执行 `(deny network-outbound)`，再单独放行 `(remote ip "localhost:<代理端口>")`。
  - 被拒绝的包括所有外部 IP 的 TCP/UDP、unix socket（因此 mDNSResponder DNS 也被拒绝）以及 localhost 的其他端口。
  - `network-bind` 只允许 localhost。
- **代理**：用 Python 标准库写的 CONNECT 代理，在 `isolate_net.py` 进程内作为线程运行，位于沙箱外。
  - 端口从 18900 起取第一个空闲端口，最多尝试到 18949，并行的 lane 不会冲突。
  - 主机名解析由代理在沙箱外完成。
  - 每个放行或拒绝决定都先记在内存里，结束时写入 `<output>/network.log`（JSON Lines），记录主机、端口、决定、对端 IP、字节数和耗时。沙箱进程无法改写这份日志；如果输出目录里已有沙箱进程写的 `network.log`，会被替换并在日志中标记。
  - 收到 SIGALRM、SIGTERM、SIGHUP 时（例如被 `perl -e 'alarm N'` 打断），仍然会终止子进程并写出日志。
- **环境变量**：设置 `HTTPS_PROXY`、`HTTP_PROXY`、`ALL_PROXY`（大小写两套），`NO_PROXY` 只包含 `localhost`。
- **队列脚本**：`run_kiro_cohort_net.sh` 是 `run_kiro_cohort.sh` 的副本，只把 `isolate.py` 换成了 `isolate_net.py`，deny 列表（含 `~/.paseo` 和 `~/cil/ainative/enterprise_agent`）、缓存目录和超时都不变。旧脚本、`isolate.py` 和已有证据都没有修改。

## 控制结果

`isolation_net_control.py` 用真实 deny 列表运行，与 cohort 一致，结果保存在 `docs/evidence/network-isolation/control.json`：22/22 通过，其中 2 项为真实 Kiro 调用。

- **文件隔离（10 项，原有检查全部保留）**：evaluator 和 seed 不可读、不可列，HOME 不可写，包和输出目录可写；`~/.paseo`、`~/cil/ainative/enterprise_agent`、`~/.kiro`、`~/.codex` 均不可读。
- **经代理访问**：`curl https://github.com` 和 `curl https://api.github.com` 失败（curl 56，CONNECT 返回 403）；`git ls-remote https://github.com/XUJiahua/enterprise_agent` 失败（128）。三次的拒绝都在 `network.log` 里有记录。
- **绕过代理直连**（`--noproxy '*'`）：以下三个 IP 在沙箱外都能连通（基线），在沙箱内都失败，且代理日志里没有任何记录。这证明拦截来自 Seatbelt，而不是代理。
  - github 公开地址 140.82.112.3；
  - 本机解析出的 github IP；
  - 白名单模型主机的 IP。
- **DNS**：沙箱内的 `getaddrinfo` 失败。
- **本机其他端口**：控制脚本在 127.0.0.1 上开一个监听端口，沙箱外可以连上，沙箱内不行。本机实际有 FlClash 7890 代理、Docker PostgreSQL、Paseo 等服务，这条规则防止经它们绕过。
- **白名单正向检查**：经代理访问 `https://runtime.eu-central-1.kiro.dev` 成功，日志记录为 allow。
- **日志防伪**：沙箱内预先写入的 `network.log` 被替换。
- **Kiro READY**：使用与正式 trial 相同的 `poc-v3-generator` agent 配置，返回 `READY`。
- **Kiro 工具调用**：同一 agent，开启 `--trust-all-tools`，让模型执行 `curl https://github.com`。模型如实汇报 `curl: (56) CONNECT tunnel failed, response 403 / 000 EXIT=56`，日志中有 github.com 的 deny 记录（`kiro-curl-github.network.log`）。

其中 Kiro 两项是在加入信号处理之前运行的。之后的改动只涉及收尾逻辑（终止子进程、写日志），沙箱规则和代理均未改动；修改后无 Kiro 的 20 项已重新运行并全部通过，结果见 `control-no-kiro-final.json`。另外做过两项单独验证：

- 只用本地模块缓存时，`go vet` 和 `go test` 在沙箱内能正常编译，没有产生任何网络连接。
- 在已归档的 293 个候选 patch 中，没有一个使用 `httptest.NewServer` 或 `net.Listen`。

本轮共真实调用 Kiro 4 次：发现 1 次、READY 1 次、curl 工具调用 1 次、`web_search` 残余风险探针 1 次。没有生成任何实验候选。

复现：

```bash
H=$HOME; python3 benchmark/phase3/isolation_net_control.py --kiro --evidence /tmp/net-evidence \
  --deny "$H/.paseo" --deny "$H/cil/ainative/enterprise_agent" --deny "$H/.kiro" --deny "$H/.codex" \
  --allow-write "$H/Library/Caches" --allow-write "$H/go/pkg/mod" \
  --allow-write "$H/Library/Application Support/kiro-cli" --allow-write "$H/.aws/sso/cache"
```

## 局限（仍不能保证的部分）

1. **服务端检索可以绕过限制（已实测）。**
   - 在 `isolate_net.py` 下用默认 agent 运行 kiro-cli，`web_search` 返回了 GitHub 搜索结果（`SEARCH=ok`），因为它在服务端执行，流量走已放行的 runtime 主机。`web_fetch` 在客户端执行，被代理拦截（`FETCH=failed: HTTP 403`）。证据见 `residual-websearch.*`。
   - 正式 trial 的 agent 只暴露 `fs_read`、`fs_write`、`execute_bash`，没有 `web_search`。但沙箱内能读取 Kiro 的认证状态，模型可以通过 `execute_bash` 再启动一个默认配置的 `kiro-cli` 来搜索。
   - 代理只能看到 TLS 外层的主机名，无法区分同一主机上的“生成”和“搜索”请求。
   - 目前只能事后审计：逐条检查每个 trial 的 shell 命令里是否出现嵌套的 `kiro-cli`，并看 `network.log` 中 runtime 连接数是否异常。
   - 要真正盲测，需要把公开仓库中的 Oracle 和参考答案移走（例如仓库改为私有），或者使用不提供服务端检索的模型端点或凭据。
2. **模型本身的知识与服务端行为在边界之外。** 训练数据或服务端缓存里可能已有公开仓库的内容，任何网络隔离都覆盖不到。
3. **同一用户、同一宿主。** Seatbelt 不是 VM 级隔离，内核或 Seatbelt 本身的漏洞不在防护范围内。`sandbox-exec` 已被标记为 deprecated，但目前仍然可用。验收端与生成端在同一台机器上运行。
4. **白名单是按主机名放行。** 代理不检查 TLS 内容和 SNI 是否一致。按目前的白名单，这只允许连到 Kiro 的两个主机本身。本机 DNS 由 FlClash 返回 198.18/15 段的 fake-IP 后转发，代理信任本机的解析和路由结果。
5. **实验条件的变化。**
   - 沙箱内禁止连接 localhost 上除代理外的端口，所以在 loopback 上起服务器的测试会失败（实测 `httptest.NewServer` 报 `operation not permitted`）。历史候选中没有这样的写法，但它与旧 cohort 的条件不同。
   - 如果以后提供“公共测试服务”（`visible_smoke.py`），需要另外放行该端口。
   - Go 模块只能来自本地缓存，`proxy.golang.org` 被拒绝。
6. **白名单依赖具体环境。** 它绑定 kiro-cli 3.3.0 和当前账号所在的 eu-central-1 区域。版本、区域或账号变化后需要重新运行 `--discover` 和控制。
