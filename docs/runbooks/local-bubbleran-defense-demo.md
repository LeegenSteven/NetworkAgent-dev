# Local BubbleRAN 四分支答辩演示运行手册

> 工作包：S7-03（窄切片 `DONE`）
> 受测 release candidate：`46318cbf84b65c3060358dffb49b829479803308`
> 证据分类：`LOCAL_BUBBLERAN_VERTICAL_DEFENSE_EVIDENCE`
> 适用范围：离线、单进程、真实 loopback TCP、零真实网络副作用
> 重要边界：输入是 `CODE_GENERATED_SCHEMA_FIXTURE`，不是 BubbleRAN 完整上游 benchmark 或上游原始字节

## 1. 演示目标与唯一入口

本演示用 4 条代码生成、符合已冻结 BubbleRAN CSV schema 的记录，依次验证：安全适配、真实 loopback TCP Replay、caller-owned 持久 checkpoint、4 个独立 Canonical Incident、四条治理终态，以及绕过已完成 checkpoint 后的 settled exact replay 零业务放大。它不下载上游数据，也不需要 GCP、模型 API、Docker、浏览器登录或个人账号。

从仓库根目录只运行以下固定命令：

```text
python tools/local-stack/run_bubbleran_defense_demo.py --offline --approve-local-simulation
```

Windows 项目虚拟环境可把开头的 `python` 换成 `.venv/Scripts/python.exe`。命令只接受 `--offline` 与 `--approve-local-simulation`；两者都必须显式给出，不接受 workspace、URL、数据路径、任意命令、actor、Cloud、Docker 或真实动作参数。

成功时 stdout 只有一份 `networkagent-local-bubbleran-defense-evidence/1.0` JSON。运行目录位于 `.local/networkagent-bubbleran-defense/` 下；临时 `work` 树会按捕获的文件身份安全清理，最终只保留原子发布的 `local-bubbleran-defense-report.json`。stdout 比持久报告多一个 `report` envelope，用于给出固定文件名、字节数和 SHA-256。

## 2. 演示前检查（约 1 分钟）

1. 使用 Python 3.12 或 3.13，并安装仓库的 Domain、Lab、Local 与 Assurance 依赖。
2. 在仓库根目录执行；不要把 `.local` 指向符号链接、junction、reparse point 或共享目录。
3. 确认不需要也不会提供 GCP 凭据、真实 Engineer/Operator 地址或外部数据 URL。
4. 如需形成 commit-bound 证据，先确认 Git 可用、当前提交固定，并且受跟踪文件在运行前后均保持干净。否则输出会诚实标记 `WORKTREE_ONLY`，不能作为受测 RC 证据。
5. 说明数据口径：4 条记录由代码生成，只用于验证冻结 schema 和治理链路；不得称为完整 BubbleRAN 上游数据集、真实生产流量或准确率 benchmark。

## 3. 6–8 分钟答辩顺序

### 第 1 分钟：范围与安全边界

展示唯一命令及 `scope`：`offline=true`、`network=REAL_LOOPBACK_TCP`、`execution=LOCAL_SINGLE_PROCESS`、`action_mode=DISABLED`、`data=CODE_GENERATED_SCHEMA_FIXTURE`。强调真实的是本机 TCP sender/receiver、DuckDB 持久化和治理状态机，不真实的是网络整改；动作契约固定为 `LOCAL_SIMULATION` 且 `side_effects=false`。

### 第 2 分钟：4 条 fixture 到 4 个独立 Incident

展示 `fixture.origin=CODE_GENERATED_SCHEMA_FIXTURE`、`fixture.record_count=4`，以及：

- `proof.canonical_cases.count=4`；
- `proof.canonical_cases.source_associations=4`；
- `proof.canonical_cases.independent=true`。

这证明每个已验证 source event 独立映射一个 Canonical Incident，不代表 episode 或跨事件聚合已经完成。

### 第 3 分钟：持久 checkpoint 与重启

展示 `proof.checkpoint`：

- 首次运行 `selected/attempted/delivered = 4/4/4`；
- 重新打开同一个完成态 store 后为 `0/0/0`；
- `settled=true`。

checkpoint 是 caller-owned、plan-bound 的本地 continuation claim，不是接收端签名 ACK，也不是共享或高可用 checkpoint 服务。

### 第 4–5 分钟：四条治理终态

按 `proof.governance.terminal` 的固定顺序解释四个独立分支：

| 分支 | Incident 终态 | Verification | ActionRun | VerificationRun |
|---|---|---|---:|---:|
| `APPROVED_PASS` | `RESOLVED` | `PASSED` | 1 | 1 |
| `APPROVED_FAIL` | `REOPENED` | `FAILED` | 1 | 1 |
| `REJECTED` | `REJECTED` | `NOT_RUN` | 0 | 0 |
| `APPROVAL_EXPIRED` | `FAILED` | `NOT_RUN` | 0 | 0 |

总计必须为 `action_runs=2`、`verification_runs=2`。`action_contract.type=LOCAL_SIMULATION` 且 `side_effects=false`；审批拒绝和审批过期分支均不得产生 ActionRun 或 VerificationRun。

### 第 6 分钟：绕过 checkpoint 的 settled exact replay

演示在治理完成后故意不使用 checkpoint，再向 receiver 投递同样的 4 个事件。`proof.settled_bypass.delivered=4`，但以下四类持久业务记录的 delta 必须全为 0：

- `cases`；
- `audit`；
- `source_associations`；
- `idempotency`。

实现还对四个 Incident 做完整对象深等复核；因此结论是 settled exact replay 没有放大业务记录或改变既有 Incident，而不是“网络没有收到重发”。

### 第 7–8 分钟：隐私、制品与限制

展示 `privacy.status=PASS`，并核对报告未记录绝对位置、source location、原始记录或敏感标识。说明 Python 3.12 release artifact 只收录隐私最小化 summary；CSV、DuckDB、JSONL、checkpoint 和本地 `work` 树都不上传。最后逐项宣读第 8 节的十项非声明，避免把这个窄切片扩写为 P3e、S7、Gate E 或生产验收。

## 4. 输出字段检查表

成功 stdout 的顶层字段为：

| 字段 | 核对内容 |
|---|---|
| `schema` | 精确等于 `networkagent-local-bubbleran-defense-evidence/1.0`。 |
| `classification` | 精确等于 `LOCAL_BUBBLERAN_VERTICAL_DEFENSE_EVIDENCE`。 |
| `ok` | 必须为 `true`。 |
| `fixture` | `origin=CODE_GENERATED_SCHEMA_FIXTURE`、`record_count=4`。 |
| `scope` | 离线、单进程、真实 loopback TCP、动作禁用、固定四记录场景。 |
| `proof` | 4 个独立 case、checkpoint `4/4/4 -> 0/0/0`、四终态、Action/Verification `2/2`、bypass 四类 delta 0。 |
| `privacy` | 四项不记录声明均为 `false`，`status=PASS`。 |
| `source` | Git 可用性、前后绑定稳定性、tracked-clean 与 commit SHA；不得凭 stdout 自行改写。 |
| `release` | 只有 `commit_bound=true` 才可为 `eligible=true` / `COMMIT_BOUND`。 |
| `coverage` | 六项 delivered 与十项 `not_claimed`。 |
| `report` | 只存在于 stdout；含持久报告的固定文件名、字节数和 SHA-256。 |

报告和 summary 的任意嵌套层都不得出现以下键：`body`、`event_id`、`events`、`ground_truth`、`incident_id`、`label`、`labels`、`path`、`ran_ue_id`、`raw`、`row`、`rows`、`source_event_id`、`source_url`、`trace_id`、`ue`、`ue_id`。

## 5. 失败处理与安全清理

失败时退出码为 2，stderr 只输出同一 schema 下的稳定 JSON 错误，不回显原始值、路径、异常堆栈或输入正文：

| 错误码 | 含义与处理 |
|---|---|
| `offline_required` | 缺少 `--offline`；使用唯一固定命令重跑。 |
| `confirmation_required` | 缺少本地模拟确认；确认范围后使用唯一固定命令。 |
| `invalid_arguments` | 出现冻结契约之外的参数；删除额外参数。 |
| `contract_failed` | 业务、身份、内容或证据契约不匹配；停止答辩，保留现场复核。 |
| `cleanup_failed` | 已捕获对象未能按身份安全清理；不要扩大删除范围。 |
| `report_write_failed` | 原子报告发布或回读身份/摘要校验失败；不得使用不完整报告。 |
| `command_failed` | 未分类的安全失败；保留 stderr 与运行目录进行人工复核。 |

成功运行只留下一个普通文件 `local-bubbleran-defense-report.json`。清理本地证据时，先在 `.local/networkagent-bubbleran-defense/` 下人工选定本次精确 run ID，确认其不是链接、目录内只有预期报告，再删除这个精确目录；不要对 `.local`、仓库根目录、通配符或计算出的未复核路径做递归删除。

失败时不能承诺零残留。脚本只删除已安全捕获且身份未变化的本次对象；首次身份捕获失败、被替换、竞态或未知身份对象会故意保留供检查。遇到这类残留，不要自动重试清理，也不要把未知对象认作脚本所有物。

## 6. 受测 RC 与远程结果

S7-03 的唯一受测 RC 为 `46318cbf84b65c3060358dffb49b829479803308`。以下 3 个 workflows 的 8/8 jobs 全部成功；合计 122 个 step 为 `success`，另有 11 个 Python 3.13/release 条件 step 按设计跳过：

- [Assurance run 33366606140](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140)：jobs [99408450337](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140/job/99408450337)、[99408450434](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140/job/99408450434)、[99408450435](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140/job/99408450435)、[99408450555](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140/job/99408450555)；
- [Local run 33366606118](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606118)：jobs [99408450116](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606118/job/99408450116)、[99408450386](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606118/job/99408450386)；
- [Container run 33366606112](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606112)：jobs [99408450317](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606112/job/99408450317)、[99408503334](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606112/job/99408503334)。

Python 3.12 Assurance job 发布了保留 14 天的 [VERIFIED RC artifact 9748618894](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140/artifacts/9748618894)：

- 名称：`telco-assurance-release-py3.12-attempt-1`；
- 大小：248,105 bytes；
- GitHub archive SHA-256：`975a60d326eb97ea2557ae237bbff9dd957b327cdc04c2d117ef8cb58f262f14`；
- 精确闭包：13 个非链接普通文件，即 12 个 payload 加 `release-evidence/release-manifest.json`；
- `release-evidence/local-bubbleran-defense-summary.json`：2,374 bytes，SHA-256 `161354c5715b8a46730debcf7dd37658158d1ec338b469aa24f2bb2f3ddbc855`；
- 去除 stdout-only `report` envelope 并按规范 JSON 重建的持久报告：2,225 bytes，SHA-256 `4a07a35b7c5ca2e2f256351dc45bfdd7c5eac069b15f78d672f1eafa9c2aff42`。

本运行手册和其他 S7-03 证据文档提交晚于且不等于上述受测 RC。远程结论只绑定该 SHA、runs、jobs 与 artifact；不得把后续文档提交写成受测源码。

## 7. 制品独立复核

1. 从 artifact 链接核对 ID、名称、run、`headSha`、大小与 GitHub archive digest，再下载到一个全新的空目录。
2. 对下载的原始 ZIP 计算 SHA-256，必须精确等于 `975a60d326eb97ea2557ae237bbff9dd957b327cdc04c2d117ef8cb58f262f14`；摘要不符立即停止。
3. 安全解压到新的空目录，拒绝绝对路径、`..`、链接、设备项、重复/大小写碰撞路径和既有文件覆盖。闭包必须恰为 13 个非链接普通文件。
4. 解析 `release-evidence/release-manifest.json`，确认它精确记录其余 12 个文件；逐个重算 bytes 与 SHA-256，既不能有额外/缺失条目，也不能有摘要漂移。
5. 单独复核 `local-bubbleran-defense-summary.json` 的 2,374 bytes 与 SHA-256。确认 schema、classification、source commit binding、scope、privacy、fixture、proof 和十项 `not_claimed` 全部匹配本手册。
6. 从 summary 删除顶层 `report`，使用 UTF-8、键排序、紧凑分隔符、禁止 NaN、末尾单个换行重建 JSON。结果必须为 2,225 bytes，SHA-256 必须等于 `4a07a35b7c5ca2e2f256351dc45bfdd7c5eac069b15f78d672f1eafa9c2aff42`，并与 `report.bytes/report.sha256` 一致。
7. 递归检查制品：不得包含 `.csv`、`.duckdb`、`.jsonl` 或名称含 `checkpoint` 的成员；summary、重建报告和 manifest supplemental record 不得出现第 4 节的禁用 ID/路径/原始数据键。
8. 记录复核人、UTC 时间、原始 ZIP 摘要、manifest 结果和任何失败；SHA-256 只证明完整性，不证明发布者身份或签名。

## 8. 必须同时宣读的十项非声明

`coverage.not_claimed` 必须按以下冻结值完整出现：

1. `COMPLETE_UPSTREAM_BENCHMARK`
2. `RCA_EVAL_MULTI_SOURCE`
3. `CROSS_EVENT_AGGREGATION`
4. `PRODUCTION_ACCURACY`
5. `REAL_NETWORK_REMEDIATION`
6. `CLOUD_OR_GCP_DEPLOYMENT`
7. `OPEN_TELEMETRY_OR_DISTRIBUTED_TRACE`
8. `UNIFIED_DASHBOARD`
9. `GATE_E_OR_G5_CLOSURE`
10. `P3E_OR_S7_OVERALL_CLOSURE`

因此，只有 S7-03 的“代码生成 fixture 四分支本地答辩入口”窄切片为 `DONE`。P3e-5 已获得独立 fixture 答辩入口，但 P3e-5 与 P3e 总体仍为 `IN PROGRESS`；RCAEval 第二条路径、跨事件/episode 聚合、完整上游 BubbleRAN 复核、容量/生产精度和发布终验尚未完成。S4、Workflow E、P7 与 S7 保持 `IN PROGRESS`；Gate E、G5、G2、G4 保持开放；S2-04 保持 `BLOCKED`；P6 统一 UI 保持 `NOT STARTED`。
