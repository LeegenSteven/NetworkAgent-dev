# Local 原生答辩演示运行手册

> 工作包：S7-02 运行手册与证据包
> 当前状态：`DONE`
> 已验收证据包 RC：`79feeee6771749bbdd1ce7ce44b77193a1db544f`
> 适用范围：无 Docker、无 GCP 凭据、无外部网络动作的本地模拟治理闭环

## 1. 先读结论与边界

本手册让未参与开发的复核者用一个固定命令演示两个隔离分支：正常验证到
`RESOLVED/PASSED`，故意验证失败到 `REOPENED/FAILED`。两个分支都只写本地
DuckDB 工作区，动作类型固定为 `LOCAL_SIMULATION`，`side_effects=false`；审批
请求会原样重放一次，以证明终态和记录数量不会放大，最后由安全 `reset` 删除两个
marker-owned 工作区并保留一份原子 JSON 报告。

这不是容器演示、Cloud 演练或真实网络修复，也不覆盖审批拒绝/过期。它不能用来
宣称 G2、G4、G5、S4、Cloud Staging 或生产能力已经完成。

## 2. 前提与准备

在新目录中检出精确基线，避免已有 tracked 修改使证据降级：

```text
git clone https://github.com/LeegenSteven/NetworkAgent-dev.git
cd NetworkAgent-dev
git checkout --detach 79feeee6771749bbdd1ce7ce44b77193a1db544f
```

准备 Python 3.12 或 3.13 的隔离环境，并安装 Domain 与 Local Profile 测试依赖。
Windows PowerShell 示例：

```text
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e "packages/telco-domain[test]"
.venv/Scripts/python.exe -m pip install -e "packages/telco-local[test]"
```

Linux/macOS 将解释器换成 `.venv/bin/python`。执行前应满足：

- 当前目录为仓库根目录，`git rev-parse HEAD` 等于上述 40 位 SHA；
- `git status --porcelain --untracked-files=no` 无输出；
- 不设置 GCP、Docker、代理、任意 URL 或动作参数；脚本会建立自己的最小安全环境；
- `.local` 与 `.local/networkagent-defense` 不是符号链接、junction 或 reparse point；
- 运行账号可写仓库内 `.local`，且磁盘可容纳两份短期本地数据库。

## 3. 唯一演示命令

激活项目虚拟环境后，只执行下面这一条演示命令：

```text
python tools/local-stack/run_defense_demo.py --approve-local-simulation
```

`--approve-local-simulation` 只表示同意执行本地模拟。脚本不接受 workspace、URL、
header、actor、Cloud、Docker、真实动作或任意子命令输入；增加其他参数会失败关闭。
成功时退出码为 `0`，标准输出恰好是一份 JSON。失败时退出码为 `2`，标准错误只给出
稳定、去路径化的 JSON 错误码。

## 4. 6–8 分钟讲解顺序

| 时间 | 展示内容 | 应讲清的结论 |
|---|---|---|
| 0:00–0:45 | 展示当前 commit 与 clean tracked tree | 演示绑定精确源码，而不是口头绑定版本。 |
| 0:45–1:15 | 朗读边界并执行唯一命令 | 本地、无 Docker、无 Cloud、无真实动作；显式同意模拟。 |
| 1:15–2:15 | 解释 doctor、init、status | 环境可用；每个分支固定加载 13,440 条 KPI、579 条安全 Trace、0 条初始 Incident，且 `external_access=false`。 |
| 2:15–3:30 | 解释 preview 与成功分支 | 15 个候选中选择确定性 Incident；先到 `AWAITING_APPROVAL`，审批复制 action hash 与 revision 4，最终为 `RESOLVED/PASSED`。 |
| 3:30–4:45 | 解释故意失败分支 | 相同治理链只改变验证结果；动作本身仍是成功的本地模拟，但验证失败使 Incident 进入 `REOPENED/FAILED`，不得伪报闭环。 |
| 4:45–5:45 | 解释 exact retry 与审计计数 | 原审批命令逐字重放；每支仍只有 1 个 ActionRun、1 个 VerificationRun、8 个审计事件，revision 固定为 7。 |
| 5:45–6:30 | 展示 cleanup 与 retained report | `success`、`failure` 都是 `workspace_removed=true`；只保留报告目录。 |
| 6:30–7:30 | 核对 commit、报告 SHA 与限制清单 | `commit_bound=true`、`commit_sha` 精确匹配；报告字节摘要可复核；`coverage.not_claimed` 明确列出未验收能力。 |

脚本运行期间只解释上述固定链路，不另开终端手工修改数据库或补跑“成功”动作。若命令
失败，应直接转入第 7 节，不用旧报告替代本次结果。

## 5. JSON 报告怎么读

成功 JSON 的关键字段如下：

| 字段 | 必须看到的值或含义 |
|---|---|
| `schema` | `networkagent-native-defense-demo/1.0`。未知 schema 不应继续解读。 |
| `ok` | 必须为 `true`。 |
| `classification` | 干净且前后为同一 commit 时是 `LOCAL_NATIVE_SIMULATION_EVIDENCE`；否则诚实降级为 `LOCAL_WORKTREE_SIMULATION_EVIDENCE`。 |
| `source` | `git_available`、`binding_stable`、`tracked_clean`、`commit_bound` 应均为 `true`，且 `commit_sha` 应等于受测 SHA。 |
| `dataset` | `performance_rows=13440`、`trace_rows=579`、`incident_rows=0`。 |
| `results.success.terminal` | `state=RESOLVED`、`verification=PASSED`、`closed_loop=true`。 |
| `results.failure.terminal` | `state=REOPENED`、`verification=FAILED`、`closed_loop=false`。 |
| `results.*.preview` | `candidate_count=15`、`state=AWAITING_APPROVAL`；动作是低风险 `LOCAL_SIMULATION`，绑定两个 LTE 资源与 revision 4。 |
| `results.*.verification` | revision 7；1 份 RCA、1 条建议、2 条审批记录、1 个 ActionRun、1 个 VerificationRun、8 个审计事件；`side_effects=false`。 |
| `results.*.exact_retry` | 三个布尔值均为 `true`，表示复用原审批命令且终态/验证不变。 |
| `cleanup.success` / `cleanup.failure` | 两者都必须是 `workspace_removed=true`。 |
| `coverage.not_claimed` | 明确不覆盖 Cloud、容器、完整 G2、G4 Cloud rehearsal、G5 最终验收、真实网络修复、拒绝/过期分支。 |
| `report.relative_path` | 本次保留报告相对仓库根目录的路径。 |
| `report.sha256` | 对保留报告原始 UTF-8 字节（含末尾换行）的 64 位小写 SHA-256。 |

`report` 指针只出现在标准输出；保留的 `defense-demo-report.json` 包含其余证据，不能把
标准输出重定向文件的摘要误当成保留报告摘要。

## 6. 报告与 SHA-256 核验

报告固定保存在：

```text
.local/networkagent-defense/<UTC时间-随机令牌>/defense-demo-report.json
```

从标准输出复制 `report.relative_path`，不要凭时间猜测目录。在 PowerShell 中核验：

```text
(Get-FileHash -Algorithm SHA256 -LiteralPath '<report.relative_path>').Hash.ToLowerInvariant()
```

Linux/macOS 使用 `sha256sum '<report.relative_path>'`。结果必须逐字符等于标准输出的
`report.sha256`。随后核对 `source.commit_sha` 与计划中的受测 RC；只要 SHA 不同、
`commit_bound=false`、任一终态不符、任一 workspace 未清理或摘要不符，本次就不能作为
commit-bound 答辩证据。

S7-02 的发布制品路径为 `release-evidence/defense-demo-summary.json`。Python 3.12 job
上传 release artifact；Python 3.13 仍执行并校验双分支。该文件作为 release manifest
的可选 supplemental evidence，manifest 记录其 bytes/SHA-256，随后由
`verify-manifest` 复核。RC `79feeee6771749bbdd1ce7ce44b77193a1db544f` 已按此契约
发布 VERIFIED RC artifact 9736785325；独立下载验证见第 8 节。S7-01 历史 artifact
9736486858 不含该 JSON，只保留为上一工作包的历史证据。

## 7. 失败处理与安全清理

| 错误码 | 含义 | 处理 |
|---|---|---|
| `confirmation_required` / `invalid_arguments` | 未给显式确认或加入了不允许的参数 | 只使用第 3 节固定命令重跑。 |
| `command_failed` | doctor、子命令、超时或严格 JSON 读取失败 | 保留 stderr JSON，确认 Python/依赖与仓库根目录，不要修改数据库后续跑。 |
| `evidence_contract_failed` | 实际数据、状态、计数或治理绑定偏离冻结契约 | 将本次判为失败并保留 run 目录，交由开发者复核。 |
| `report_write_failed` | 安全路径检查或原子报告写入失败 | 检查 `.local` 权限及链接/reparse 状态，不要绕过路径保护。 |
| `cleanup_failed` | 至少一个临时 workspace 未被安全 reset | 不要递归删除 `.local`；按下面步骤对精确目录执行受保护 reset。 |

脚本会在成功和业务失败时都尝试清理。若收到 `cleanup_failed`，先只读检查
`.local/networkagent-defense` 中本次目录，再分别对确实存在的 `success`、`failure`
目录执行：

```text
python tools/local-stack/local_stack.py --workspace '<exact-run-directory>/success' reset --yes
python tools/local-stack/local_stack.py --workspace '<exact-run-directory>/failure' reset --yes
```

`reset` 只接受 marker-owned 工作区；它拒绝仓库根、home、文件系统根、未标记目录和
链接/reparse 路径。不要使用递归删除命令。若 `defense-demo-report.json` 已成功生成，
则保留它供复核；较早发生的命令、契约或清理失败通常只会留下本次 run 目录或未清理的
workspace。若 reset 仍失败，停止操作并记录精确错误码。

## 8. 当前证据表

| 证据 | 已确认结果 | 适用边界 |
|---|---|---|
| 当前 S7-02 RC | `79feeee6771749bbdd1ce7ce44b77193a1db544f` | 演示证据包实现与 supplemental evidence 的受测源码；本次证据回填文档提交晚于且不等于该 RC。 |
| [Local run 33327786238](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786238) | jobs [99300888630](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786238/job/99300888630)（Python 3.12）/[99300888747](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786238/job/99300888747)（Python 3.13）全绿；两版各为 Domain + Local `518 passed`、local-stack `49 passed, 2 skipped`、Local E2E `2 passed`；3.12 release boundary `18 tests passed`。 | 两版都运行一键命令并核对 `commit_bound=true`、`commit_sha=GITHUB_SHA`、双终态、exact retry 与双清理。 |
| [Data Lab run 33327786237](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786237) | jobs [99300888644](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786237/job/99300888644) / [99300888752](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786237/job/99300888752) / [99300888782](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786237/job/99300888782) 全绿；各适用矩阵为 `220 passed, 3 skipped`。 | 同 RC 回归证据，不等同于本演示报告。 |
| [Assurance run 33327786211](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786211) | jobs [99300888597](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786211/job/99300888597) / [99300888619](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786211/job/99300888619) / [99300888628](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786211/job/99300888628) / [99300888662](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786211/job/99300888662) 全绿；两个主 job 各为 `854 passed, 3 skipped`，Supervisor 为 `57 passed`。 | 同 RC 回归证据，不等同于 Cloud 或生产验收。 |
| [VERIFIED RC artifact 9736785325](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786238/artifacts/9736785325) | 名称 `telco-local-release-py3.12-attempt-1`，104,109 bytes，archive digest `sha256:b4b6f6ab762695a367169d54078ab1f6d2ec64c4ef3c21c132190421ed31cff3`，到期时间 `2026-09-13T18:24:17Z`。 | 独立下载共 10 个文件：manifest 记录 9 个文件，加 manifest 自身；闭包无额外、缺失或摘要漂移。 |
| 演示 supplemental evidence | `defense-demo-summary.json` 为 3,379 bytes，SHA-256 `ae0b412a42d9430a35117dd9e8987662c7359cc95ea72a076fa2f869bcaa51ef`；其中 `report.sha256` 为 `a91676e52789d5c520d3cb3e2e8b0a47d19d7801f5bebbb51f3f10ffa613bc5f`。 | 独立核对双终态、exact retry、双 cleanup 与 source binding 均通过。 |
| S7-01 历史证据 | RC `c08d634c9c3deb628df5f98d4f60dd1675cd5706`；[artifact 9736486858](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33326721937/artifacts/9736486858) 为 102,753 bytes、archive digest `sha256:a1961b1897cdb86c802ce3dbd9762381ef7726e28476a1d24657162014b330f2`，且不含 `defense-demo-summary.json`。 | 只证明 S7-01，不替代当前 S7-02 VERIFIED RC。 |

## 9. 限制声明

- S7-01 与 S7-02 均已由各自精确 RC 验收并标记 `DONE`；S7 总体仍为
  `IN PROGRESS`，不能由两个子工作包推导阶段关闭。
- 本演示不启动容器、不访问外部网络、不读 Cloud/GCP 凭据，不执行真实网络动作。
- 失败分支证明验证失败会 reopen，不代表已覆盖审批拒绝或审批过期；这些属于其他 E2E
  证据，不能从本命令推导。
- S2-04 仍为 `BLOCKED`：完整容器 Critical/High `0/0` 尚未在兼容、可追溯的
  CPython 3.12/glibc 基础镜像上实现。
- 本证据不关闭 G2、G4、G5 或 S4，不代表 Cloud Staging、真实 IAM/OIDC/DLQ、GKE、
  Spanner、Operator、真实回滚或生产可用性已经验收。
