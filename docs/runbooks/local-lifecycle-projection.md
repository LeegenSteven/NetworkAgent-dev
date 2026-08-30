# Local Canonical 生命周期安全投影运行手册

> 工作包：S4-02 Canonical 生命周期安全投影
> 当前状态：`DONE`（仅限本手册定义的窄切片）
> 已验收 release candidate：`69643e8a6f79b1264d60e5517eeb9a24035c8e7d`
> 适用范围：无 Docker、无 GCP 凭据、无外部网络动作的 durable Canonical record 只读投影

## 1. 先读结论与边界

本手册让未参与开发的复核者以一个固定命令重跑原生双分支答辩流程，并在 exact retry
之后从完整持久 Canonical records 构建隐私最小化投影。成功分支必须为
`RESOLVED/PASSED`，故意验证失败分支必须为 `REOPENED/FAILED`；每个分支必须恰有
revision 0–7 八个原子组和 14 个唯一事件。

投影是 `DERIVED_FROM_DURABLE_CANONICAL_RECORDS`，冻结
`ordering=REVISION_GROUPED_ATOMIC_PROJECTION`、`read_only=true`、
`distributed_trace=false`。它验证 single Incident、精确记录绑定、连续 revision、单次执行
尝试和 `side_effects=false`，但不改变 Incident、审批、动作、ActionRun、Verification 或
Audit。原生演示仍负责 exact retry 和两个 marker-owned 工作区的安全清理。

S4-02 不是运行时结构化日志，不是 OpenTelemetry/Collector，不是 Prometheus，不是分布式
trace，不定义 SLI/SLO，也不投递外部告警。完成本手册不能推导 S4、Workflow E、P7、
S7、Gate E/G5、G2/G4、Cloud Staging 或生产已完成。

## 2. 前提与唯一命令

从仓库根目录使用项目的 Python 3.12 或 3.13 环境。形成 commit-bound 证据时，应检出上方
精确 RC，确保运行前后提交不变且 tracked tree 始终干净。无需 Docker、GCP/Cloud 凭据、
`gcloud` 登录、模型 API 或外部网络访问。

```text
python tools/local-stack/run_lifecycle_evidence_demo.py --approve-local-simulation
```

这是唯一演示命令，且唯一允许的参数是显式确认
`--approve-local-simulation`。workspace、URL、header、actor、Cloud、Docker、真实动作、
任意命令或其他参数都会失败关闭。包装器复用 `run_defense_demo.py`，不会改变
`local_stack.py` 的默认禁用动作路径。

成功时进程以状态 0 在 stdout 输出一个 JSON。失败时以状态 2 在 stderr 输出固定、有界且
不反射输入的错误 JSON。不要通过添加参数、修改数据库或绕过校验来取得成功输出。

## 3. 外层证据字段

成功 stdout 的顶层字段必须精确为以下 allowlist；保留的
`local-lifecycle-report.json` 包含除 stdout-only `report` 之外的同一正文：

| 字段 | 必须看到的值或含义 |
|---|---|
| `schema` | `networkagent-local-lifecycle-evidence/1.0`。未知 schema 立即停止解读。 |
| `ok` | 严格布尔值 `true`。 |
| `classification` | clean、stable、commit-bound 运行必须为 `LOCAL_CANONICAL_LIFECYCLE_EVIDENCE`；否则只能为 `LOCAL_WORKTREE_CANONICAL_LIFECYCLE_EVIDENCE`。 |
| `source` | 只允许 `binding_stable/commit_bound/commit_sha/git_available/tracked_clean`；RC 证据的 `commit_sha` 必须等于受测 SHA。 |
| `branches` | 键精确为 `success/failure`，值为第 4 节投影。 |
| `coverage` | 第 7 节的 delivered/not-claimed 固定声明。 |
| `privacy` | 第 6 节七项精确隐私结论。 |
| `proof` | `branch_count=2`、`projected_event_count=28`、`revision_group_count=16`，并包含双分支 terminal/exact-retry/cleanup 证明。 |
| `report` | 只允许 `filename/bytes/sha256`；filename 固定为 `local-lifecycle-report.json`。 |

`proof.terminal.success` 必须为 `RESOLVED/PASSED`、`closed_loop=true`；
`proof.terminal.failure` 必须为 `REOPENED/FAILED`、`closed_loop=false`。两者的
`expected_business_result` 都必须为 `true`，`proof.exact_retry` 与 `proof.cleanup` 的
success/failure 都必须为严格布尔值 `true`。布尔值不得用整数 `0/1` 代替。

## 4. 每个分支的投影字段

每个 `branches.*` 投影的顶层字段必须精确为：

| 字段 | 冻结值或规则 |
|---|---|
| `schema` | `networkagent-local-lifecycle-projection/1.0` |
| `classification` | `DERIVED_FROM_DURABLE_CANONICAL_RECORDS` |
| `read_only` | 严格布尔值 `true` |
| `distributed_trace` | 严格布尔值 `false` |
| `ordering` | `REVISION_GROUPED_ATOMIC_PROJECTION` |
| `scenario` | success=`LOCAL_SIMULATION_RESOLVED`；failure=`LOCAL_SIMULATION_REOPENED` |
| `terminal_status` | success=`RESOLVED`；failure=`REOPENED` |
| `record_counts` | 第 4.1 节的精确计数 |
| `invariants` | 第 4.2 节的精确不变量 |
| `revision_groups` | 恰 8 组；每组只含 `revision/events`，revision 精确为 0–7。 |

每个 event 只允许
`sequence/occurred_at/record_type/component/operation/outcome` 六个字段。sequence 在每个
分支内精确为 1–14；`occurred_at` 是该持久记录自身的 UTC 时间属性，不作为 label，也不
要求跨不同记录单调。生命周期顺序只由 revision 原子组和组内固定序列定义。

### 4.1 精确记录计数

```text
incidents=1
incident_audit_events=8
rca_reports=1
remediation_actions=1
approval_decisions=2
action_runs=1
verification_runs=1
projected_events=14
```

### 4.2 精确不变量

```text
single_incident=true
bindings_exact=true
revision_contiguous=true
single_execution_attempt=true
side_effects=false
```

所有键、值和类型都必须精确匹配；缺失、重复、额外记录，错误 revision/state/binding，
approval sequence/idempotency 绑定漂移，重复 ActionRun，Verification 契约漂移或任何
side effect 都必须失败关闭，不能生成看似成功的投影。

## 5. 14 节点 revision-grouped 事件图

Audit 节点统一使用 `record_type=INCIDENT_AUDIT_EVENT`、
`component=INCIDENT_REPOSITORY`、`operation=RECORD_STATE_TRANSITION`。两个分支只有最后两
个节点的 outcome 不同。

| sequence | revision | record_type | component | operation | success outcome | failure outcome |
|---:|---:|---|---|---|---|---|
| 1 | 0 | `INCIDENT_AUDIT_EVENT` | `INCIDENT_REPOSITORY` | `RECORD_STATE_TRANSITION` | `DETECTED` | `DETECTED` |
| 2 | 1 | `INCIDENT_AUDIT_EVENT` | `INCIDENT_REPOSITORY` | `RECORD_STATE_TRANSITION` | `TRIAGED` | `TRIAGED` |
| 3 | 2 | `INCIDENT_AUDIT_EVENT` | `INCIDENT_REPOSITORY` | `RECORD_STATE_TRANSITION` | `INVESTIGATING` | `INVESTIGATING` |
| 4 | 3 | `RCA_REPORT` | `RCA_GATEWAY` | `PROPOSE_REPORT` | `CONCLUSIVE` | `CONCLUSIVE` |
| 5 | 3 | `REMEDIATION_ACTION` | `GOVERNANCE_ENGINE` | `PROPOSE_ACTION` | `LOCAL_SIMULATION` | `LOCAL_SIMULATION` |
| 6 | 3 | `INCIDENT_AUDIT_EVENT` | `INCIDENT_REPOSITORY` | `RECORD_STATE_TRANSITION` | `RCA_COMPLETE` | `RCA_COMPLETE` |
| 7 | 4 | `APPROVAL_DECISION` | `APPROVAL_GATEWAY` | `REQUEST_NETWORK_ACTION_APPROVAL` | `PENDING` | `PENDING` |
| 8 | 4 | `INCIDENT_AUDIT_EVENT` | `INCIDENT_REPOSITORY` | `RECORD_STATE_TRANSITION` | `AWAITING_APPROVAL` | `AWAITING_APPROVAL` |
| 9 | 5 | `APPROVAL_DECISION` | `APPROVAL_GATEWAY` | `DECIDE_NETWORK_ACTION_APPROVAL` | `APPROVED` | `APPROVED` |
| 10 | 5 | `INCIDENT_AUDIT_EVENT` | `INCIDENT_REPOSITORY` | `RECORD_STATE_TRANSITION` | `REMEDIATING` | `REMEDIATING` |
| 11 | 6 | `ACTION_RUN` | `SIMULATED_ACTION_GATEWAY` | `EXECUTE_LOCAL_SIMULATION` | `SUCCEEDED` | `SUCCEEDED` |
| 12 | 6 | `INCIDENT_AUDIT_EVENT` | `INCIDENT_REPOSITORY` | `RECORD_STATE_TRANSITION` | `VERIFYING` | `VERIFYING` |
| 13 | 7 | `VERIFICATION_RUN` | `LOCAL_VERIFICATION_GATEWAY` | `VERIFY_LOCAL_SIMULATION` | `PASSED` | `FAILED` |
| 14 | 7 | `INCIDENT_AUDIT_EVENT` | `INCIDENT_REPOSITORY` | `RECORD_STATE_TRANSITION` | `RESOLVED` | `REOPENED` |

原子组大小必须依次为 `1/1/1/3/2/2/2/2`。RCA report、去重后的唯一
`LOCAL_SIMULATION` action 与 `RCA_COMPLETE` Audit 必须同属 revision 3；pending/awaiting、
approved/remediating、ActionRun/verifying、Verification/terminal Audit 分别绑定 revision
4/5/6/7。

## 6. 隐私与只读复核

`privacy` 必须精确为：

```text
status=PASS
absolute_paths_recorded=false
domain_hashes_recorded=false
domain_identifiers_recorded=false
pseudonymous_correlation_recorded=false
raw_records_recorded=false
workspace_identifiers_recorded=false
```

投影中不得出现 Incident/RCA/action/approval/run/verification/event/resource/workspace ID，
不得出现 action hash、domain hash、idempotency key、correlation/trace/scenario-projection ID，
不得出现 resource、KPI、root cause、evidence URI、actor、reason、路径、环境、stdout 或
stderr。时间只允许作为 event 的 `occurred_at` 属性。

外层 evidence envelope 的 `source.commit_sha` 与 `report.sha256` 是源码/制品完整性证据，
不属于 domain 投影，也不得被解释为业务 correlation 或 distributed trace。包装器同时
核对投影前后 durable database bytes 不变、两分支 exact retry 不放大记录、双工作区已经
安全清理。任何一项不满足都使本次证据无效。

## 7. 覆盖声明

`coverage.delivered` 精确为：

- `DURABLE_CANONICAL_RECORD_PROJECTION`
- `REVISION_GROUPED_ATOMIC_LIFECYCLE`
- `DUAL_TERMINAL_BRANCH_EVIDENCE`
- `READ_ONLY_PROJECTION_AFTER_EXACT_RETRY`

`coverage.not_claimed` 精确为：

- `OPEN_TELEMETRY_EXPORT`
- `DISTRIBUTED_TRACE`
- `RUNTIME_STRUCTURED_LOGGING`
- `CROSS_HTTP_REPLAY_A2A_MCP_TRACE`
- `PROMETHEUS_METRICS`
- `SERVICE_LEVEL_OBJECTIVES`
- `EXTERNAL_ALERT_DELIVERY`
- `GATE_E_OR_G5_CLOSURE`
- `CLOUD_OR_PRODUCTION_EXECUTION`

缺少任一限制项或新增未评审声明都应按契约漂移处理。

## 8. 报告与 release artifact 复核

### 8.1 本地报告

stdout 的 `report.filename` 固定为 `local-lifecycle-report.json`，`report.bytes` 与
`report.sha256` 分别绑定保留报告的原始 UTF-8 字节数和小写 SHA-256。为避免路径泄漏，
stdout 不输出 run/workspace 路径；报告保留在本次 marker-owned defense run 目录中。复核
时应比较运行前后的固定 `.local/networkagent-defense` 目录清单，只选择本次新增且非链接的
普通报告文件，不按时间或随机令牌猜测路径。

读取 stdout JSON 后删除顶层 `report`，按 UTF-8、键排序、无多余空格的 JSON 编码并追加
一个换行，应与保留报告逐字节相同。文件类型、bytes、SHA 或正文任一不一致，均按
`report_write_failed`/契约漂移处理。本地长期目录可能包含历史报告；不要递归删除
`.local`，也不要把历史报告当成本次结果。

### 8.2 已验收 artifact 元数据

| 项目 | 精确值 |
|---|---|
| RC | `69643e8a6f79b1264d60e5517eeb9a24035c8e7d` |
| Local run | [33336341831](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831) |
| Artifact | [9739212391](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/artifacts/9739212391) |
| 名称 | `telco-local-release-py3.12-attempt-1` |
| 分类 | `VERIFIED RC` |
| Archive bytes | `115482` |
| Archive digest | `sha256:237bcb207e29e53aba6c907f94efcb1999b77b1640f59dd95b8d3c9c30b27aa7` |
| 到期时间 | `2026-09-13T21:30:29Z` |

独立下载后的复核顺序如下：

1. GitHub 元数据中的 ID、name、bytes、archive digest 和 expiry 必须与上表
   逐项相等。`VERIFIED RC` 分类不由 GitHub artifact API 元数据提供，必须在解包后
   由 release manifest 及其 verification evidence 独立证明。
2. 解包后必须恰有 12 个普通文件；`release-manifest.json` 必须精确记录其余 11 个文件，
   无额外、缺失、重复或摘要漂移。
3. 对 11 个 manifest records 逐一重算 bytes/SHA-256；manifest 的状态、受测 SHA、Python
   版本和 supplemental-evidence 条目必须匹配该 run。生命周期 summary 是原 defense 与
   observability summaries 之后的第三个 supplemental evidence。
4. `release-evidence/local-lifecycle-summary.json` 必须为 8,431 bytes，SHA-256 必须为
   `5726b4eec6f0c4621a3d804b0f6973a24d41ae14ea1878b2e57205a299966e45`。
5. Summary 必须满足第 3–7 节。删除 stdout-only `report` 后按第 8.1 节重建正文，必须得到
   8,290 bytes / SHA-256
   `21528fd8694da0cb0c51452b14c45af864b24951282475367addbcd1b74fa004`，并与 summary
   内 `report.bytes/report.sha256` 精确相等。

该 artifact 只由 Python 3.12 job 上传；Python 3.13 同样执行并验证生命周期包装器，但不
上传重复 artifact。到期后若 GitHub 不再提供下载，以上元数据仍是历史证据，不能伪造新的
下载或把其他 artifact 替代为 9739212391。

## 9. 同 SHA GitHub Actions 证据

所有下表 run 的 `headSha` 都精确等于受测 RC，且列出的每个 job 都为 `success`：

| 范围 | Run | Jobs | 结论边界 |
|---|---|---|---|
| Local | [33336341831](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831) | [99323794962](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/job/99323794962) (3.12), [99323795037](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/job/99323795037) (3.13) | 每版 Domain + Local `576 passed`、local-stack `89 passed, 2 skipped`、Local E2E `2 passed`；3.12 release boundary `18 tests passed`。 |
| Assurance | [33336341877](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341877) | 99323794957 / 99323795038 / 99323795061 / 99323795122 | 同 SHA Assurance 回归全绿。 |
| Container | [33336341805](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341805) | 99323794954 / 99323831112 | 同 SHA container workflow 全绿；不改变 S2-04 `BLOCKED`。 |
| Cloud | [33336341859](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341859) | 99323794980 / 99323795082 / 99323795085 / 99323795200 | CI/Emulator 路径全绿；不是 Cloud Staging、真实 IAM/OIDC/DLQ/WIF 或生产验收。 |
| Data Lab | 未触发 | 无 | 路径规则未触发，不能写成 success、failure 或同 SHA Lab 回归。 |

本证据文档提交晚于且不等于受测 RC。远程结论只绑定上表 SHA、runs、jobs 和 artifact，
不能把文档提交本身写成受测源码。

## 10. 失败与清理语义

| 错误码 | 含义与处理 |
|---|---|
| `confirmation_required` / `invalid_arguments` | 只使用第 2 节唯一命令。 |
| `command_failed` | 原生固定流程未安全完成；保留稳定 stderr JSON，核对 Python/RC/source binding 后再运行。 |
| `evidence_contract_failed` | defense terminal、exact retry、cleanup 或 source 契约漂移；不得放宽解析。 |
| `lifecycle_contract_failed` | durable records、字段 allowlist、revision graph、计数、不变量、隐私或类型不匹配；本次投影无效。 |
| `cleanup_failed` | 至少一个 marker-owned workspace 未确认清理；本次证据无效。 |
| `report_write_failed` | 报告目录/文件身份、原子写入、大小或摘要复核失败；不要绕过链接/reparse/普通文件保护。 |

包装器先让原生演示执行受保护 reset，再发布成功结论。不要对仓库根、home、文件系统根或
`.local` 执行递归删除；不要手工删除 marker 来伪造 cleanup。若 stderr 返回稳定错误，保留
该输出和已存在的报告，停止证据认定，修复契约后以新 RC 重新验收。

## 11. 限制声明

- S4-02 只证明固定 Local Profile 双终态的 durable Canonical lifecycle 安全投影，不是完整
  可观测平台，也不是通用任意 Incident 导出接口。
- `distributed_trace=false`；没有运行时结构化日志、OpenTelemetry export/Collector、
  Tempo/Jaeger/Grafana、Prometheus、跨 HTTP/Replay/A2A/MCP/Repository trace、SLI/SLO、
  外部告警或 Collector 故障容忍证据。
- 投影故意不输出 domain/workspace 标识与哈希、路径、correlation、原始记录或业务证据；
  source commit/report/archive SHA 只用于源码与 artifact 完整性复核。
- 本命令不启动容器、不读取 Cloud/GCP 凭据、不执行真实网络动作；同 SHA Cloud workflow
  全绿不能外推为 Cloud Staging、生产、真实 Spanner/GKE/Operator、IAM/OIDC、DLQ、
  Workload Identity、备份/恢复或真实回滚已验收。
- S4-02=`DONE`；S4、Workflow E、P7 和 S7 继续 `IN PROGRESS`；Gate E/G5、G2/G4 保持
  开放；S2-04 继续 `BLOCKED`。
