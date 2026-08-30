# Local 答辩可观测证据运行手册

> 工作包：S4-01 本地答辩可观测证据
> 当前状态：`DONE`（仅限本手册定义的窄切片）
> 已验收证据包 RC：`cb4a4e7191f67aa71ef980668352d55001e23142`
> 适用范围：无 Docker、无 GCP 凭据、无外部网络动作的本地答辩进程内观测

## 1. 先读结论与边界

本手册让未参与开发的复核者以一个固定命令执行原生双分支答辩流程，同时得到一份
隐私最小化的阶段事件报告。包装器复用且不改变 `run_defense_demo.py`：成功分支必须到
`RESOLVED/PASSED`，故意验证失败分支必须到 `REOPENED/FAILED`；两者都要证明 exact
retry 和 marker-owned 工作区清理。

S4-01 只交付本地进程内的有界阶段事件、诊断性单次时序快照、低基数报告内指标聚合、
四项报告内固定告警求值和对应处置步骤。它不是 OpenTelemetry export 或 Collector，不是
跨 HTTP/Replay/A2A/MCP/Repository 的分布式 trace，不是 Prometheus 指标，不提供外部
告警投递，也没有定义或证明 SLI/SLO。`propagated_trace=false` 是冻结契约。

Local 数据集中的 579 条安全 Trace rows 是输入数据记录，不是 OpenTelemetry span。完成
本手册不能推导 S4、Workflow E、Gate E、G5 或 S7 已关闭。

## 2. 前提与唯一命令

从仓库根目录运行 Python 3.12 或 3.13。若要形成 commit-bound 证据，应检出精确 RC，
确保运行前后为同一提交且 tracked tree 始终干净。命令不需要 Docker、GCP/Cloud 凭据、
`gcloud` 登录或外部网络访问：

```text
python tools/local-stack/run_observability_demo.py --approve-local-simulation
```

唯一接受的参数是显式确认 `--approve-local-simulation`。workspace、URL、header、actor、
Cloud、Docker、真实动作、任意命令或其他参数都会失败关闭。包装器会在
`.local/networkagent-defense` 下使用原生演示创建的随机、marker-owned run 目录；不要
预建、猜测或替换该目录。

## 3. 成功事件图

成功报告的 `events` 必须严格包含 21 个子进程事件和 1 个 `run_finalize`，共 22 个；
内部硬上限为 24。事件顺序与重试次数如下：

| 顺序 | stage | branch | attempt |
|---:|---|---|---:|
| 1 | `source_revision` | `none` | 1 |
| 2 | `source_cleanliness` | `none` | 1 |
| 3 | `preflight` | `none` | 1 |
| 4–6 | `workspace_init` → `workspace_status` → `governance_preview` | `success` | 1 |
| 7–10 | `approval_execute` → `terminal_verify` → `approval_execute` → `terminal_verify` | `success` | 1, 1, 2, 2 |
| 11–13 | `workspace_init` → `workspace_status` → `governance_preview` | `failure` | 1 |
| 14–17 | `approval_execute` → `terminal_verify` → `approval_execute` → `terminal_verify` | `failure` | 1, 1, 2, 2 |
| 18 | `workspace_cleanup` | `success` | 1 |
| 19 | `workspace_cleanup` | `failure` | 1 |
| 20 | `source_revision` | `none` | 2 |
| 21 | `source_cleanliness` | `none` | 2 |
| 22 | `run_finalize` | `none` | 1 |

每个事件只允许
`sequence/stage/branch/attempt/outcome/duration_ms/error_class` 七个字段。成功报告中每个
事件的 `outcome` 都为 `SUCCEEDED`、`error_class` 都为 `NONE`，duration 为非负整数。
业务上的 `REOPENED/FAILED` 是故意验证失败分支的正确结果，不是执行错误。

## 4. JSON 报告怎么读

标准输出是带 `report` 指针的单个有界 JSON。关键字段必须按下表复核：

| 字段 | 必须看到的值或含义 |
|---|---|
| `schema` | `networkagent-local-observability/1.0`；未知 schema 立即停止解读。 |
| `ok` / `run.status` | `true` / `PASS`；`run.error_code=null`、`run.error_class=NONE`、`run.event_count=22`。 |
| `source` | commit-bound RC 应为 `git_available=true`、`binding_stable=true`、`tracked_clean=true`、`commit_bound=true`，且 `commit_sha` 等于受测 SHA。 |
| `events` | 第 3 节的精确事件图；没有原始命令、路径、stdout、stderr 或环境。 |
| `business_outcomes.success` | `RESOLVED/PASSED`、`closed_loop=true`、`expected_business_result=true`。 |
| `business_outcomes.failure` | `REOPENED/FAILED`、`closed_loop=false`、`expected_business_result=true`。 |
| `business_outcomes.exact_retry` | success/failure 都为 `true`。 |
| `business_outcomes.cleanup` | success/failure 都为 `true`。 |
| `timing_snapshot` | `diagnostic_only=true`、`sample_count=1`；只用于本次诊断，不是 SLI/SLO。 |
| `metrics` | `name=networkagent_local_stage`；label keys 精确为 `branch/error_class/outcome/stage`，`high_cardinality_labels_present=false`。这是报告内聚合，不是 Prometheus。 |
| `local_alerts` | 四项固定规则各有 threshold、owner、runbook anchor；成功运行均为 `OK`。这是报告内求值，不是外部通知。 |
| `correlation` | 以 `observation_id`、`source_commit`、`defense_report_sha256` 关联本次证据；`propagated_trace=false`。 |
| `privacy` | status 为 `PASS`，六个泄漏/记录标志均为 `false`。 |
| `report.relative_path` | 本次保留的 `local-observability-report.json` 相对仓库根目录路径。 |
| `report.sha256` | 保留报告原始 UTF-8 字节的 64 位小写 SHA-256。 |

`report` 指针只加入标准输出；保留报告包含其余报告正文。不要把重定向后的 stdout
summary 摘要误当成保留报告摘要。

## 5. 覆盖声明

`coverage.delivered` 精确声明以下四项：

- `BOUNDED_LOCAL_STAGE_EVENTS`
- `LOCAL_TIMING_SNAPSHOT`
- `STABLE_LOCAL_ERROR_CLASSIFICATION`
- `IN_REPORT_LOCAL_ALERT_EVALUATION`

`coverage.not_claimed` 精确声明以下八项未交付能力：

- `OPEN_TELEMETRY_EXPORT`
- `CROSS_HTTP_REPLAY_A2A_MCP_TRACE`
- `PROMETHEUS_METRICS`
- `EXTERNAL_ALERT_DELIVERY`
- `SERVICE_LEVEL_OBJECTIVES`
- `COLLECTOR_FAILURE_TOLERANCE`
- `GATE_E_OR_G5_CLOSURE`
- `CLOUD_OR_PRODUCTION_OBSERVABILITY`

缺少任一限制项都应按契约漂移处理，不能通过口头说明补齐。

## 6. 报告与 SHA-256 核验

从标准输出复制 `report.relative_path`，不要凭时间或随机令牌猜测目录。在 PowerShell 中
核验：

```text
(Get-FileHash -Algorithm SHA256 -LiteralPath '<report.relative_path>').Hash.ToLowerInvariant()
```

Linux/macOS 使用 `sha256sum '<report.relative_path>'`。结果必须逐字符等于标准输出的
`report.sha256`。只要 source commit 不匹配、`commit_bound=false`、事件图或业务结果不符、
任一 workspace 未清理、任一告警不是预期状态，或摘要不符，本次就不能作为
commit-bound S4-01 证据。

## 7. 远程 RC 与发布制品

S4-01 的受测源码是 RC `cb4a4e7191f67aa71ef980668352d55001e23142`。本次证据回填
提交晚于且不等于该 RC。

| 证据 | 已确认结果 | 边界 |
|---|---|---|
| [Local run 33330915665](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665) | Python 3.12 [job 99309192438](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665/job/99309192438) 与 Python 3.13 [job 99309192337](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665/job/99309192337) 均成功；每版 Domain + Local `518 passed`、local-stack `66 passed, 2 skipped`、Local E2E `2 passed`；3.12 release boundary `18 tests passed`。 | 两版都运行并严格核对可观测包装器；3.13 不上传重复 release artifact。 |
| [VERIFIED RC artifact 9737683310](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665/artifacts/9737683310) | 名称 `telco-local-release-py3.12-attempt-1`，106,309 bytes，archive digest `sha256:5b26c3ccaff8e57c10c4bb3375ebbad8156d151e26158b1123639e3423006eca`，到期时间 `2026-09-13T19:33:25Z`。 | 独立下载为 11 个文件：manifest 记录 10 个文件，加 manifest 自身；闭包无额外、缺失或摘要漂移。 |
| Defense supplemental evidence | `release-evidence/defense-demo-summary.json` 为 3,379 bytes，SHA-256 `14f04bf556f03fd7c22edf0272240dba566610466546362442abdab3dd06a9b7`。 | 继续证明原生双分支、exact retry、双 cleanup 和 source binding。 |
| Observability supplemental evidence | `release-evidence/local-observability-summary.json` 为 9,178 bytes，SHA-256 `2741c3a25983056a73ea0bcd6ea99ffc14bf83dbd6209e4a9811b93c0a98df49`。 | 证明本手册冻结的事件、诊断、报告内指标/告警、隐私和限制契约。 |

本 RC 只有 Local workflow 因路径规则触发；Data Lab 与 Assurance workflows 没有在该
SHA 上运行。既有 Lab/Assurance 全绿结果只保留为其各自历史 RC 证据，不得改写为本次
同 SHA 回归。

## Execution failure

- 报告规则：`LOCAL_EXECUTION_FAILURE`，owner 为 `networkagent-local-owner`，threshold 为
  `execution_error_count > 0`。
- `ALERT` 表示 doctor 或固定子命令未按冻结契约完成。保存 stderr 的稳定错误 JSON 和
  已生成的 report 指针；不要添加参数、修改数据库或绕过固定命令。
- 核对 Python/依赖、仓库根目录和 source binding 后，以第 2 节唯一命令重新执行。若同一
  RC 重复失败，停止答辩证据认定并交由开发者复核。

## Cleanup failure

- 报告规则：`LOCAL_CLEANUP_FAILURE`，owner 为 `networkagent-local-owner`，threshold 为
  `cleanup_error_count > 0`。
- 不要对 `.local` 使用递归删除。先从报告指针确认本次精确 run 目录，只对仍存在且确为
  marker-owned 的分支工作区运行受保护 reset：

```text
python tools/local-stack/local_stack.py --workspace '<exact-run-directory>/success' reset --yes
python tools/local-stack/local_stack.py --workspace '<exact-run-directory>/failure' reset --yes
```

- reset 拒绝仓库根、home、文件系统根、未标记目录和链接/reparse 路径。任何 workspace
  未确认清理前，本次证据无效。

## Retry amplification

- 报告规则：`LOCAL_RETRY_AMPLIFICATION`，owner 为 `networkagent-local-owner`，threshold
  为 `exact_retry_proof != complete`。
- success/failure 两分支的 `approval_execute` 和 `terminal_verify` 都必须精确出现 attempt
  1 与 2，且 `business_outcomes.exact_retry` 两项都为 `true`。少一次、多一次或业务记录
  放大均判为 `ALERT`。
- 不得以删事件、改计数或重复运行后挑选结果的方式关闭告警；保留失败报告并复核原审批
  请求重放和终态不变契约。

## Contract drift

- 报告规则：`LOCAL_CONTRACT_DRIFT`，owner 为 `networkagent-local-owner`，threshold 为
  `contract_or_observation_error_count > 0`。
- 未知 schema、事件图/字段变化、非法 label、缺失限制声明、source/report 绑定漂移或观测
  器自身违规均使本次结果无效。
- 不要放宽解析、删除失败字段或把未知版本按 1.0 解读。保存报告，比较受测 RC 与冻结
  schema；只有经评审的新版本契约和新 RC 才能解除漂移。

## 8. 其他失败语义

| 错误码 | 分类 | 处理 |
|---|---|---|
| `confirmation_required` / `invalid_arguments` | `INPUT` | 只使用第 2 节固定命令。 |
| `command_failed` | `EXECUTION` | 按 `Execution failure` 处理。 |
| `evidence_contract_failed` | `CONTRACT` | 按 `Contract drift` 处理。 |
| `cleanup_failed` | `CLEANUP` | 按 `Cleanup failure` 处理。 |
| `report_write_failed` | `ARTIFACT` | 检查 `.local` 权限和链接/reparse 状态，不要绕过安全写入。 |
| `observation_contract_failed` | `OBSERVATION` | 报告无效，按 `Contract drift` 处理。 |

成功时进程以状态 0 在 stdout 输出一个 JSON；失败时以状态 2 在 stderr 输出一个固定、
不反射敏感输入的 JSON。业务或观测失败时若报告已经安全写入，stderr 会带 report 指针；
报告自身无法安全写入时不保证存在该指针。包装器先让原生演示尝试安全清理，再形成最终
观测结论。

## 9. 限制声明

- S4-01 只证明本地答辩进程内的观测证据契约，不是完整可观测平台。
- `observation_id`、source commit 与 defense report SHA 不是跨服务 trace context；
  `propagated_trace=false`。
- timing 只有一个诊断样本；metrics 只存在于 JSON 报告；alerts 只在报告内求值，没有
  Prometheus、外部通知、自动恢复证明或 SLO。
- 没有 OpenTelemetry Collector/Tempo/Jaeger/Grafana，也没有 Collector 故障不阻断业务
  的演练证据。
- S4、Workflow E 和 S7 继续 `IN PROGRESS`；Gate E 与 G5 保持开放。
- S2-04 继续 `BLOCKED`，G2/G4 保持开放。
- 本命令不启动容器、不读取 Cloud/GCP 凭据、不执行真实网络动作，不代表 Cloud Staging、
  生产、真实回滚、备份/恢复或外部值班能力已验收。
