# 实施开发计划 2.0

> 文档状态：Active
> 计划版本：2.0
> 建立日期：2026-08-30
> 最近更新：2026-08-31
> 当前执行阶段：S4 — 可观测与运维（`IN PROGRESS`；S4-01、S4-02、S4-03、S4-04、S4-05 窄切片 `DONE`，S2-04 仍为 `BLOCKED`）
> 适用仓库：`NetworkAgent-dev`
> 关系说明：本计划承接《统一智能运维平台实施计划》，作为下一轮发布工程和开发验收的执行基线；历史阶段结论仍以原 Gate 文档和远程 CI 证据为准。

## 1. 计划目的

本计划以“先形成可独立复核的本地答辩版本，再进入真实 Cloud Staging”为主线。当前没有 GCP 服务账号不是停止开发的理由，但必须成为明确的授权边界：本地开发不得读取、推断或要求真实 GCP 凭据，不得把 Emulator、静态检查或模拟动作描述为真实 Cloud 验收。

2.0 阶段的目标是：

1. 将现有命令行治理闭环提升为有严格边界的本地 HTTP 服务。
2. 将 BubbleRAN `ReplayPlan` 接入仅允许 loopback 的真实本地传输层，并进入 Canonical Fault/Incident 治理入口。
3. 形成可重复构建、可扫描、可签名、可容器化的一键本地演示版本。
4. 建立结构化日志、指标、追踪、SLO、告警和 Runbook 的本地证据。
5. 提前完成 Cloud Staging 的 IaC、权限矩阵和验收脚本设计；在获得真实身份后只执行必须依赖 Cloud 的最后 Gate。

## 2. 状态词汇与维护规则

计划只使用以下状态：

| 状态 | 含义 |
|---|---|
| `NOT STARTED` | 尚未开始，且没有可验收实现。 |
| `IN PROGRESS` | 已开始实施，但至少一个必需 Gate 尚未通过。 |
| `READY FOR REVIEW` | 实现与本地证据齐全，等待独立复核。 |
| `READY FOR STAGING` | 无 Cloud 凭据范围内的实现、静态策略与验收包已通过，等待真实 Staging 身份执行行为 Gate。 |
| `DONE` | 代码、测试、文档、制品和适用 Gate 均已通过。 |
| `WAITING FOR CLOUD` | 本地准备工作可继续，但最终结论必须依赖真实 GCP 项目或身份。 |
| `BLOCKED` | 存在已确认且无法在当前授权范围内消除的阻断。 |

维护规则：

- 只有在 DoD 全部满足并附有可追溯证据后，才能将工作项标为 `DONE`。
- Emulator 通过只能证明协议、DDL 或事务逻辑，不得替代真实 IAM、OIDC、DLQ、配额、网络或区域行为。
- 每个 Sprint 结束时更新状态、实际测试计数、制品摘要、残余风险和变更日志。
- 状态变化必须绑定 commit SHA、CI run URL 或本地证据摘要；不使用“基本完成”“应该可用”等不可复核描述。
- 新接口先更新“接口冻结”章节，再实施代码；破坏性变更必须提升契约版本并提供迁移说明。

## 3. 当前基线

### 3.1 当前能力基线

| 能力 | 状态 | 当前证据与边界 |
|---|---|---|
| P1 Canonical Domain | `DONE` | 统一 Incident、状态机、证据、RCA、审批、动作、验证和审计契约；历史双 Pydantic 与远程 CI Gate 已通过。 |
| P2a Local Profile | `DONE` | DuckDB、本地 LTE 数据适配、Detector、规则 RCA、显式 Incident 确认和安全 CLI 已完成。 |
| P2b Assurance/A2A | `DONE` | 本机限定 A2A、持久 challenge/task、Supervisor 结构化桥接及真实 HTTP 契约测试已完成。 |
| P3a–P3d Cloud 代码与 Emulator | `DONE` | Spanner v2、事务型 Inbox/Outbox、Fault Ingress、只读 MCP、FGAC 制品和迁移逻辑已通过远程 Spanner Emulator Gate；不代表 Cloud Staging 已通过。 |
| Local 模拟治理闭环 | `DONE` | 13,440 条 KPI、579 条安全 Trace、15 个候选；独立审批后可到 `RESOLVED`，验证失败到 `REOPENED`，无真实网络副作用。 |
| Local Stack | `DONE` | `doctor/init/status/demo/serve/reset` 已具备工作区所有权、loopback、默认禁用动作、安全 reset 与提交响应丢失恢复。 |
| S7-01 原生一键答辩闭环 | `DONE` | RC `c08d634c9c3deb628df5f98d4f60dd1675cd5706` 的远程 Python 3.12/3.13 Local jobs 均运行固定命令并核对 `commit_bound=true`、`commit_sha=GITHUB_SHA`、`RESOLVED/PASSED`、`REOPENED/FAILED` 与双安全清理。 |
| S7-02 运行手册与证据包 | `DONE` | RC `79feeee6771749bbdd1ce7ce44b77193a1db544f` 的远程双 Python 演示、release boundary、同 SHA Lab/Assurance 回归及独立下载闭包均通过；VERIFIED RC artifact 9736785325 将 `defense-demo-summary.json` 作为 manifest-verified supplemental evidence。 |
| S4-01 本地答辩可观测证据 | `DONE` | RC `cb4a4e7191f67aa71ef980668352d55001e23142` 的远程双 Python Local jobs 均核对 22 个有界阶段事件、诊断时序、低基数报告内指标、四项报告内告警、隐私边界和双分支业务结果；Python 3.12 VERIFIED RC artifact 9737683310 的两个 supplemental evidence 均经 manifest 与独立下载闭包复核。该窄切片不关闭 S4、Workflow E、Gate E 或 G5。 |
| S4-02 Canonical 生命周期安全投影 | `DONE` | RC `69643e8a6f79b1264d60e5517eeb9a24035c8e7d` 的远程双 Python Local jobs 均核对双分支各 8 个 revision group / 14 个 allowlisted 事件、只读性、精确绑定、exact retry、双清理和零副作用；Python 3.12 VERIFIED RC artifact 9739212391 的第三个 supplemental evidence 已经 manifest 与独立下载闭包复核。该窄切片不关闭 S4、Workflow E、P7、S7、Gate E/G5 或 G2/G4。 |
| S4-03 固定三窗口 Local acceptance SLI/SLO 证据 | `DONE` | RC `faa11ff7a165cd5eae6cf3f0fa1a030c9472f46c` 的远程双 Python Local jobs 各执行一个全新三窗口组；五项整数 ppm SLI 均达到 1,000,000 ppm、零错误预算，evaluation 为 `OK` 且无 breach。Python 3.12 VERIFIED RC artifact 9740377450 的第四个 supplemental evidence 已经 manifest 与独立下载闭包复核。该窄切片不是时间型可用性、延迟或长期统计可靠性 SLO，也不关闭 S4/Workflow E/P7/S7、Gate E/G5 或 G2/G4。 |
| S4-04 Local DuckDB 冷备恢复证据 | `DONE` | RC `54551feb43be60c3b9bdd5eab076cdb7c0aba61a` 的双 Python Local jobs 均验证 stopped-writer、两文件 checkpointed cold backup、manifest/逻辑指纹、损坏副本拒绝且 fresh database 零改、首次恢复 `changed=true`、精确重试 `changed=false`、生命周期等价及身份绑定清理；Python 3.12 VERIFIED RC artifact 9744736851 的第五个 supplemental evidence 已通过 14 文件 manifest 与独立下载闭包复核。该窄切片不声明在线/异地/加密签名/跨版本/多副本/Cloud/生产恢复、RPO/RTO、断电耐久，也不关闭 S4/Workflow E/P7/S7、Gate E/G5 或 G2/G4。 |
| S4-05 Local 单进程运行时 Trace 贯通证据 | `DONE` | corrective RC `2e59d7ca88cc550e315d63e80339909ef619cd2c` 的 Assurance/Local/Container 三个 workflows 全绿；固定命令把一个 BubbleRAN 事件经真实 loopback Replay、durable DuckDB readback 与 A2A Analyze 串成 6 事件/4 组件/6 bindings。Analyze 只改变 `assurance_a2a_tasks`，Canonical domain 与其余 9 表不变，治理四类记录 `0 -> 0`；Assurance VERIFIED RC artifact 9747354240 已通过 12 文件闭包与报告重建复核，raw JSONL 未上传。该窄切片不是 OTel/Prometheus、distributed/cross-process/multi-event、MCP、外部告警、Cloud/生产或 full-database read-only 证据，也不关闭 S4/Workflow E/P7/S7、Gate E/G5 或 G2/G4。 |
| S2-02 容器化治理恢复 | `DONE` | 精确绑定 RC 的远程真实 Docker 已覆盖 `RESOLVED`/`REOPENED` 两分支、Assurance 重启、原请求 exact replay、离线数据库核验和项目卷清理；真实网络副作用为 0。 |
| BubbleRAN Data Lab | `IN PROGRESS` | 下载锁定、隐私投影、离线评估、immutable `ReplayPlan`、公开 `ReplayWirePayload`、loopback transport、单调 paced runner、caller-owned 本地持久 checkpoint 与 Assurance Canonical Fault 持久接收器已完成；每个 source event 独立映射 Incident，不做跨事件聚合，RCAEval 尚未完成。 |

### 3.2 当前发布证据

- Sprint 1 的受测 release candidate 为 `7cbff490ccb71befb42c7cd30204f7f88e3b2f38`。以下三个 run 均为 `success`，且各自 `headSha` 精确等于该 SHA：
  - [Assurance CI run 33308634938](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634938)：4 个 job 全绿；本地对应 Gate C/D 回归为 Assurance `76 passed`、A2A contracts `33 passed`、A2A E2E `4 passed`。
  - [Data Lab CI run 33308635073](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308635073)：3 个 job 全绿；本地双 Pydantic 的 Data Lab + Lab E2E 各为 `222 passed, 1 skipped`。
  - [Local CI run 33308634955](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634955)：2 个 job 全绿；本地对应回归为 local-stack `22 passed`、Local E2E `3 passed`。
- 三个 Python 3.12 release job 均发布了绑定该 RC、保留 14 天的 `VERIFIED RC` artifact：
  - [Assurance artifact 9731341117](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634938/artifacts/9731341117)：archive digest `sha256:30cee4d4ca7c8e7d09cdde27449a8165a5e1da3e16efa8dc0fc30c4af44d454e`；runtime inventory 34 项、`pip-audit` 0 个已知漏洞、SBOM 38 components。
  - [Data Lab artifact 9731281738](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308635073/artifacts/9731281738)：archive digest `sha256:2e314321e990f38ef82696a6df78fe9f11538f6c582996004d4b66d2d11a2231`；runtime inventory 5 项、`pip-audit` 0 个已知漏洞、SBOM 7 components。
  - [Local artifact 9731294281](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634955/artifacts/9731294281)：archive digest `sha256:adee5fba5887a4d61a4f59fba9a946c8d211038144095918e3045a6f56b0bee0`；runtime inventory 7 项、`pip-audit` 0 个已知漏洞、SBOM 9 components。
- 当前远程 canonical wheel 为：`telco_domain-0.1.0` 32,547 bytes / SHA-256 `53f0b118041c5897d4e01813b777744263f849894f6c211cc67cb9df41fd104e`；`telco_lab-0.1.0` 74,425 bytes / `4c646e7ad618884284bf5f0b484b579c19dbcaccc8ef01571eccfc4ea197d900`；`telco_local-0.1.0` 66,728 bytes / `f86b66dbd9a157ca0ecbdb0fb1d63743f48fc96195a12629e01780f809dd7e3f`；`telco_assurance_agent-0.1.0` 56,893 bytes / `9f7d47ea0c45d2a01a60a5a726055a7368f3d2cf86d4d8a8ac1445bde08ce96d`。Domain/Lab/Local 摘要相对上一 RC 未变；Assurance 摘要随 HTTP hardening 更新。
- 上一 RC 的 [Cloud CI run 33301104595](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104595) 仍是有效的 Cloud/Spanner Emulator 历史证据，但其 `headSha` 不是本节的新 RC，不能据此宣称新 RC 已完成 Cloud 回归，更不能替代 Cloud Staging IAM/OIDC/DLQ/Workload Identity 验收。
- Sprint 2 S2-01 的受测 release candidate 为 `d0a020fb7a5d8a33cd136cd18917d21b7e067946`；[telco-container run 33311995755](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755) 的 `headSha` 精确绑定该 SHA，[compose-policy job 99258612862](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755/job/99258612862) 与 [build-inspect-smoke job 99258640065](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755/job/99258640065) 均为 `success`。远程 Linux 政策门禁为 `76 passed, 0 skipped`；真实 Compose resolve/build/inspect 得到 runner 本地 image ID `sha256:0acef50a2ee7978ea67a8b37582a19698a21b1303451ce37a0a569d48fef6cff`（不是 registry digest），并完成 5 个应用层 / 2,570 个成员、9,148 个合并 rootfs 成员扫描；初始化为 13,440 条 performance / 579 条 trace / 0 条 incident 且 `external_access=false`。health、运行中隔离、共享 loopback smoke 和 probe step 均成功，但 probe step 无 stdout；reset 删除 state/artifacts/marker 且 `workspace_removed=true`，cleanup 成功。
- Sprint 2 S2-02 的受测 release candidate 为 `d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4`；[telco-container run 33314782750](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750) 的 `headSha` 精确绑定该 SHA，[compose-policy job 99266075811](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750/job/99266075811) 与 [build-inspect-smoke job 99266104885](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750/job/99266104885) 均为 `success`，Linux 政策门禁为 `128 passed`。真实容器治理 JSON 中成功分支为 `RESOLVED`、故意验证失败分支为 `REOPENED`；两分支均为 `restart_observed=true`、`exact_replay=true`、`real_network_side_effects=false`，顶层 `projects_removed=true` 证明两个 Compose 项目均已清理。
- 同一 RC 的 [Local CI run 33314782757](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757) 两个 job [99266075954](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/job/99266075954) / [99266075805](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/job/99266075805) 均为 `success`。两版 Python 各通过 Domain + Local `518 passed`、local-stack `29 passed, 2 skipped`、Local E2E `2 passed`；Python 3.12 发布 [VERIFIED RC artifact 9733117877](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/artifacts/9733117877)，archive digest 为 `sha256:3d557a52a80960add94c04b443c14f613892701c5b5b93dcfba4174fd78f3469`。
- Sprint 2 S2-03 的受测 release candidate 为 `68b16ea528a85b743aa8c05044948bac195ee8ec`；[telco-container run 33320667296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296) 的 `headSha` 精确绑定该 SHA，[compose-policy job 99281949020](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/job/99281949020) 与 [build-inspect-smoke job 99281979960](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/job/99281979960) 均为 `success`。该 run 发布了保留 14 天的 [artifact 9734817516](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/artifacts/9734817516)，名称 `telco-container-release-attempt-1`，archive digest 为 `sha256:e35d8eb12484feeb474477bae0f3d937019f3ab19e9f8ccf1fd491b8a95f0394`，分类为 `VERIFIED RUNNER-LOCAL EVIDENCE`；其绑定 runner 本地 image/config digest `sha256:0e8caa8418d93e1f9654655b84331723e8223d9dc94e66274aed1ca3fa7d00bb`、Trivy 0.74.0 fixable Critical/High gate `0/0`、full diagnostic `5` Critical + `29` High 且全部 unfixed，以及 CycloneDX 1.7 SBOM `145` components。
- S7-01 的受测 release candidate 为 `c08d634c9c3deb628df5f98d4f60dd1675cd5706`。[Local run 33326721937](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33326721937) 的 Python 3.12/3.13 jobs [99298066127](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33326721937/job/99298066127) / [99298066217](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33326721937/job/99298066217) 均为 `success`：两版各通过 Domain + Local `518 passed`、local-stack `47 passed, 2 skipped`、Local E2E `2 passed`，并直接运行一键脚本，核对 `commit_bound=true`、`commit_sha=GITHUB_SHA`、`RESOLVED/PASSED`、`REOPENED/FAILED` 与双 `workspace_removed=true`。[Data Lab run 33326721947](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33326721947) 和 [Assurance run 33326721991](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33326721991) 也绑定同一 SHA 且全绿。
- 同一 Local run 的 Python 3.12 job 发布 S7-01 历史 [artifact 9736486858](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33326721937/artifacts/9736486858)：102,753 bytes，archive digest `sha256:a1961b1897cdb86c802ce3dbd9762381ef7726e28476a1d24657162014b330f2`，到期时间 `2026-09-13T18:02:15Z`。该制品不含演示 JSON，只作为 S7-01 历史证据，不能替代 S7-02 证据包。
- S7-02 的受测 release candidate 为 `79feeee6771749bbdd1ce7ce44b77193a1db544f`。[Local run 33327786238](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786238) 的 Python 3.12/3.13 jobs [99300888630](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786238/job/99300888630) / [99300888747](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786238/job/99300888747) 均为 `success`：两版各通过 Domain + Local `518 passed`、local-stack `49 passed, 2 skipped`、Local E2E `2 passed`，一键脚本的 source binding、双终态、exact retry 与双 cleanup 均通过；Python 3.12 release boundary 额外为 `18 tests passed`。
- 同一 RC 的 [Data Lab run 33327786237](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786237) jobs 99300888644/99300888752/99300888782 全绿，适用矩阵为 `220 passed, 3 skipped`；[Assurance run 33327786211](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786211) jobs 99300888597/99300888619/99300888628/99300888662 全绿，两个主 job 各为 `854 passed, 3 skipped`，Supervisor 为 `57 passed`。
- Local Python 3.12 job 发布保留 14 天的 [VERIFIED RC artifact 9736785325](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786238/artifacts/9736785325)，名称 `telco-local-release-py3.12-attempt-1`，104,109 bytes，archive digest `sha256:b4b6f6ab762695a367169d54078ab1f6d2ec64c4ef3c21c132190421ed31cff3`，到期时间 `2026-09-13T18:24:17Z`。独立下载得到 10 个文件：release manifest 精确记录其余 9 个，闭包无额外、缺失或摘要漂移；`release-evidence/defense-demo-summary.json` 为 3,379 bytes / SHA-256 `ae0b412a42d9430a35117dd9e8987662c7359cc95ea72a076fa2f869bcaa51ef`，其中 `report.sha256` 为 `a91676e52789d5c520d3cb3e2e8b0a47d19d7801f5bebbb51f3f10ffa613bc5f`。独立复核确认双终态、exact retry、双 cleanup 与 source binding 全部匹配冻结契约。
- 本次 S7-02 证据文档提交晚于且不等于受测 RC `79feeee6771749bbdd1ce7ce44b77193a1db544f`；远程结论只绑定上述 RC、runs、jobs 和 artifact，不能把文档提交本身写成受测源码。
- S4-01 的受测 release candidate 为 `cb4a4e7191f67aa71ef980668352d55001e23142`。[Local run 33330915665](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665) 的 Python 3.12/3.13 jobs [99309192438](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665/job/99309192438) / [99309192337](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665/job/99309192337) 均为 `success`：两版各通过 Domain + Local `518 passed`、local-stack `66 passed, 2 skipped`、Local E2E `2 passed`；Python 3.12 release boundary 另为 `18 tests passed`，Python 3.13 不发布重复 artifact。两版均运行固定可观测包装器，核对 22 个有界阶段事件、双终态、exact retry、双 cleanup、四项报告内告警为 `OK`、低基数 labels、隐私字段和 `propagated_trace=false`。
- 同一 Local run 的 Python 3.12 job 发布 [VERIFIED RC artifact 9737683310](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665/artifacts/9737683310)，名称 `telco-local-release-py3.12-attempt-1`，106,309 bytes，archive digest `sha256:5b26c3ccaff8e57c10c4bb3375ebbad8156d151e26158b1123639e3423006eca`，到期时间 `2026-09-13T19:33:25Z`。独立下载得到 11 个文件：manifest 精确记录其余 10 个，闭包无额外、缺失或摘要漂移；`defense-demo-summary.json` 为 3,379 bytes / SHA-256 `14f04bf556f03fd7c22edf0272240dba566610466546362442abdab3dd06a9b7`，`local-observability-summary.json` 为 9,178 bytes / SHA-256 `2741c3a25983056a73ea0bcd6ea99ffc14bf83dbd6209e4a9811b93c0a98df49`。
- 本 RC 仅由 Local workflow 的路径规则触发；Data Lab 与 Assurance workflows 没有在 `cb4a4e7191f67aa71ef980668352d55001e23142` 上运行。既有 Lab/Assurance 全绿记录仍是各自历史 RC 证据，不得写成 S4-01 同 SHA 回归。本次 S4-01 证据回填提交也晚于且不等于该受测 RC。
- S4-02 的受测 release candidate 为 `69643e8a6f79b1264d60e5517eeb9a24035c8e7d`。[Local run 33336341831](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831) 的 Python 3.12/3.13 jobs [99323794962](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/job/99323794962) / [99323795037](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/job/99323795037) 均为 `success`：两版各通过 Domain + Local `576 passed`、local-stack `89 passed, 2 skipped`、Local E2E `2 passed`；Python 3.12 release boundary 为 `18 tests passed`。固定 lifecycle wrapper 对成功/失败分支各核对 8 个连续 revision group、14 个唯一事件、只读投影、精确绑定、单次执行、exact retry、双 cleanup、`side_effects=false` 与 `distributed_trace=false`。
- 同一 Local run 的 Python 3.12 job 发布 [VERIFIED RC artifact 9739212391](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/artifacts/9739212391)，名称 `telco-local-release-py3.12-attempt-1`，115,482 bytes，archive digest `sha256:237bcb207e29e53aba6c907f94efcb1999b77b1640f59dd95b8d3c9c30b27aa7`，到期时间 `2026-09-13T21:30:29Z`。独立下载得到 12 个文件：manifest 精确记录其余 11 个，闭包无额外、缺失或摘要漂移；`local-lifecycle-summary.json` 为 8,431 bytes / SHA-256 `5726b4eec6f0c4621a3d804b0f6973a24d41ae14ea1878b2e57205a299966e45`，去除 stdout-only `report` envelope 后重建的持久报告为 8,290 bytes / SHA-256 `21528fd8694da0cb0c51452b14c45af864b24951282475367addbcd1b74fa004`。
- 同一 SHA 的 [Assurance run 33336341877](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341877) jobs 99323794957/99323795038/99323795061/99323795122、[Container run 33336341805](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341805) jobs 99323794954/99323831112 和 [Cloud run 33336341859](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341859) jobs 99323794980/99323795082/99323795085/99323795200 均全绿；Data Lab 未触发。Cloud workflow 只提供 CI/Emulator 证据，不代表 Cloud Staging 或生产已验收。
- 本次 S4-02 证据文档提交晚于且不等于受测 RC `69643e8a6f79b1264d60e5517eeb9a24035c8e7d`；远程结论只绑定上述 SHA、runs、jobs 和 artifact。
- S4-03 的受测 release candidate 为 `faa11ff7a165cd5eae6cf3f0fa1a030c9472f46c`。[Local run 33340008133](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133) 总耗时 11m40s，仅该 workflow 被路径规则触发。Python 3.12/3.13 jobs [99333812338](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133/job/99333812338) / [99333812397](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133/job/99333812397) 均为 `success`，分别耗时 10m51s / 11m36s；固定三窗口 SLO step 分别耗时 2m38s / 2m51s，3.13 的发布步骤按矩阵设计跳过。两版均核对一个全新三窗口组、66 个阶段事件，以及 `66/66`、`6/6`、`6/6`、`6/6`、`3/3` 五项 SLI；每项 observed/objective 均为 1,000,000 ppm、error budget 为 0、状态为 `OK`，evaluation 为 `OK` 且无 breach，privacy 为 `PASS`。
- Python 3.12 发布保留 14 天的 [VERIFIED RC artifact 9740377450](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133/artifacts/9740377450)，名称 `telco-local-release-py3.12-attempt-1`，117,046 bytes，archive digest 为 `sha256:11207c784de25ec1d6d956bb8b47274663100455a6924ccf95213c839c848536`。独立下载得到 13 个文件：12 条 manifest 记录加 manifest 自身，闭包、全部 bytes/SHA 精确匹配，manifest 为 `PASS` 且 `failures=[]`。`local-slo-summary.json` 为 3,271 bytes / SHA-256 `ae181eaffe6da11c5dd0cdea07dcfcba3a400daaf6ed44352b1e573faa5f489b`；去除 stdout-only `report` envelope 后重建的 `local-slo-report.json` 为 3,136 bytes / SHA-256 `2538629be3133920e76f2de9e0fa0ff9575853095538c266efc6e544d02c5c64`。schema、`LOCAL_DEMO_ACCEPTANCE_SLO_EVIDENCE` classification、source SHA、三个窗口、五项 SLI、evaluation、privacy 和 12 项 `not_claimed` 均匹配冻结契约。
- 本次 S4-03 证据文档提交晚于且不等于受测 RC `faa11ff7a165cd5eae6cf3f0fa1a030c9472f46c`；远程结论只绑定上述 SHA、Local run、两个 jobs 和 artifact，不能外推同 SHA 的 Data Lab、Assurance、Container 或 Cloud 结果。
- S4-04 的受测 release candidate 为 `54551feb43be60c3b9bdd5eab076cdb7c0aba61a`。[Local run 33353994792](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792) 总耗时 13m04s；Python 3.12/3.13 jobs [99372557281](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/job/99372557281) / [99372557192](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/job/99372557192) 均为 `success`，分别耗时 13m00s / 10m28s，固定 backup/recovery step 分别耗时 37s / 31s。两版各通过 Domain + Local `576 passed`、local-stack `224 passed, 3 skipped`、Local E2E `2 passed`，并核对 `backup_mode=COLD_OFFLINE`、`database_engine=DUCKDB`、`execution_mode=LOCAL_SINGLE_PROCESS`、`restore_target=RESET_FRESH_INITIALIZATION`、`writer_stopped=true`、10 项 proof、7 项 delivered、12 项 `not_claimed` 与 privacy `PASS`。
- 同一 SHA 的 [Container run 33353994784](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994784) 总耗时 2m07s，jobs [99372557334](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994784/job/99372557334) / [99372587413](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994784/job/99372587413) 全绿。该 RC 只触发 Local 与 Container 两个 workflows，不外推同 SHA Data Lab、Assurance 或 Cloud 结果。
- Python 3.12 发布保留 14 天的 [VERIFIED RC artifact 9744736851](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/artifacts/9744736851)，名称 `telco-local-release-py3.12-attempt-1`，118,251 bytes，archive digest `sha256:5ca975e95cd86befb77ca977a3acc2aa57122a0148202b945a3a5c50a3153fe1`，创建/到期时间分别为 `2026-08-31T03:42:18Z` / `2026-09-14T03:42:17Z`。独立下载得到 14 个非链接普通文件：13 条 manifest 记录加 manifest 自身，闭包、全部 bytes/SHA 精确匹配，6,847-byte manifest 的 SHA-256 为 `e13db5a8d326538e7c2aaea1d51f0ce8a71e557e4b0d366659d2873127d8d502`，状态为 `PASS` 且 `failures=[]`。第五个 supplemental evidence `local-backup-recovery-summary.json` 为 1,951 bytes / SHA-256 `f44187fece9d33b71b520521df188c6043cfdfe4e67618c71b96b5703828e7bb`；去除 stdout-only `report` envelope 后重建的持久报告为 1,804 bytes / SHA-256 `f6698b0846571a6af3a9cca7edd57f20e1204154fc09dbec3630e86fca784a96`。制品成员不含 `.duckdb` 文件、`backup-manifest.json` 或 `.local` 目录成员，证据 JSON 不含冷备原始路径及 ownership/device/inode 字段。
- 本次 S4-04 证据文档提交晚于且不等于受测 RC `54551feb43be60c3b9bdd5eab076cdb7c0aba61a`；远程结论只绑定上述 SHA、Local/Container runs、四个 jobs 和 Local artifact。
- S4-05 首个功能提交 `b0bcb8fa39c2971e2dd1c1910cde69d68cc97edc` 不是成功 RC：[Local run 33362166565](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362166565) 在收集误置于 Local profile 的 Assurance-owned trace tests 时失败。corrective commit `2e59d7ca88cc550e315d63e80339909ef619cd2c` 将这些测试迁入 Assurance profile 并移除重复 Local 收集；只有该 corrective commit 是 S4-05 的受测 RC。
- 同一 corrective RC 的 [Assurance run 33362806092](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092) 四个 jobs [99397345468](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/job/99397345468) / [99397345590](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/job/99397345590) / [99397345635](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/job/99397345635) / [99397345601](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/job/99397345601)、[Local run 33362806180](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806180) 两个 jobs [99397346249](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806180/job/99397346249) / [99397346041](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806180/job/99397346041)，以及 [Container run 33362806104](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806104) 两个 jobs [99397345678](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806104/job/99397345678) / [99397392344](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806104/job/99397392344) 均为 `success`。Assurance 3.12/3.13 jobs 均运行固定 trace wrapper；只有 3.12 发布一次 supplemental evidence。
- Python 3.12 Assurance 发布保留 14 天的 [VERIFIED RC artifact 9747354240](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/artifacts/9747354240)，名称 `telco-assurance-release-py3.12-attempt-1`，246,678 bytes，archive digest `sha256:f772dcae631cdde59483eaef6a28d1caee0b0b357d8eb5eb069e863747991fa4`，创建/到期时间为 `2026-08-31T06:10:45Z` / `2026-09-14T06:10:44Z`。独立下载得到 12 个非链接普通文件：11 条 manifest 记录加 manifest 自身，闭包与 bytes/SHA 精确匹配；`local-runtime-trace-summary.json` 去除 stdout-only `report` envelope 后重建持久报告为 1,651 bytes / SHA-256 `5932b0454c7d095b7864f7c50cd0e2a48a05e288dbb74fd83a1773aedcaea5e8`。制品不含 raw JSONL 或 DuckDB；Local artifact 不是 S4-05 supplemental evidence 载体。
- 本次 S4-05 证据文档提交晚于且不等于受测 corrective RC `2e59d7ca88cc550e315d63e80339909ef619cd2c`；远程结论只绑定上述 SHA、三个 runs、八个 jobs 和 Assurance artifact。
- 本次证据回填形成的文档提交晚于且不等于上述受测 RC。S2-01 与 S2-02 的 `telco-container` runs 上传 0 个 artifact，保留为历史行为证据；当前容器供应链状态以 S2-03 RC 为准。S2-03 的 runner-local artifact 不是 container registry artifact：未发布 registry image/digest，未提供签名、attestation、provenance 或 Trivy DB OCI digest/signature，且由于未上传镜像、scanner binary 与数据库，不能离线独立重验。Gate A、Gate B、G2、S3、G4 与独立 secret/SAST/license policy 仍未完成。

### 3.3 已知缺口

- Sprint 1 的 Gate C、Gate D、S1-01 至 S1-07 已完成独立本地复核并通过同一新 RC 的 Assurance、Data Lab 与 Local 远程矩阵；Sprint 1 已关闭，S2 安全容器与 Compose 发布入口已进入实施。
- `ReplaySink` 同时支持立即串行投递与单调节奏 runner；只有明确启用的瞬时 network/timeout 失败才执行有限次重试。可选持久 wrapper 在有效 202/204 后先原子保存严格 plan-bound checkpoint，再推进内存状态；store 为 caller-owned、单 writer，本地 continuation claim 仍不是接收端签名 ACK。
- `/local/v1/healthz`、`readyz`、`version` 已实现并冻结。`readyz` 只做一次 1 秒有界本地 Repository 读；依赖异常、超时或前一个超时 worker 仍未结束时固定返回 503，不启动第二个并行探针，也不代表 Cloud readiness。
- HTTP 首部/body deadline 是 ASGI event loop 上的协作式计时。Governance/Fault 仓储工作由专用 worker 隔离，但 legacy A2A SDK/store 的同步 DuckDB 调用仍可能阻塞该 loop；A2A 后台/流式任务生命周期也不受同步请求 admission lease 覆盖，因此不声明全局硬 wall-clock 隔离。
- BubbleRAN 事件已进入 Canonical Incident 治理链路，但当前是每 source 独立 Incident，不做跨事件聚合；RCA 仅识别受控 `5G_SA` BubbleRAN UL BLER 签名，不得外推生产。
- S2-01 已在精确绑定 RC `d0a020fb7a5d8a33cd136cd18917d21b7e067946` 的远程 Docker runner 上通过 Compose config、build/inspect、应用层与合并 rootfs 扫描、health、运行中隔离、共享 loopback smoke/probe、reset 和 cleanup，因此该工作包为 `DONE`；本机仍没有 Docker/actionlint，不能把远程结果改写为本机工具 PASS。
- S2-02 已在精确绑定 RC `d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4` 的远程 Docker runner 上通过成功/失败治理分支、Assurance 重启、exact replay、离线核验和项目卷清理，因此该工作包为 `DONE`；其 Local 双 Python 矩阵与 Python 3.12 `VERIFIED RC` artifact 也已通过同一 RC。
- 当前 Sprint 1/Local RC 已发布 wheel、manifest、CycloneDX SBOM、runtime inventory、内容扫描与 `pip-audit` 证据；S2-03 已补齐 runner-local 容器 release manifest、Trivy 双报告与 CycloneDX SBOM artifact，但仍未发布 registry image/digest，未提供签名、attestation、provenance 或 Trivy DB OCI digest/signature，且 artifact 不能离线独立重验。
- S2-04 已在相同 Trivy 0.74.0/数据库快照下完成四个候选镜像实扫；没有候选能同时满足当前 CPython 3.12/glibc/provenance 契约和完整 Critical/High `0/0`，因此按 `BLOCKED` 收口，G2/Gate B 继续开放。
- S7-01 原生一键答辩脚本已由精确绑定 RC `c08d634c9c3deb628df5f98d4f60dd1675cd5706` 的远程 Python 3.12/3.13 jobs 验收，因此为 `DONE`；其范围仍不覆盖拒绝/过期、容器执行、真实动作、Cloud、G2/G4/G5。
- S7-02 已由精确绑定 RC `79feeee6771749bbdd1ce7ce44b77193a1db544f` 的双 Python Local jobs、同 SHA Lab/Assurance 回归、manifest verify 与独立下载闭包验收，因此为 `DONE`；S7-01 历史 artifact 9736486858 仍明确不含演示 JSON。
- S4-01 已交付本地答辩进程内的有界阶段事件、诊断性单次时序快照、低基数报告内指标聚合、四项报告内固定告警求值和窄运行手册。跨 HTTP/Replay/A2A/MCP/Repository 的结构化日志与分布式追踪、OpenTelemetry export/Collector、Prometheus、外部告警投递、运行态时间型可用性/延迟型/长期统计可靠性 SLI/SLO、Collector 故障容忍和完整故障演练仍未交付；579 条安全 Trace rows 是输入数据记录，不是 OpenTelemetry span。
- S4-02 已交付从完整持久 Canonical 记录派生的只读、revision-grouped 双终态生命周期投影与第三个 release supplemental evidence；每分支 8 组/14 事件，投影隐去 domain/workspace 标识与哈希、路径、correlation 和原始业务内容。它明确 `distributed_trace=false`，不能替代运行时结构化日志、OpenTelemetry/Prometheus、跨组件追踪、SLO、外部告警或 Cloud 生产证据。
- S4-03 已交付一个严格固定的三窗口 Local acceptance sample、五项整数 ppm SLI、零错误预算、报告内 breach 求值和第四个 release supplemental evidence。它不提供时间型可用性、延迟、长期统计可靠性或 Cloud/production SLO，不提供 runtime structured logs、OpenTelemetry/Prometheus 或外部告警；新 `OK` 报告是新鲜评估，不是自动恢复。
- S4-04 已交付 stopped-writer、Local single-process DuckDB 的 checkpointed 两文件冷备、严格 manifest/逻辑指纹核验、损坏副本零改拒绝、reset/fresh-init 后原子恢复、精确重试及安全清理，并将第五个 supplemental evidence 绑定 release manifest。它不是在线/生产 HA、多副本 failover、RPO/RTO、断电耐久、加密/签名、异地/远端、跨版本、Cloud/Spanner 或生产恢复；未知身份或 raced residue 必须保留人工检查，不自动清理。
- S4-05 已交付一个 fixed BubbleRAN event 的真实 loopback Replay -> durable readback -> A2A Analyze 单进程相关性：exact derived header、6 个顺序事件、4 个组件、6 项 binding、`assurance_a2a_tasks` 唯一表变化、其余 9 表/Canonical domain 不变以及治理四类 `0 -> 0`。raw JSONL 只保留本地、不进入 artifact；该结果不是 OTel/Prometheus、distributed/cross-process/multi-event trace、MCP propagation、external alert、Cloud/production observability、sink delivery guarantee 或 full-database read-only Analyze。
- Cloud Staging IAM/OIDC、Pub/Sub DLQ、Workload Identity 及真实基础设施验收尚未进行。

## 4. 无 GCP 开发原则

1. **零凭据依赖**：Local Profile 的导入、启动、测试和演示不得要求 GCP 项目、服务账号、ADC、`gcloud` 登录或模型厂商凭据。
2. **默认无副作用**：动作模式默认 `disabled`；本地只允许 `simulate`，所有真实动作枚举值、端点或环境配置必须失败关闭。
3. **仅 loopback 暴露**：HTTP 服务和 Replay transport 只允许显式 `127.0.0.1`/`::1`，禁止通配监听、非 loopback 重定向、代理继承和 URL 用户信息。
4. **同一领域契约**：Local 与 Cloud 共用 Canonical 模型、状态机、幂等和审计语义；不得创建第二套 Incident 生命周期。
5. **证据与结论分级**：本地、Emulator、静态 IaC 和真实 Cloud 的证据必须分别标记，低级证据不得冒充高级 Gate。
6. **确定性与可恢复**：相同输入、版本和时间基准产生稳定标识；重复、乱序、超时和提交响应丢失不得造成重复动作或审计分叉。
7. **隐私最小化**：Replay/HTTP/日志/指标只通过 allowlist 字段；不得传递 ground truth、预测标签、订户标识、原始 payload、文件路径或带查询参数的源 URL。
8. **资源有界**：请求、事件数、速率、持续时间、并发、单体/总 payload、队列和日志基数均必须有硬上限。
9. **可重复发布**：测试、wheel、容器、SBOM 和验收报告必须绑定代码版本与摘要；第三方数据不进入 Git 或发布制品。
10. **真实 Cloud 后置授权**：任何需要 IAM 写入、真实消息投递、真实数据库、集群或网络动作的测试，必须等最小权限 Staging 身份并单独审批。

## 5. A–F 工作流

### A. 发布完整性与 CI

**目标**：把当前本地候选版本变成可追溯、可重复、不可绕过的发布候选。

**交付物**：

- 小步提交和与 commit SHA 绑定的 Local、Lab、Domain、Assurance、Cloud Emulator CI。
- Python 3.12/3.13 测试矩阵、wheel 源码树外安装、CLI/API 导出和依赖一致性检查。
- 依赖约束与哈希、wheel SHA-256、CycloneDX/SPDX SBOM、构建 manifest。
- Secret、SAST、依赖、许可证和制品内容扫描；高风险例外必须有 owner 与到期日。
- 主分支 required checks、最小 Actions 权限、并发取消、超时和有限制品保留期。

**状态**：`IN PROGRESS`。

**Gate A**：相关 workflow 对同一 commit 全绿；Critical/High 漏洞为 0；干净环境离线安装成功；`pip check` 成功；发布报告记录 commit、run URL、wheel 摘要和 SBOM 摘要。

### B. 本地容器与部署

**目标**：交付不依赖 GCP、可在新机器复现的本地答辩部署包。

**交付物**：

- Local Governance、Data Lab 及必要领域包的最小多阶段镜像。
- Compose 入口、仅内部网络、显式持久卷、健康检查和确定性 reset。
- 非 root 用户、只读根文件系统、`tmpfs /tmp`、`cap_drop: ALL`、`no-new-privileges`、资源限制和不可变镜像摘要。
- 从全新工作区运行 `doctor → init → replay/detect → approval → simulate → verify → reset` 的自动化验收。

**状态**：`IN PROGRESS`。

**Gate B**：镜像扫描无 Critical/High；容器无 GCP/模型凭据；安全上下文不降级；成功和失败验证路径均可复现；重启后幂等恢复；发布镜像不包含测试、缓存、构建目录或第三方原始数据。

**当前 S2-01 工作包**：`DONE`（远程 Docker 行为验收已精确绑定 RC `d0a020fb7a5d8a33cd136cd18917d21b7e067946`；不代表 Gate B 完成）。

**当前 S2-02 工作包**：`DONE`（成功/失败治理分支与重启后精确重放已精确绑定 RC `d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4`；不代表 Gate B 完成）。

**当前 S2-03 工作包**：`DONE`（Trivy 双报告、CycloneDX 1.7 与 runner-local release artifact 已精确绑定 RC `68b16ea528a85b743aa8c05044948bac195ee8ec`；不代表 Gate B 完成）。

**当前 S2-04 工作包**：`BLOCKED`（同一 Trivy 0.74.0/数据库快照下，没有发现同时保持 CPython 3.12、glibc、公开可取得、包身份可追溯且完整 Critical/High 为 `0/0` 的可信基础镜像；不得以 `--ignore-unfixed`、漏洞白名单或删除包管理器 provenance 伪造关闭）。

- 容器网络冻结为：`assurance`、`init`、`reset` 使用 `network_mode: none`；`probe`、`smoke` 使用 `network_mode: service:assurance` 共享同一网络命名空间中的 loopback。Compose 不发布或 expose 端口，不创建默认/自定义 bridge，也不通过服务名或反向代理绕过 loopback。
- 运行时使用 digest 固定的 Python 3.12 Debian 基础镜像与 UID/GID `10001:10001`；根文件系统只读，`cap_drop: ALL`、`no-new-privileges`、有界 CPU/内存/PID/nofile 和 `noexec,nosuid,nodev` `/tmp` tmpfs。只有 Docker named workspace volume 可持久写；四类受控 LTE 输入只读 bind，并由镜像内 manifest 校验精确文件集合、字节数和 SHA-256。
- 本地静态门禁为 `75 passed, 1 skipped`；唯一 skip 是 Windows symlink 条件测试。Black、flake8、YAML/JSON 解析和 `git diff --check` 均通过；本机未安装 Docker 与 actionlint，因此这些工具的结论不得写为本地 PASS。
- 精确绑定受测 RC 的 [telco-container run 33311995755](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755) 已全绿：远程 Linux 政策门禁 `76 passed, 0 skipped`，compose-policy job `99258612862` 和 build-inspect-smoke job `99258640065` 均成功；真实 Compose resolve、build/inspect、5 层 / 2,570 成员应用层扫描、9,148 成员合并 rootfs 扫描、初始化 `13440/579/0` 且 `external_access=false`、health/隔离/shared-loopback smoke 与 probe step 均通过。probe step 无 stdout；reset 删除 state/artifacts/marker 且 `workspace_removed=true`，随后 cleanup 成功。runner 本地 image ID 为 `sha256:0acef50a2ee7978ea67a8b37582a19698a21b1303451ce37a0a569d48fef6cff`，不是 registry digest。
- 精确绑定 S2-02 RC 的 [telco-container run 33314782750](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750) 已全绿：Linux 政策门禁 `128 passed`，compose-policy job `99266075811` 和 build-inspect-smoke job `99266104885` 均成功；真实治理 JSON 同时证明 `RESOLVED` 成功分支和 `REOPENED` 失败验证分支，且两者均观察到重启、原请求 exact replay、零真实网络副作用与项目卷清理。
- 同一 RC 的 [Local CI run 33314782757](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757) 两个 job 均成功；两版 Python 各为 Domain + Local `518 passed`、local-stack `29 passed, 2 skipped`、Local E2E `2 passed`。Python 3.12 `VERIFIED RC` artifact 9733117877 的 archive digest 为 `sha256:3d557a52a80960add94c04b443c14f613892701c5b5b93dcfba4174fd78f3469`。
- 精确绑定 S2-03 RC 的 [telco-container run 33320667296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296) 已全绿：compose-policy job `99281949020` 和 build-inspect-smoke job `99281979960` 均成功；14 天 [artifact 9734817516](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/artifacts/9734817516) 分类为 `VERIFIED RUNNER-LOCAL EVIDENCE`，archive digest 为 `sha256:e35d8eb12484feeb474477bae0f3d937019f3ab19e9f8ccf1fd491b8a95f0394`。该 artifact 绑定 runner 本地 image/config digest `sha256:0e8caa8418d93e1f9654655b84331723e8223d9dc94e66274aed1ca3fa7d00bb`、Trivy 0.74.0 fixable Critical/High gate `0/0`、full diagnostic `5` Critical + `29` High 且全部 unfixed，以及 CycloneDX 1.7 SBOM `145` components。
- [S2-04 基础镜像评估](security/s2-04-base-image-evaluation.md) 使用同一 Trivy 0.74.0 与冻结数据库实扫：当前 Bookworm 为 `5C+29H`；保持 CPython 3.12 ABI 的 Trixie 为 `3C+16H`（3 个 fixable、16 个 unfixed）；官方 Distroless Python 3/Debian 13 为 `0C+17H` 且解释器变为 Python 3.13；公开 Chainguard `latest` 虽为 `0/0`，但实际是 Python 3.14，超出现有所有一方包 `<3.14` 契约，且公开 `3.12`/`3.13` tag 不可取得。复制解释器或系统库但丢弃包身份只会形成扫描假阴性，因此 S2-04 按 `BLOCKED` 收口。
- 当前仍未发布 registry image/digest，未提供签名、attestation、provenance 或 Trivy DB OCI digest/signature，且 runner-local artifact 未上传镜像、scanner binary 与数据库，因此不能离线独立重验。S2/Workflow B 及统一计划 P7 保持 `IN PROGRESS`，Gate B、G2、Gate A、S3 与 G4 均保持开放。

### C. Governance HTTP 服务

**目标**：以薄适配层公开现有 `LocalGovernanceEngine`，不复制状态机或授权逻辑。

**交付物**：

- 固定 loopback 的前台 HTTP 服务、健康/就绪端点和版本端点。
- Canonical Fault 接收、Incident 查询、治理准备、审批决定、模拟执行和验证接口。
- 请求大小、深度、字段、超时、并发和分页预算；稳定 JSON 错误码且不回显敏感值。
- `Idempotency-Key`、`trace_id`、`source_event_id`、Incident revision、action hash 和审批 TTL 的端到端绑定。
- 服务崩溃、超时和提交响应丢失后的精确恢复测试。

**状态**：`DONE`（独立本地 Gate 与同一提交的远程 RC 均已通过）。

**Gate C**：服务只监听 loopback；动作仍只允许 `disabled|simulate`；首次确认与动作审批不能合并；错误 hash/revision/actor/TTL 或重放 payload 产生零动作；通过路径到 `RESOLVED`，失败路径到 `REOPENED`；与 CLI 操作同一仓储时语义一致。

### D. Loopback Replay Transport

**目标**：以公开严格 wire 契约连接仓内、无 Cloud 依赖的 `ReplaySink` transport 与 Governance HTTP Canonical Fault 入口。

**交付物**：

- 只接受显式 loopback URL 的 HTTP transport；禁用系统代理、重定向和非本地 DNS。
- checksummed payload、稳定 source-event/idempotency identity、durable ACK/NACK、截止时间、取消不确定性证据和可由调用方持久化的 checkpoint。
- 单调节奏 runner 与硬截止时间；默认零重试，仅显式选择时对 network/timeout 瞬时失败执行有限固定退避。
- 重复、乱序、断点续传、接收端崩溃、poison event 和预算越界测试。
- 从锁定 BubbleRAN artifact 重新适配、构建 plan、投递、创建/关联 Incident 并进入治理闭环的 E2E。
- 保持 label-free 投影；ground truth 与 upstream prediction 永不进入 Fault/Incident payload。

**状态**：`DONE`（独立本地 Gate 与同一提交的远程 RC 均已通过）。

**Gate D**：相同计划精确重放不重复创建活动 Incident；重复/乱序不产生重复审计或动作；任何非 loopback、Cloud 配置、真实动作模式、标签字段、校验和错误或容量越界均失败关闭；传输失败可从持久 checkpoint 恢复。

### E. 可观测性、SLO 与 Runbook

**目标**：使答辩中的每一步都可关联、可度量、可告警、可复盘，同时不泄露数据。

**交付物**：

- 结构化 JSON 日志以及贯通 HTTP、Replay、A2A、MCP、Repository 的 trace context。
- 本地 OpenTelemetry Collector、Prometheus 和 Tempo/Jaeger/Grafana 组合。
- 低基数指标：事件接收、去重、状态迁移、RCA 结论、审批等待/拒绝/过期、动作/验证结果、Replay 重试、Outbox lag、MCP/A2A 延迟与错误、数据库冲突与恢复。
- 本地 SLI/SLO、告警规则和故障处置 Runbook。
- 锁竞争、进程重启、接收超时、poison event、Collector 不可用等演练。

**状态**：`IN PROGRESS`。S4-01 本地答辩可观测证据、S4-02 Canonical 生命周期安全投影、S4-03 固定三窗口 Local acceptance SLI/SLO、S4-04 Local DuckDB 冷备恢复和 S4-05 Local 单进程运行时 Trace 五个窄切片均已由精确绑定 RC 验收并标记 `DONE`。S4-01 只提供进程内有界阶段事件、诊断时序、低基数报告内聚合和报告内告警求值；S4-02 只提供 durable Canonical records 的只读、revision-grouped、隐私最小化投影，且明确 `distributed_trace=false`；S4-03 只提供一个固定三窗口 acceptance sample、五项整数 ppm SLI 和报告内 breach 求值；S4-04 只提供 stopped-writer、Local single-process DuckDB 两文件 cold backup/restore 及受控损坏拒绝演练；S4-05 只提供一个事件在同一进程内经真实 loopback Replay、durable readback 与 A2A Analyze 的六事件相关性，且 A2A transport table 会写。五者都不满足本工作流其余交付物，尤其不构成 OTel/Prometheus 分布式追踪、外部告警、在线/生产恢复或完整故障演练。

**Gate E**：一次 E2E 可由同一 `trace_id` 关联；日志隐私扫描通过；高基数值不作为 metric label；Collector 故障不阻断业务；每个告警有阈值、owner、Runbook 和自动触发/恢复证据。所有指标明确标记为 Local SLO，不外推为 Cloud SLO。

### F. Cloud Staging 就绪包

**目标**：在没有真实身份时完成所有可静态复核准备，使获得 Staging 后只剩真实行为验收。

**交付物**：

- IaC、服务账号/KSA/GSA 映射、IAM 最小权限矩阵、OIDC issuer/audience/attribute 条件。
- Pub/Sub topic/subscription/DLQ 拓扑、重试与保留策略、服务代理权限和 poison event 验收脚本。
- Spanner FGAC、迁移、备份/恢复、容量/配额与并发验收脚本。
- Artifact Registry、GKE/Cloud Run、Secret Manager/KMS、网络和回滚清单。
- 每一项允许/拒绝测试的输入、期望、证据字段和销毁步骤。

**状态**：`IN PROGRESS`（静态准备）；最终 Gate 为 `WAITING FOR CLOUD`。

**Gate F-local**：格式、schema、静态安全和策略检查通过；无服务账号 key、`roles/owner`、`roles/editor`、`allUsers` 或未约束通配权限；计划输出可人工复核。通过后只能标记 `READY FOR STAGING`。

## 6. 阶段计划与状态

| 阶段 | 主要范围 | 工作流 | 状态 | 退出条件 |
|---|---|---|---|---|
| S0 基线冻结 | 领域契约、现有 Gate、发布计数和边界盘点 | A、F | `DONE` | 基线与缺口写入本计划，Cloud/Local 证据分级明确。 |
| S1 本地事件治理接入 | Governance HTTP + Loopback Replay Transport | C、D | `DONE` | Gate C、D、本地攻击性/E2E 与同一提交的远程 RC 均已通过。 |
| S2 本地发布包 | 安全容器、Compose、一键演示 | B | `IN PROGRESS` | S2-01 安全容器基线、S2-02 成功/失败治理重启恢复，以及 S2-03 Trivy 双报告/runner-local release artifact 均已通过精确绑定 RC 的远程 Docker 并标记 `DONE`；S2-04 因无满足完整 `C/H=0` 且保持当前运行时/provenance 契约的可信基础镜像而为 `BLOCKED`，供应链边界与 Gate B 仍未通过。 |
| S3 发布与供应链 | 远程 CI、SBOM、扫描、签名/证明 | A | `IN PROGRESS` | Gate A 通过并绑定提交和远程证据。 |
| S4 可观测与运维 | OTel、指标、SLO、告警、Runbook、演练 | E | `IN PROGRESS` | S4-01 本地答辩可观测证据、S4-02 Canonical 生命周期安全投影、S4-03 固定三窗口 Local acceptance SLI/SLO、S4-04 Local DuckDB 冷备恢复与 S4-05 Local 单进程运行时 Trace 窄切片已完成；仍须交付跨进程/分布式 trace、OTel/Collector、Prometheus、运行态时间型可用性/延迟型/长期统计可靠性 SLI/SLO、外部告警、MCP 传播、在线/异地/加密签名/多副本/Cloud/生产恢复及完整故障演练，并通过 Gate E。 |
| S5 Cloud 就绪 | IaC、权限矩阵、Staging 验收包 | F | `IN PROGRESS` | Gate F-local 通过，状态变为 `READY FOR STAGING`。 |
| S6 Cloud Staging | 真实 IAM/OIDC/DLQ/WI/Spanner/GKE | F | `WAITING FOR CLOUD` | 获得最小权限身份并通过真实 Cloud Gate。 |
| S7 答辩发布 | 固化版本、证据包、演示脚本、限制声明 | A–F | `IN PROGRESS` | S7-01、S7-02 与支持答辩的 S4-01、S4-02、S4-03、S4-04、S4-05 窄切片均已标记 `DONE`；完整 S4/Workflow E、最终阶段限制声明及 G2/G4/G5 等适用 Gate 仍未通过。 |

### 6.1 S7-01 原生一键答辩闭环

**状态**：`DONE`。

固定入口为 `python tools/local-stack/run_defense_demo.py --approve-local-simulation`，不接受 workspace、URL、header、actor、Cloud、Docker 或任意命令参数。它在 `.local/networkagent-defense` 下创建两个随机、marker-owned 的隔离工作区，分别完成 `RESOLVED/PASSED` 与 `REOPENED/FAILED`；每条路径均验证 13,440 条 KPI、579 条安全 Trace、15 个候选、action hash/revision 双绑定、八事件审计链、一个 ActionRun/VerificationRun、`side_effects=false`，再原样重放首次审批命令并确认终态和记录不放大。最终两个工作区都必须由安全 reset 删除，原子 JSON 报告及 SHA-256 留在 run 目录。

本地证据为脚本定向/真实集成 `18 passed`、local-stack 全套 `49 passed`，以及一次直接命令运行：两分支、精确重试和双清理均通过。未提交工作树中的直接运行诚实标记为 `LOCAL_WORKTREE_SIMULATION_EVIDENCE`；只有 Git 可用、前后 commit 相同且 tracked tree 始终干净时才标记 `LOCAL_NATIVE_SIMULATION_EVIDENCE`。

远程验收绑定 RC `c08d634c9c3deb628df5f98d4f60dd1675cd5706`：[Local run 33326721937](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33326721937) 的 Python 3.12/3.13 jobs 99298066127/99298066217 均为 `success`，每版通过 Domain + Local `518 passed`、local-stack `47 passed, 2 skipped`、Local E2E `2 passed`；两版直接命令均验证 `commit_bound=true`、`commit_sha=GITHUB_SHA`、双终态与双清理。同一 SHA 的 Data Lab run 33326721947 与 Assurance run 33326721991 也全绿。因此 S7-01 标记 `DONE`，但 S7 总阶段不因本工作包通过而关闭。

### 6.2 S7-02 运行手册与证据包

**状态**：`DONE`。

[Local 原生答辩演示运行手册](runbooks/local-defense-demo.md) 已给出第三方前提、唯一命令、6–8 分钟讲解顺序、JSON 字段解释、报告 SHA-256 核验、失败处理/安全清理、证据表和限制声明。发布证据路径为 `release-evidence/defense-demo-summary.json`：仅 Python 3.12 job 上传 release artifact，Python 3.13 job 仍执行并校验演示；该文件作为 release manifest 的可选 supplemental evidence 记录 bytes/SHA-256，并通过 `verify-manifest` 复核。

远程验收绑定 RC `79feeee6771749bbdd1ce7ce44b77193a1db544f`。Local run 33327786238 的 Python 3.12/3.13 jobs 99300888630/99300888747 均为 `success`，每版为 Domain + Local `518 passed`、local-stack `49 passed, 2 skipped`、Local E2E `2 passed`，且一键演示的 source binding、双终态、exact retry 和双 cleanup 均通过；3.12 release boundary 为 `18 tests passed`。同 SHA 的 Data Lab run 33327786237 三个 jobs 全绿（`220 passed, 3 skipped`），Assurance run 33327786211 四个 jobs 全绿（两个主 job 各 `854 passed, 3 skipped`，Supervisor `57 passed`）。

Python 3.12 [VERIFIED RC artifact 9736785325](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33327786238/artifacts/9736785325) 名称为 `telco-local-release-py3.12-attempt-1`，104,109 bytes，archive digest `sha256:b4b6f6ab762695a367169d54078ab1f6d2ec64c4ef3c21c132190421ed31cff3`，到期时间 `2026-09-13T18:24:17Z`。独立下载的 10 文件闭包与 manifest 记录的 9 个非 manifest 文件精确一致；演示 summary 为 3,379 bytes / SHA-256 `ae0b412a42d9430a35117dd9e8987662c7359cc95ea72a076fa2f869bcaa51ef`，其 `report.sha256` 为 `a91676e52789d5c520d3cb3e2e8b0a47d19d7801f5bebbb51f3f10ffa613bc5f`。因此 S7-02 标记 `DONE`。

S7-01 历史 [Local artifact 9736486858](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33326721937/artifacts/9736486858) 仍保留，但不含演示 JSON，不能替代当前 S7-02 VERIFIED RC。本次证据文档提交晚于且不等于受测 RC；S7 总体继续 `IN PROGRESS`，S2-04 继续 `BLOCKED`，G2/G4/G5、S4、Cloud、真实动作和生产结论均保持开放。

### 6.3 S4-01 本地答辩可观测证据

**状态**：`DONE`（仅限本节定义的窄切片）。

固定入口为 `python tools/local-stack/run_observability_demo.py --approve-local-simulation`，除显式本地模拟确认外不接受其他参数。该入口复用且不改变 S7-01 原生答辩逻辑，在同一安全 run 目录保留 `local-observability-report.json`，并以 `networkagent-local-observability/1.0` 输出 21 个子进程阶段事件和 1 个 `run_finalize` 事件。每个事件只含 `sequence/stage/branch/attempt/outcome/duration_ms/error_class`；正常 `REOPENED/FAILED` 是预期业务结果，不被误报为执行故障。

该报告提供 `diagnostic_only=true` 的单次时序快照、仅使用 `branch/error_class/outcome/stage` 四个固定 label 的报告内指标聚合，以及 `LOCAL_EXECUTION_FAILURE`、`LOCAL_CLEANUP_FAILURE`、`LOCAL_RETRY_AMPLIFICATION`、`LOCAL_CONTRACT_DRIFT` 四项报告内固定告警求值。`observation_id`、source commit 和 defense report SHA 只关联本次本地证据；`propagated_trace=false`。报告不记录绝对路径、子进程 stdout/stderr、环境或原始参数。579 条安全 Trace rows 是 Local 数据集输入记录，不是 OpenTelemetry span。

远程验收绑定 RC `cb4a4e7191f67aa71ef980668352d55001e23142`。[Local run 33330915665](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665) 的 Python 3.12/3.13 jobs 99309192438/99309192337 全绿：两版各为 Domain + Local `518 passed`、local-stack `66 passed, 2 skipped`、Local E2E `2 passed`；3.12 release boundary 为 `18 tests passed`。本 RC 只有 Local workflow 被路径规则触发，不能声称同 SHA Lab/Assurance 回归。

Python 3.12 [VERIFIED RC artifact 9737683310](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665/artifacts/9737683310) 为 106,309 bytes，archive digest `sha256:5b26c3ccaff8e57c10c4bb3375ebbad8156d151e26158b1123639e3423006eca`，到期时间 `2026-09-13T19:33:25Z`。独立下载的 11 文件闭包与 manifest 记录的 10 个非 manifest 文件精确一致；defense/observability supplemental evidence 分别为 3,379/9,178 bytes，SHA-256 分别为 `14f04bf556f03fd7c22edf0272240dba566610466546362442abdab3dd06a9b7` / `2741c3a25983056a73ea0bcd6ea99ffc14bf83dbd6209e4a9811b93c0a98df49`。

[Local 可观测答辩运行手册](runbooks/local-observability-demo.md) 冻结了第三方复核、报告核验和四项本地告警处置步骤。本切片不提供 OpenTelemetry export/Collector、跨 HTTP/Replay/A2A/MCP trace、Prometheus、外部告警投递、SLI/SLO 或 Collector 故障容忍，因而不关闭 S4、Workflow E、Gate E、G5 或 S7；S2-04 继续 `BLOCKED`，G2/G4、Cloud、真实动作和生产结论继续开放。本证据回填提交晚于且不等于受测 RC。

### 6.4 S4-02 Canonical 生命周期安全投影

**状态**：`DONE`（仅限本节定义的窄切片）。

固定入口为 `python tools/local-stack/run_lifecycle_evidence_demo.py --approve-local-simulation`。它复用原生双分支答辩流程，在 exact retry 后只读完整持久 Canonical records，并按 `REVISION_GROUPED_ATOMIC_PROJECTION` 输出成功与失败两个投影。每分支恰有 revision 0–7 八个原子组和 14 个唯一事件；成功为 `RESOLVED/PASSED`，故意验证失败为 `REOPENED/FAILED`。投影冻结 `read_only=true`、`distributed_trace=false`、精确记录绑定、连续 revision、单 Incident、单执行尝试与 `side_effects=false`，只输出事件 sequence/time/type/component/operation/outcome，不输出 domain/workspace 标识或哈希、资源/KPI/root cause/evidence URI、路径、correlation、actor/reason、stdout/stderr 或幂等键。

远程验收绑定 RC `69643e8a6f79b1264d60e5517eeb9a24035c8e7d`。[Local run 33336341831](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831) 的 Python 3.12/3.13 jobs 99323794962/99323795037 均为 `success`：每版 Domain + Local `576 passed`、local-stack `89 passed, 2 skipped`、Local E2E `2 passed`；3.12 release boundary 为 `18 tests passed`。同 SHA 的 Assurance run 33336341877、Container run 33336341805 和 Cloud run 33336341859 全绿，Data Lab 未触发；Cloud 结果不代表 Cloud Staging 或生产验收。

Python 3.12 [VERIFIED RC artifact 9739212391](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/artifacts/9739212391) 名称为 `telco-local-release-py3.12-attempt-1`，115,482 bytes，archive digest `sha256:237bcb207e29e53aba6c907f94efcb1999b77b1640f59dd95b8d3c9c30b27aa7`，到期时间 `2026-09-13T21:30:29Z`。独立下载的 12 文件闭包与 manifest 记录的 11 个非 manifest 文件精确一致；第三个 supplemental evidence `local-lifecycle-summary.json` 为 8,431 bytes / SHA-256 `5726b4eec6f0c4621a3d804b0f6973a24d41ae14ea1878b2e57205a299966e45`，重建持久报告为 8,290 bytes / SHA-256 `21528fd8694da0cb0c51452b14c45af864b24951282475367addbcd1b74fa004`。

[Local Canonical 生命周期投影运行手册](runbooks/local-lifecycle-projection.md) 冻结了字段 allowlist、14 节点图、报告与 release artifact 复核方法和限制。S4-02 不交付运行时结构化日志、OpenTelemetry/Collector、Prometheus、分布式 trace、SLI/SLO 或外部告警，也不关闭 S4、Workflow E、P7、S7、Gate E/G5 或 G2/G4；S2-04 继续 `BLOCKED`。本证据回填提交晚于且不等于受测 RC。

### 6.5 S4-03 固定三窗口 Local acceptance SLI/SLO 证据

**状态**：`DONE`（仅限本节定义的窄切片）。

固定入口为 `python tools/local-stack/run_slo_evidence_demo.py --approve-local-simulation`，不接受窗口数、阈值、workspace、路径、URL、Cloud 参数或任意命令。每次调用顺序执行三个全新、隔离的 S4-01 窗口，以 `networkagent-local-slo-evidence/1.0` 聚合 66 个阶段事件。五项整数 ppm SLI 分别为 stage command `66/66`、expected branch `6/6`、exact retry `6/6`、workspace cleanup `6/6` 和 observation contract `3/3`；每项目标均为 1,000,000 ppm，错误预算为 0。正常 `REOPENED/FAILED` 是预期业务结果；可信完整窗口的指标缺失为 `BREACH`，身份、摘要、路径、清理或契约不可信则为 `ERROR`，不得执行或声称 SLO 数学。

远程验收绑定 RC `faa11ff7a165cd5eae6cf3f0fa1a030c9472f46c`。[Local run 33340008133](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133) 总耗时 11m40s，只有该 workflow 被触发。Python 3.12/3.13 jobs 99333812338/99333812397 均成功，分别耗时 10m51s / 11m36s；固定三窗口 step 分别耗时 2m38s / 2m51s，3.13 发布步骤按矩阵跳过。两版各自独立验证三个窗口和五项 `OK` SLI，evaluation 为 `OK`、无 breach，privacy 为 `PASS`。

Python 3.12 [VERIFIED RC artifact 9740377450](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133/artifacts/9740377450) 名称为 `telco-local-release-py3.12-attempt-1`，保留 14 天，117,046 bytes，archive digest `sha256:11207c784de25ec1d6d956bb8b47274663100455a6924ccf95213c839c848536`。独立下载的 13 文件闭包与 12 条 manifest 记录加 manifest 自身精确一致，全部 bytes/SHA 匹配，manifest 为 `PASS` 且 `failures=[]`。第四个 supplemental evidence `local-slo-summary.json` 为 3,271 bytes / SHA-256 `ae181eaffe6da11c5dd0cdea07dcfcba3a400daaf6ed44352b1e573faa5f489b`；去除 stdout-only `report` envelope 后重建持久报告为 3,136 bytes / SHA-256 `2538629be3133920e76f2de9e0fa0ff9575853095538c266efc6e544d02c5c64`。schema/classification/source binding、三窗口、五项 SLI、evaluation、privacy 和 12 项 `not_claimed` 均匹配冻结契约。

[Local 固定窗口 acceptance SLO 运行手册](runbooks/local-slo-evidence.md) 冻结了第三方报告重建、breach/error 分类、清理和新鲜评估恢复规则。该结果只是固定三窗口 Local acceptance sample，不是时间型可用性、延迟或长期统计可靠性 SLO，不提供运行时结构化日志、OpenTelemetry/Collector、Prometheus、分布式 trace、外部告警、自动恢复、备份/恢复或 Cloud/生产证据。S4/Workflow E/P7/S7 继续 `IN PROGRESS`，Gate E/G5/G2/G4 保持开放，S2-04 继续 `BLOCKED`；本证据回填提交晚于且不等于受测 RC。

### 6.6 S4-04 Local DuckDB 冷备恢复证据

**状态**：`DONE`（仅限本节定义的窄切片）。

固定入口为 `python tools/local-stack/run_backup_restore_demo.py --approve-local-simulation`，除显式本地模拟确认外不接受其他参数。底层冻结为 `local_stack.py --workspace W backup --destination NEW_DIR` 和 `local_stack.py --workspace W restore --source DIR --expected-manifest-sha256 64-lowercase-hex --yes`；执行前必须停止所有 writer。backup 使用 DuckDB `CHECKPOINT` 与 `COPY FROM DATABASE` 创建 schema `networkagent-local-cold-backup/1.0` 的精确两文件闭包（`networkagent.duckdb`、`backup-manifest.json`），限制数据库 128 MiB、manifest 16 KiB，并绑定物理 bytes/SHA、Local/Assurance schema、DuckDB library/storage、catalog/table/row 和逻辑内容指纹。restore 在替换前重复核验 exact membership、canonical/无重复键 manifest、摘要、catalog 与逻辑指纹，并通过同目录临时文件和原子 replace 发布。

固定演练先生成一个 `RESOLVED/PASSED` 生命周期，冷备后 reset 并 fresh init；随后复制并实际破坏 database bytes，同时保留 manifest 声明，使恢复以稳定 `backup_invalid` 拒绝且 fresh database 摘要不变。有效备份首次恢复必须 `changed=true`，同参数 retry 必须 `changed=false`，两次的 catalog/row/manifest/database 摘要与恢复前完整 lifecycle projection 精确等价。成功路径 workspace、有效备份和损坏副本均被身份绑定清理；目录身份及文件 `device/inode/size/mtime/ctime/link-count` 任一不一致时失败关闭并保留 raced/unknown residue，绝不把未知对象当作本次所有物自动删除。

远程验收绑定 RC `54551feb43be60c3b9bdd5eab076cdb7c0aba61a`。[Local run 33353994792](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792) 总耗时 13m04s；Python 3.12/3.13 jobs 99372557281/99372557192 均成功，分别为 13m00s / 10m28s，固定 backup/recovery step 为 37s / 31s。每版 Domain + Local `576 passed`、local-stack `224 passed, 3 skipped`、Local E2E `2 passed`。[Container run 33353994784](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994784) 总耗时 2m07s，jobs 99372557334/99372587413 全绿；该 SHA 只触发 Local 与 Container workflows。

Python 3.12 [VERIFIED RC artifact 9744736851](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792/artifacts/9744736851) 名称为 `telco-local-release-py3.12-attempt-1`，保留 14 天，118,251 bytes，archive digest `sha256:5ca975e95cd86befb77ca977a3acc2aa57122a0148202b945a3a5c50a3153fe1`，创建/到期时间为 `2026-08-31T03:42:18Z` / `2026-09-14T03:42:17Z`。独立下载的 14 个非链接普通文件与 13 条 manifest 记录加 manifest 自身精确一致；6,847-byte manifest SHA-256 为 `e13db5a8d326538e7c2aaea1d51f0ce8a71e557e4b0d366659d2873127d8d502`，状态 `PASS` 且 `failures=[]`。第五个 supplemental evidence `local-backup-recovery-summary.json` 为 1,951 bytes / SHA-256 `f44187fece9d33b71b520521df188c6043cfdfe4e67618c71b96b5703828e7bb`；去除 stdout-only `report` envelope 后重建持久报告为 1,804 bytes / SHA-256 `f6698b0846571a6af3a9cca7edd57f20e1204154fc09dbec3630e86fca784a96`。scope 五项、proof 十项、delivered 七项、privacy `PASS` 与 12 项 `not_claimed` 均匹配冻结契约；制品只含发布证据，不含冷备数据库或原始 backup manifest。

[Local 冷备恢复运行手册](runbooks/local-backup-restore.md) 冻结了 stopped-writer 前提、底层命令、两文件/manifest/逻辑核验、损坏拒绝、幂等、生命周期、身份与 race 清理、错误分类、报告重建、隐私和全部限制。该结果不是 online backup、production HA、multi-replica failover、RPO/RTO、power-loss durability、encrypted/signed、off-host/remote、cross-version、Cloud/Spanner 或 production recovery，也不自动清理未知身份/raced residue。S4/Workflow E/P7/S7 继续 `IN PROGRESS`，Gate E/G5/G2/G4 保持开放，S2-04 继续 `BLOCKED`；本证据回填提交晚于且不等于受测 RC。

### 6.7 S4-05 Local 单进程运行时 Trace 贯通证据

**状态**：`DONE`（仅限本节定义的窄切片）。

固定入口为 `python tools/local-stack/run_runtime_trace_demo.py --approve-local-simulation`，除显式本地模拟确认外不接受其他参数。一个固定 BubbleRAN 事件通过真实 `127.0.0.1` TCP Replay sender/receiver、DuckDB durable readback 与真实 A2A Analyze。sender 以冻结 domain separator 和已验证 `source_event_id` 派生 `X-NetworkAgent-Trace-Id`；receiver 独立复算，重复、非 ASCII、超长或不匹配值在 business write 前以 `LOCAL_FAULT_TRACE_CONFLICT` 拒绝。成功路径按 `sender/repository/receiver/sender/a2a/a2a` 输出六个固定 `OK` 事件，核对 header、durable Incident、revision-0 audit、source association、A2A request 与 RCA result 六项 binding。

数据库比较发生在 Replay 已持久化之后、A2A Analyze 前后。只有 A2A transport 表 `assurance_a2a_tasks` 变化；Canonical domain 与另外九表保持不变，actions/approvals/executions/verifications 均为 `0 -> 0`。因此准确边界是 `analyze_semantics=TRANSPORT_WRITE_DOMAIN_UNCHANGED` 与 `whole_database_read_only_claimed=false`，不得把 domain RCA 的只读语义扩大成完整数据库只读。成功运行只清理临时 scenario workspace，在固定 run 目录保留 report 与六行 raw JSONL；raw JSONL 包含 correlation，只允许本地诊断，绝不进入 release summary 或 artifact。

首个功能提交 `b0bcb8fa39c2971e2dd1c1910cde69d68cc97edc` 的 Local run 33362166565 在测试收集阶段失败，不能作为成功 RC。corrective RC `2e59d7ca88cc550e315d63e80339909ef619cd2c` 将 Assurance-owned trace tests 迁回 Assurance profile 并移除重复 Local 收集；其 Assurance run 33362806092（jobs 99397345468/99397345590/99397345635/99397345601）、Local run 33362806180（jobs 99397346249/99397346041）和 Container run 33362806104（jobs 99397345678/99397392344）全部成功。

Python 3.12 [VERIFIED RC artifact 9747354240](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33362806092/artifacts/9747354240) 由 Assurance workflow 发布，名称 `telco-assurance-release-py3.12-attempt-1`，246,678 bytes，archive digest `sha256:f772dcae631cdde59483eaef6a28d1caee0b0b357d8eb5eb069e863747991fa4`。独立下载的 12 文件闭包与 11 条 manifest 记录加 manifest 自身精确一致；制品无 `.jsonl`/DuckDB，去除 supplemental summary 的 stdout-only `report` envelope 后重建持久报告为 1,651 bytes / SHA-256 `5932b0454c7d095b7864f7c50cd0e2a48a05e288dbb74fd83a1773aedcaea5e8`。

[Local 单进程运行时 Trace 运行手册](runbooks/local-runtime-trace.md) 冻结了唯一命令、七字段事件、header 派生、六项 binding、表写语义、稳定错误、隐私、本地 raw JSONL、artifact 独立复核与十项 `not_claimed`。该结果不是 OpenTelemetry/Collector、Prometheus、distributed/cross-process/multi-event correlation、MCP propagation、external alert delivery、sink guarantee、Cloud/production observability 或 full-database read-only Analyze。S4/Workflow E/P7/S7 继续 `IN PROGRESS`，Gate E/G5/G2/G4 保持开放，S2-04 继续 `BLOCKED`；本证据回填提交晚于且不等于受测 RC。

## 7. Sprint 1：Governance HTTP + Loopback Replay

> Sprint 状态：`DONE`
> Sprint 目标：完成“锁定公开数据 → 有界 Replay → 本地 HTTP Fault 入口 → Canonical Incident → RCA → 独立审批 → 模拟动作 → 验证”的真实本地链路。

### 7.1 工作包

| ID | 工作包 | 状态 | 验收摘要 |
|---|---|---|---|
| S1-01 | HTTP 契约与威胁模型冻结 | `DONE` | 四个 `/local/v1/incidents` 查询/治理路由及 `POST /local/v1/faults/replay` 要求 loopback Host+peer；32 连接、1 秒首部、1 个请求体槽/零队列、2 秒 body 与 1 个业务 worker/零队列/5 秒操作预算已通过本地独立 Gate 与新 RC。 |
| S1-02 | Governance HTTP 薄适配层 | `DONE` | 不复制引擎逻辑；稳定 JSON 404/405/408/503，超时或调用方取消不取消未知结果的底层事务，settle 后精确重试复用仓储幂等结果；本地与远程 Gate 均通过。 |
| S1-03 | 持久接收与幂等恢复 | `DONE` | 公开 `ReplayWirePayload` 为唯一 sender/receiver 契约；HTTP 202 只在有界回读 current Incident 不可变事实、初始 revision-0 Audit 与 SourceAssociation 通过后返回；缺失事实失败关闭、变更冲突与 response-loss exact replay 已通过验收。 |
| S1-04 | Loopback HTTP ReplaySink | `DONE` | 单调 paced runner、deadline、cancel/不确定序号证据、有限 transient retry 与 caller-owned 原子持久 checkpoint 均通过双 Pydantic、真实 TCP 重启和同一提交远程矩阵；checkpoint 仍为非签名 ACK 的 single-writer 本地 continuation claim。 |
| S1-05 | BubbleRAN → Governance E2E | `DONE` | 真实 loopback TCP E2E 使用受控 5G SA UL BLER exact provenance，覆盖 `RESOLVED`/`REOPENED`/`REJECTED`/审批过期 `FAILED`、标签不泄漏、持久 checkpoint 重启零投递和 settled exact replay 零写；独立本地 Gate 与远程 RC 均通过。 |
| S1-06 | 安全与兼容矩阵 | `DONE` | RC `7cbff490ccb71befb42c7cd30204f7f88e3b2f38` 的 Assurance、Data Lab 与 Local workflows 全部 `success` 且 `headSha` 精确绑定；双 Pydantic、边界/E2E、wheel allowlist/源码树外 smoke 与 `pip check` 证据见 3.2。Cloud run 仍明确属于上一 RC。 |
| S1-07 | 操作文档与证据 | `DONE` | README/Gate 已同步最终 HTTP budgets、health/ready/version、caller-owned 持久 checkpoint、真实 TCP 重启零投递与残余边界；三个 Python 3.12 job 的 14 天 `VERIFIED RC` artifacts、archive/wheel 摘要、runtime inventory、SBOM 与零已知漏洞 `pip-audit` 均绑定同一 RC。 |

### 7.2 Sprint 1 DoD

- HTTP 服务和 Replay transport 均固定 loopback，测试证明不能借助代理、重定向、DNS、IPv4/IPv6 表示或 URL 用户信息越界。
- 每个写操作必须携带稳定幂等键；精确重试只读，改变请求冲突，提交成功但响应丢失可恢复。
- Canonical Fault、Incident、RCA、Approval、ActionRun、VerificationRun 和 AuditEvent 之间的 ID、revision、hash、资源范围和时间窗绑定不变。
- 审批准备、审批决定和执行保持分离；调用方不能在首次 Fault/Incident 请求中夹带批准。
- 服务只允许固定 `LOCAL_SIMULATION`，验证为确定性本地测试输入；任何实际网络、GCP、Kubernetes、Engineer、Operator 或 GitOps 配置均拒绝。
- Replay 事件仅含 allowlist KPI、单位、时间、资源、质量标志和完整性字段；不含 ground truth、预测标签、订户标识或源数据行。
- 单请求、批次、速率、持续时间、总 payload、资源数、并发、队列、重试和日志均有硬预算，并在读取/分配前尽早失败。
- E2E 至少覆盖 `RESOLVED`、`REOPENED`、审批拒绝、审批过期、重复、乱序、poison、崩溃恢复和 reset；所有真实副作用计数为 0。
- 适用单元、契约、集成、E2E 和攻击性测试在支持矩阵中通过；新增公共 API 能从 wheel 在源码树外导入。
- 文档、CI、测试计数、制品摘要和残余限制与实现一致；远程 CI 未运行时不得把 Sprint 标记为 `DONE`。

## 8. 接口冻结

### 8.1 冻结原则

- `telco-domain` 的 Canonical 模型、状态枚举、状态迁移守卫和 Repository 语义为最高优先级契约。
- HTTP 与 Replay 是适配层，不得绕过 Repository CAS、审批策略、动作网关或隐私校验。
- 首个 HTTP 契约版本为 `local-governance-http/1.0`；Replay 事件与计划沿用其各自 `schema_version`/`policy_version`。
- 未知字段、未知枚举、非 UTC 时间、非规范 ID、非有限数字和非布尔布尔值必须拒绝，不做宽松转换。
- 所有响应为严格 JSON；错误只返回稳定 `error_code`、安全 message、trace/request ID，不返回本地路径、异常堆栈、原始值或源 URL。

### 8.2 Sprint 1 当前冻结的最小 HTTP 面

| 方法与路径 | 用途 | 写入边界 |
|---|---|---|
| `GET /.well-known/agent-card.json` | 保留既有 Assurance A2A 能力发现 | 原路由不变、零业务写。 |
| `POST /` | 保留既有 A2A JSON-RPC detect/confirm/analyze | 原路由与契约不变。 |
| `POST /local/v1/faults/replay` | 接收严格 `ReplayWirePayload` 并创建/识别 Canonical Incident | 必须携带 `replay-v1` operation header 与与 wire 一致的 `Idempotency-Key`；202 表示 current Incident immutable facts、revision-0 Audit 与 SourceAssociation 已有界回读核验。 |
| `GET /local/v1/incidents/{incident_id}` | 读取 Incident 与治理安全摘要 | 零写、字段 allowlist、loopback Host。 |
| `POST /local/v1/incidents/{incident_id}/prepare` | 推进 RCA 并产生固定模拟动作预览 | 最多推进到 `AWAITING_APPROVAL`；不得自动批准。 |
| `POST /local/v1/incidents/{incident_id}/decide` | 独立审批决定 | 必须携带 `governance-v1` operation header，并绑定 exact hash、expected revision、actor、reason 和幂等键。 |
| `POST /local/v1/incidents/{incident_id}/execute` | 执行已生效的本地模拟动作并验证 | 必须携带 operation header；仅 `LOCAL_SIMULATION`；通过到 `RESOLVED`，失败到 `REOPENED`。 |
| `GET /local/v1/healthz` | 本地进程存活探针 | 仅校验直接 loopback Host 与 peer；不读取仓储、不执行写入、不代表依赖就绪。所有标准非 GET 方法使用固定有界 JSON 405 契约，HEAD 按 HTTP 语义省略 body。 |
| `GET /local/v1/readyz` | 本地依赖就绪探针 | 仅执行一次 1 秒有界 Canonical Incident 仓储读；就绪返回 200，依赖失败、超时或已有卡住 worker 返回固定 503；不代表 Cloud readiness。 |
| `GET /local/v1/version` | 版本与契约摘要 | 只返回 allowlist 的服务、包、HTTP/Replay API 与 Domain schema 版本；不返回路径、环境变量或未签名的部署身份声明。 |

以上八个 `/local/v1` 路由构成当前冻结面；后续破坏性调整必须提升 API 版本。三个探针同样只支持官方直接 loopback runner 和 GET；所有其他标准 HTTP 方法使用固定、有界 JSON 405 契约，HEAD 按 HTTP 语义省略 body。`healthz` 仅证明进程可响应，`readyz` 仅证明本地 Canonical 仓储可执行有界读；线程超时不能强制终止底层同步读，因此未结束 worker 存续期间后续 readiness 固定 503，不并发叠加 worker。`version` 仅提供未签名的 allowlist 元数据。服务不得提供“真实动作”“任意工具调用”“任意 URL 转发”或自由 SQL 接口。

### 8.3 Replay transport 冻结项

- transport 输入必须是已通过 `build_replay_plan()` 生成并重新验证的 immutable plan。
- sink 只接收校验后的 `ReplayEvent` 安全 payload，不接收任意 Mapping、任意 URL 或调用方自报的 artifact 绑定。
- 默认顺序投递；重复、乱序和 resume 只能由显式测试策略启用，并保持同一 plan/window 的 source-event/idempotency identity。
- transport 只把 202/204 解释为接收端 ACK，不代表 RCA、审批或动作成功。仓内 Assurance receiver 的 202 已由真实 Repository 测试证明 durable-before-ACK；其他自定义 sink 仍不自动获得这一语义。
- 当前 transport 只接受完整 `ReplayDeliveryCheckpoint`：精确绑定 plan ID、最高连续成功序号、对应 source event ID 与 payload SHA-256。plan ID 本身绑定完整 policy/endpoint、回放窗口和事件序列；跨 plan、旧窗口或字段漂移会在调用 transport 前拒绝。可选 store 要求显式本地 workspace/checkpoint 目录，以严格有界 JSON、原子替换、单调不回退和非阻塞单-writer 锁实现 `load/save/clear`；Windows 在任何路径探测前拒绝 UNC/device 且只允许 `DRIVE_FIXED`。POSIX mount topology 与恶意同用户 ancestor swap 属本机文件系统信任边界。checkpoint 仍只是 caller-owned continuation claim，不是接收端签名的 ACK 证明。
- `deliver_replay_plan()` 保持立即、串行、每 occurrence 一次尝试，专用于确定性 fault injection。`run_paced_replay()` 按计划偏移与速率执行单调节奏，设硬 deadline，并在 deadline/cancel 时保留最后已确认 checkpoint 及不确定序号证据。
- `run_persistent_paced_replay()` 在每个有效 202/204 后先持久化新 checkpoint，再允许 runner 推进或发送下一事件。response 丢失或本地保存失败会保留旧 checkpoint；恢复时可能再次发送同一稳定幂等事件，并依赖 receiver 的 exact-replay 幂等语义。
- paced runner 默认 `NONE`；只有显式的 `TRANSIENT_ONCE|TRANSIENT_TWICE` 可重试 network/timeout 错误，使用固定有界退避。契约、环境、事件、payload、响应、重定向、HTTP 状态与 poison 失败不重试。
- 接收器只允许精确锁定的 BubbleRAN 数据集/版本/场景与 `5G_SA` GNB；每 source event 独立 Incident。`ran.mac.ul_bler > 0.15 ratio` 时只使用服务端固定 rule version/content hash 建立 provenance，不信任客户端规则字段，也不将该阈值宣称为生产结论。

## 9. Gate 与全局 DoD

| Gate | 适用阶段 | 必需结论 |
|---|---|---|
| G0 Source Gate | 所有阶段 | 工作树范围清晰；无秘密、大文件、原始第三方数据、构建缓存或意外生成物；`git diff --check` 通过。 |
| G1 Contract Gate | S1–S6 | Canonical schema、HTTP、Replay、A2A、MCP 和仓储契约测试通过；未知/旧版本失败关闭。 |
| G2 Security Gate | S1–S7 | 威胁模型、隐私、审批、副作用、SSRF、路径、重放、容量、依赖和制品扫描通过；Critical/High 为 0。 |
| G3 Local E2E Gate | S1–S4、S7 | 新工作区可完成成功、失败、拒绝、过期、恢复和 reset；无外部副作用。 |
| G4 Release Gate | S2–S4、S7 | 双 Python 矩阵、wheel/容器、SBOM、摘要、源码树外冒烟、远程 CI 和文档证据齐全。 |
| G5 Operability Gate | S4、S7 | trace、指标、告警、Runbook、备份/恢复和故障演练通过。 |
| G6 Cloud Staging Gate | S6 | 真实 IAM/OIDC/DLQ/WI/Spanner/GKE/网络/秘密/回滚证据通过；Emulator 不可替代。 |

全局 DoD：

1. 功能、失败语义、幂等、恢复、安全、隐私和容量均有自动化回归。
2. 代码只依赖公开冻结接口，未引入双主写、隐藏后门或真实动作旁路。
3. wheel、容器、SBOM、摘要和运行证据绑定同一 commit。
4. 操作手册可由未参与开发者在干净环境执行，并得到同类安全结果。
5. 已知限制、条件跳过和未执行 Cloud Gate 均显式披露。
6. 独立审计无 P0/P1 未关闭项；较低风险有 owner、缓解措施和复核日期。
7. 只有 G0–G5 通过才能发布 Local Demo；只有额外 G6 通过才能宣称 Cloud Staging Ready/Accepted。

## 10. 风险登记

| 风险 | 等级 | 触发信号 | 缓解措施 | 关闭条件 |
|---|---|---|---|---|
| HTTP 适配层复制或绕过治理状态机 | 高 | HTTP 与 CLI 对同一事件产生不同 revision/审计 | 只调用 `LocalGovernanceEngine`；共享契约/E2E | 同源事件跨入口得到一致持久结果。 |
| Replay 被用作任意 HTTP/SSRF 客户端 | 高 | 接受非 loopback、重定向、代理或用户信息 URL | 解析后固定 loopback、禁代理/重定向、连接前后复核 | SSRF 攻击矩阵全部失败关闭。 |
| 标签或隐私数据泄漏 | 高 | Fault/日志含 ground truth、prediction、订户 ID、原始行 | allowlist 模型、二次验证、输出隐私扫描 | E2E payload/日志/制品扫描为 0 泄漏。 |
| 审批与执行被合并或重放 | 高 | 首次请求可执行、旧 hash/revision 可复用 | 两阶段 API、CAS、TTL、actor/reason 和 request fingerprint | 攻击性审批测试全绿、零额外 ActionRun。 |
| 重复/乱序造成 Incident 或动作放大 | 高 | 同一 source event 出现多个活动 Incident/ActionRun | 稳定 ID、唯一约束、checkpoint、幂等接收 | 重放压力下唯一性和审计连续性保持。 |
| HTTP/Replay 容量耗尽 | 高 | 无限 Sequence、巨型 JSON、高基数字段或无限重试 | 读取前预算、并发/队列/重试硬上限 | 边界测试资源使用可控且快速拒绝。 |
| Local 与 Cloud 结论混淆 | 高 | 报告把 Emulator/模拟 SLO 写成 Cloud PASS | 证据分级、状态词汇和独立 G6 | 所有发布材料明确标注适用范围。 |
| 依赖和制品不可重复 | 中 | 浮动版本、镜像 `latest`、构建结果漂移 | 约束+hash、digest、SBOM、attestation | 干净环境重复安装/验证通过。 |
| legacy 容器安全基线不足 | 中 | root、可写根文件系统、宽权限、调试工具 | 新组件先用安全模板；legacy 分批迁移 | 发布范围镜像全部通过容器策略。 |
| 第三方数据许可扩散 | 中 | 原始 BubbleRAN 数据进入 Git/wheel/镜像 | 仅本地缓存、内容白名单、许可证据 | Git/制品扫描无数据字节，合规复核完成。 |
| 单进程 DuckDB 并发边界 | 中 | 多写者锁冲突、长事务或恢复时间超标 | 明确单写者、限流；保留 S4-04 stopped-writer 冷备恢复证据，继续补在线/异地/生产演练 | 本地负载基准满足已声明 Local SLO，且适用完整恢复 Gate 通过。 |

## 11. 必须等待真实 Cloud 的项目

以下项目可以提前准备 IaC、测试脚本和期望结果，但没有真实 GCP 项目及最小权限身份时不得标为 `PASS`：

1. GitHub/外部 OIDC 到 Google STS 的真实 token exchange、audience/issuer/attribute condition 允许与拒绝测试。
2. GKE Workload Identity 的 KSA→GSA 授权、跨 namespace 冒用拒绝和 token 轮换。
3. Pub/Sub DLQ forwarding、最大投递次数、服务代理角色、ack deadline、redelivery、ordering 和 push OIDC 签名/audience 的真实行为。
4. Cloud Logging Sink → Pub/Sub → Fault Ingress 的端到端事件、过滤器和权限。
5. Spanner Staging FGAC/IAM deny、真实事务冲突/重试/延迟/配额、备份和 PITR。
6. Artifact Registry push/pull、GKE/Cloud Run rollout、Load Balancer/TLS/Cloud Armor、VPC/DNS/NAT/VPC-SC 和 NetworkPolicy 的组合行为。
7. Secret Manager/KMS/CMEK 的访问、轮换、撤销和审计日志。
8. 多副本真实负载、Cloud Monitoring 告警、预算/成本、区域故障和回滚时间。
9. Engineer/MCP/GitOps/Network Operator 的真实网络变更、最小权限、审批后执行和真实回滚。
10. 公司组织策略、数据驻留、许可证、审计保留和生产上线审批。

在上述项目完成前，S5 Cloud 就绪包保持 `IN PROGRESS`，S6 真实 Cloud Staging 保持 `WAITING FOR CLOUD`；Local Demo 的通过不会自动改变任一状态。

## 12. 发布判定与答辩口径

### 12.1 可在无 GCP 时发布的结论

当 G0–G5 通过后，可以发布：

- `Local Demo Release Candidate`；
- 无外部副作用的本地 Incident/RCA/审批/模拟动作/验证闭环；
- 锁定公开数据到本地治理入口的 loopback 故障演练能力；
- 可重复 wheel/容器、SBOM、测试、可观测性和 Runbook 证据。

### 12.2 禁止提前声明的结论

在 G6 前不得声明：

- Cloud Staging 或生产已就绪；
- IAM/OIDC、DLQ、Workload Identity 已验收；
- 真实 Spanner/GKE/Operator 的性能、可用性、权限或回滚已验证；
- 本地 SLO 等同于 Cloud SLO；
- 模拟动作等同于真实网络治理闭环。

## 13. 变更日志

| 日期 | 版本 | 变更 | 状态影响 |
|---|---|---|---|
| 2026-08-30 | 2.0 | 建立实施开发计划 2.0；记录当前 Local/Cloud/Data Lab 基线，定义无 GCP 原则、A–F 工作流、接口冻结、Gate/DoD、风险和 Cloud 后置清单；启动 Governance HTTP 与 Loopback Replay Sprint。 | S1=`IN PROGRESS`；Cloud G6=`WAITING FOR CLOUD`。 |
| 2026-08-30 | 2.0 | 完成 Sprint 1 第一批本地实现：Assurance 增加四个严格 loopback Governance 路由，并同时校验 Host 与连接 peer；Data Lab 增加 opt-in loopback HTTP transport。终审发现并关闭裸 checkpoint 序号未绑定 plan 的 Gate，恢复现精确绑定 plan、序号、事件与 payload 摘要。两者仍未通过 Canonical Fault 业务接收器连接，Replay 也尚无 paced runner/自动重试/持久 checkpoint。 | S1-01/02/04=`READY FOR REVIEW`；S1 总体仍为 `IN PROGRESS`。 |
| 2026-08-30 | 2.0 | 完成 Sprint 1 第二批本地实现：冻结公开 `ReplayWirePayload`；增加单调 paced runner、有界 transient retry/deadline/cancel 证据与 durable-before-202 Canonical Fault receiver。受控 BubbleRAN 5G SA UL BLER 规则只使用服务端 exact provenance；真实 TCP E2E 覆盖成功、验证失败、拒绝、过期和 settled exact replay 零写。保留每 source 独立 Incident，未实现 checkpoint 持久化、跨事件聚合、真实动作、Cloud 或 RCAEval。 | S1-03/04/05=`READY FOR REVIEW`；远程 CI、health/ready 与其余 DoD 未完成，S1 保持 `IN PROGRESS`。 |
| 2026-08-30 | 2.0 | RC `427fc6832bf6b115d035e5d2cb492a25ffd82395` 的 Assurance、Data Lab、Local 与额外 Cloud workflows 全部成功，且四个 run 的 `headSha` 均绑定该 RC；双 Python、双 Pydantic、E2E、wheel 内容/外部安装与依赖检查证据已回填。远程 workflows 未输出 wheel digest 或上传 artifact，后续证据文档提交也不是受测 RC。 | S1-06=`DONE`；S1-01..05 保持 `READY FOR REVIEW`；S1-07、Sprint 1、P3e 与 S3 保持 `IN PROGRESS`。 |
| 2026-08-30 | 2.0 | 在上述 RC 之后实现严格 loopback `healthz/readyz/version`、caller-owned 原子持久 checkpoint 与持久 paced wrapper；真实 TCP E2E `1 passed`，确认完成计划重启后零选择/零尝试/零投递。当前本地 Lab+Lab E2E 双 Pydantic 各 `222 passed, 1 skipped`、Assurance full `54 passed`、Domain+Local+shared contracts `520 passed`、status `4 passed`、Local E2E `3 passed`、A2A contracts `33 passed`、A2A E2E `4 passed`。release artifact、SBOM 与 `pip-audit` 证据生成已实现，尚待新远程 RC 验收。 | S1-04 仍 `READY FOR REVIEW`；S1-07、Sprint 1、P3e 与 S3 仍 `IN PROGRESS`，不编造新 run URL、artifact 或摘要。 |
| 2026-08-30 | 2.0 | RC `6ba631929c312bbff27ef0ad4a9136d2cb390ae1` 的 Assurance、Data Lab、Local 与额外 Cloud workflows 全部成功；三个 Python 3.12 jobs 上传 14 天 `VERIFIED RC` artifacts。下载后确认全部文件字节数/SHA 与 manifest 一致，CycloneDX 1.4、runtime inventory、wheel scan 与 `pip-audit==2.10.1` 零已知漏洞均 PASS。 | S1-07=`DONE`；S1-01..05 仍 `READY FOR REVIEW`，Sprint 1、P3e 与 S3 因独立评审、RCAEval、签名/attestation、离线 hash-lock、容器等缺口继续 `IN PROGRESS`。 |
| 2026-08-30 | 2.0 | Gate C/D 独立本地复核通过。Assurance 增加严格 32 连接上限、首部/body/业务 deadline、共享零队列 admission、固定 JSON 404/405/408/503、未知提交结果保守恢复和显式禁用 proxy headers；最新本地结果为 Assurance `76 passed`、A2A contracts `33 passed`、A2A E2E `4 passed`、local-stack `22 passed`、Local E2E `3 passed`。 | C/D=`READY FOR REVIEW`；S1-01..05 只剩最新候选的远程 RC，Sprint 1 仍 `IN PROGRESS`。 |
| 2026-08-30 | 2.0 | RC `7cbff490ccb71befb42c7cd30204f7f88e3b2f38` 的 Assurance、Data Lab、Local 三个 workflows 共 9 个 job 全绿，且三个 run 的 `headSha` 均精确绑定该 SHA；三个 Python 3.12 job 发布 14 天 `VERIFIED RC` artifacts，archive/wheel 摘要、runtime inventory、SBOM 与零已知漏洞审计证据已回填。上一 RC 的 Cloud run 只保留为历史 Emulator 证据。 | S1-01..07、Workflow C/D、S1 与 Sprint 1=`DONE`；当前转入 Sprint 2 准备启动。P3e、S3、Cloud Staging 及其余全局缺口不受此状态变化影响。 |
| 2026-08-30 | 2.0 | 启动 S2-01 安全容器候选：冻结 `none` + `service:assurance` 共享 loopback 网络模型、digest-pinned 多阶段镜像、non-root/只读根/capability 与资源限制、只读输入 manifest、named workspace volume、固定入口、Compose/镜像层守卫及 `telco-container` workflow。独立静态审计与 `56 passed, 1 skipped`、Black/flake8/YAML/JSON/diff 门禁通过；本机无 Docker/actionlint，远程 workflow 尚未运行。 | Workflow B、S2=`IN PROGRESS`；S2-01=`READY FOR REVIEW`；Gate B 仍未通过。`--require-hashes`、Trivy C/H、容器 SBOM、签名/attestation/provenance 和全部真实 Docker 行为证据保持开放。 |
| 2026-08-30 | 2.0 | RC `d0a020fb7a5d8a33cd136cd18917d21b7e067946` 的 `telco-container` run 33311995755 全绿：远程 Linux `76 passed, 0 skipped`，compose-policy job 99258612862 与 build-inspect-smoke job 99258640065 成功；真实 Compose/build/inspect 得到 runner 本地 image ID `sha256:0acef50a2ee7978ea67a8b37582a19698a21b1303451ce37a0a569d48fef6cff`（非 registry digest），完成 5 层 / 2,570 成员应用层和 9,148 成员合并 rootfs 扫描、初始化 `13440/579/0`/无外部访问及 health/隔离/shared-loopback smoke/probe steps。probe 无 stdout；reset 删除 state/artifacts/marker 且 `workspace_removed=true`，cleanup 成功。最新本地门禁为 `75 passed, 1 skipped`；run 上传 0 artifacts，未发布 registry image 或容器 SBOM/签名/attestation/provenance。 | S2-01=`DONE`；S2/Workflow B/P7 仍为 `IN PROGRESS`，Gate B、Gate A、S3 与 G4 保持开放。 |
| 2026-08-30 | 2.0 | RC `d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4` 的 `telco-container` run 33314782750 与 Local run 33314782757 全绿。容器两 job 为 99266075811/99266104885，Linux 政策门禁 `128 passed`；真实成功/失败治理 JSON 分别为 `RESOLVED`/`REOPENED`，且两者均 `restart_observed=true`、`exact_replay=true`、`real_network_side_effects=false`；顶层 `projects_removed=true` 证明两个 Compose 项目均已清理。Local 两 job 为 99266075954/99266075805，两版各通过 Domain + Local `518 passed`、local-stack `29 passed, 2 skipped`、Local E2E `2 passed`；Python 3.12 `VERIFIED RC` artifact 9733117877 archive digest 为 `3d557a52a80960add94c04b443c14f613892701c5b5b93dcfba4174fd78f3469`。容器 run 上传 0 artifacts。 | S2-02=`DONE`；S2/Workflow B/P7 仍为 `IN PROGRESS`，Gate B、Gate A、S3 与 G4 保持开放；registry image/digest、容器 SBOM、hash-lock、Trivy 与 signing/attestation/provenance 未完成。 |
| 2026-08-30 | 2.0 | RC `68b16ea528a85b743aa8c05044948bac195ee8ec` 的 `telco-container` run 33320667296 全绿：compose-policy job 99281949020 与 build-inspect-smoke job 99281979960 成功；14 天 artifact 9734817516 分类 `VERIFIED RUNNER-LOCAL EVIDENCE`，archive digest 为 `sha256:e35d8eb12484feeb474477bae0f3d937019f3ab19e9f8ccf1fd491b8a95f0394`。该 artifact 绑定 runner-local image/config digest `sha256:0e8caa8418d93e1f9654655b84331723e8223d9dc94e66274aed1ca3fa7d00bb`、Trivy 0.74.0 fixable Critical/High gate `0/0`、full diagnostic `5` Critical + `29` High 且全部 unfixed，以及 CycloneDX 1.7 SBOM `145` components。 | S2-03=`DONE`；S2/Workflow B/P7 保持 `IN PROGRESS`，Gate B、G2、Gate A、S3 与 G4 保持开放；仍无 registry image/digest、签名/attestation/provenance、Trivy DB OCI digest/signature 与离线独立重验。 |
| 2026-08-31 | 2.0 | 完成 S2-04 完整 Critical/High 关闭可行性评估：同一 Trivy 0.74.0/冻结数据库下，Bookworm=`5C+29H`、Trixie=`3C+16H`、Distroless Python 3/Debian 13=`0C+17H`；公开 Chainguard `latest` 的 `0/0` 对应 Python 3.14，不满足当前 `<3.14` 一方包与 CPython 3.12 锁定契约。拒绝 `--ignore-unfixed`、漏洞白名单和丢弃包 provenance 的扫描假清零。 | S2-04=`BLOCKED`；S2/Workflow B/P7 继续 `IN PROGRESS`，Gate B、G2、Gate A、S3、G4 均保持开放；后续只能以 provenance 可验证的 3.12 镜像或独立 Python/base migration 重新开启。 |
| 2026-08-31 | 2.0 | 新增固定无 Docker 的 S7-01 原生一键答辩入口：同一命令在两个隔离工作区完成 `RESOLVED/PASSED` 与 `REOPENED/FAILED`，验证原审批请求精确重放、记录不放大、`side_effects=false` 与双安全 reset，并原子保留报告及 SHA-256。本地脚本定向/真实集成为 `18 passed`，local-stack 全套为 `49 passed`。 | S7-01=`READY FOR REVIEW`、S7=`IN PROGRESS`；等待新提交远程 Python 3.12/3.13 验收，不声明拒绝/过期、容器、真实动作、Cloud 或 G2/G4/G5 完成。 |
| 2026-08-31 | 2.0 | RC `c08d634c9c3deb628df5f98d4f60dd1675cd5706` 的 Local run 33326721937 双 Python jobs 99298066127/99298066217 全绿：各为 Domain + Local `518 passed`、local-stack `47 passed, 2 skipped`、Local E2E `2 passed`，一键命令核对源码绑定、双终态和双清理；同 SHA 的 Data Lab 33326721947 与 Assurance 33326721991 也全绿。新增 S7-02 第三方运行手册，并冻结 `release-evidence/defense-demo-summary.json` supplemental-evidence/manifest 契约。S7-01 历史 artifact 9736486858 为 102,753 bytes、archive digest `sha256:a1961b1897cdb86c802ce3dbd9762381ef7726e28476a1d24657162014b330f2`，但不含该 JSON。 | S7-01=`DONE`；S7-02/S7=`IN PROGRESS`。等待新 RC 生成并核验演示 supplemental evidence；S2-04 保持 `BLOCKED`，不声明 G2/G4/G5、S4、Cloud、真实动作或生产完成。 |
| 2026-08-31 | 2.0 | RC `79feeee6771749bbdd1ce7ce44b77193a1db544f` 的 Local run 33327786238 双 Python jobs 全绿：每版 Domain + Local `518 passed`、local-stack `49 passed, 2 skipped`、Local E2E `2 passed`，且 source binding、双终态、exact retry、双 cleanup 均通过；3.12 release boundary 为 `18 tests passed`。同 SHA 的 Data Lab 33327786237 与 Assurance 33327786211 全绿。Python 3.12 VERIFIED RC artifact 9736785325 为 104,109 bytes、archive digest `sha256:b4b6f6ab762695a367169d54078ab1f6d2ec64c4ef3c21c132190421ed31cff3`；独立下载 10 文件闭包与 manifest 精确一致，演示 summary 为 3,379 bytes / SHA-256 `ae0b412a42d9430a35117dd9e8987662c7359cc95ea72a076fa2f869bcaa51ef`，其 report SHA 为 `a91676e52789d5c520d3cb3e2e8b0a47d19d7801f5bebbb51f3f10ffa613bc5f`。本证据文档提交晚于且不等于该受测 RC。 | S7-02=`DONE`；S7 保持 `IN PROGRESS`。S2-04=`BLOCKED`，G2/G4/G5、S4、Cloud、真实动作与生产均保持开放。 |
| 2026-08-31 | 2.0 | RC `cb4a4e7191f67aa71ef980668352d55001e23142` 的 Local run 33330915665 双 Python jobs 99309192438/99309192337 全绿：每版 Domain + Local `518 passed`、local-stack `66 passed, 2 skipped`、Local E2E `2 passed`，3.12 release boundary 为 `18 tests passed`。两版核对 22 个有界阶段事件、诊断时序、低基数报告内指标、四项报告内告警、隐私与 `propagated_trace=false`。Python 3.12 VERIFIED RC artifact 9737683310 为 106,309 bytes、archive digest `sha256:5b26c3ccaff8e57c10c4bb3375ebbad8156d151e26158b1123639e3423006eca`；独立下载 11 文件闭包与 manifest 精确一致，defense/observability summaries 分别为 3,379/9,178 bytes，SHA-256 为 `14f04bf556f03fd7c22edf0272240dba566610466546362442abdab3dd06a9b7` / `2741c3a25983056a73ea0bcd6ea99ffc14bf83dbd6209e4a9811b93c0a98df49`。本 RC 只有 Local workflow 因路径规则触发，不能声称同 SHA Lab/Assurance 回归。 | S4-01 窄切片=`DONE`；S4、Workflow E 与 S7 保持 `IN PROGRESS`，Gate E/G5 保持开放。S2-04=`BLOCKED`，G2/G4、Cloud、真实动作与生产均保持开放；本证据文档提交晚于且不等于受测 RC。 |
| 2026-08-31 | 2.0 | RC `69643e8a6f79b1264d60e5517eeb9a24035c8e7d` 的 Local run 33336341831 双 Python jobs 99323794962/99323795037 全绿：每版 Domain + Local `576 passed`、local-stack `89 passed, 2 skipped`、Local E2E `2 passed`，3.12 release boundary `18 tests passed`。双分支各 8 个 revision group / 14 个事件，只读、精确绑定、单执行尝试、exact retry、双 cleanup、`side_effects=false` 与隐私 allowlist 均通过。Python 3.12 VERIFIED RC artifact 9739212391 为 115,482 bytes、archive digest `sha256:237bcb207e29e53aba6c907f94efcb1999b77b1640f59dd95b8d3c9c30b27aa7`；独立下载 12 文件闭包匹配 11 条 manifest 记录加 manifest，lifecycle summary 为 8,431 bytes / SHA-256 `5726b4eec6f0c4621a3d804b0f6973a24d41ae14ea1878b2e57205a299966e45`，重建报告为 8,290 bytes / SHA-256 `21528fd8694da0cb0c51452b14c45af864b24951282475367addbcd1b74fa004`。同 SHA Assurance 33336341877、Container 33336341805、Cloud 33336341859 全绿，Lab 未触发。 | S4-02 窄切片=`DONE`；S4/Workflow E/P7/S7=`IN PROGRESS`，Gate E/G5/G2/G4 保持开放，S2-04=`BLOCKED`；`distributed_trace=false`，不声明 runtime structured logs、OTel/Prometheus、SLO、外部告警或 Cloud production 完成；本证据文档提交晚于且不等于受测 RC。 |
| 2026-08-31 | 2.0 | RC `faa11ff7a165cd5eae6cf3f0fa1a030c9472f46c` 的 [Local run 33340008133](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33340008133) 仅触发 Local workflow，总耗时 11m40s；双 Python jobs 99333812338/99333812397 均成功，分别耗时 10m51s / 11m36s，固定三窗口 step 为 2m38s / 2m51s。两版各验证 3 个全新窗口、66 个阶段事件和 `66/66`、`6/6`、`6/6`、`6/6`、`3/3` 五项 SLI，observed/objective 均为 1,000,000 ppm、budget 0、状态 `OK`，evaluation `OK`/无 breach、privacy `PASS`。Python 3.12 VERIFIED RC artifact 9740377450 保留 14 天，为 117,046 bytes、archive digest `sha256:11207c784de25ec1d6d956bb8b47274663100455a6924ccf95213c839c848536`；独立下载 13 文件闭包匹配 12 条 manifest 记录加 manifest，`local-slo-summary.json` 为 3,271 bytes / SHA-256 `ae181eaffe6da11c5dd0cdea07dcfcba3a400daaf6ed44352b1e573faa5f489b`，重建报告为 3,136 bytes / SHA-256 `2538629be3133920e76f2de9e0fa0ff9575853095538c266efc6e544d02c5c64`，12 项 `not_claimed` 均匹配。 | S4-03 窄切片=`DONE`；S4/Workflow E/P7/S7=`IN PROGRESS`，Gate E/G5/G2/G4 保持开放，S2-04=`BLOCKED`；不声明时间型/延迟/长期统计可靠性 SLO、OTel/Prometheus、外部告警、备份恢复或 Cloud/生产完成；本证据文档提交晚于且不等于受测 RC。 |
| 2026-08-31 | 2.0 | RC `54551feb43be60c3b9bdd5eab076cdb7c0aba61a` 的 [Local run 33353994792](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994792) 总耗时 13m04s，双 Python jobs 99372557281/99372557192 均成功；每版 Domain + Local `576 passed`、local-stack `224 passed, 3 skipped`、Local E2E `2 passed`，固定 backup/recovery step 为 37s / 31s。stopped-writer 两文件 cold backup、manifest/逻辑指纹、损坏副本拒绝且 fresh 零改、restore `true`/retry `false`、生命周期等价和身份绑定清理均通过。同 SHA [Container run 33353994784](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33353994784) 两 jobs 全绿。Python 3.12 VERIFIED RC artifact 9744736851 为 118,251 bytes、archive digest `sha256:5ca975e95cd86befb77ca977a3acc2aa57122a0148202b945a3a5c50a3153fe1`；独立下载 14 文件闭包匹配 13 条 manifest 记录加 manifest，backup summary 为 1,951 bytes / SHA-256 `f44187fece9d33b71b520521df188c6043cfdfe4e67618c71b96b5703828e7bb`，重建报告为 1,804 bytes / SHA-256 `f6698b0846571a6af3a9cca7edd57f20e1204154fc09dbec3630e86fca784a96`。 | S4-04 窄切片=`DONE`；S4/Workflow E/P7/S7=`IN PROGRESS`，Gate E/G5/G2/G4 保持开放，S2-04=`BLOCKED`；不声明在线/生产 HA、多副本、RPO/RTO、断电、加密签名、异地、跨版本、Cloud/Spanner 或生产恢复；本证据文档提交晚于且不等于受测 RC。 |
| 2026-08-31 | 2.0 | 首个 feature commit `b0bcb8fa39c2971e2dd1c1910cde69d68cc97edc` 的 Local run 33362166565 在收集错置的 Assurance trace tests 时失败，未作为成功 RC。corrective RC `2e59d7ca88cc550e315d63e80339909ef619cd2c` 将测试迁回 Assurance profile；Assurance run 33362806092 四 jobs、Local run 33362806180 双 jobs、Container run 33362806104 双 jobs 全绿。固定命令验证真实 loopback Replay -> durable readback -> A2A Analyze 的 6 events/4 components/6 bindings；仅 `assurance_a2a_tasks` 改变，其余 9 表与 Canonical domain 不变，治理四类 `0 -> 0`。Assurance VERIFIED RC artifact 9747354240 为 246,678 bytes、archive digest `sha256:f772dcae631cdde59483eaef6a28d1caee0b0b357d8eb5eb069e863747991fa4`；独立下载 12 文件闭包匹配 11 条 manifest 记录加 manifest，无 raw JSONL，重建报告为 1,651 bytes / SHA-256 `5932b0454c7d095b7864f7c50cd0e2a48a05e288dbb74fd83a1773aedcaea5e8`。 | S4-05 窄切片=`DONE`；S4/Workflow E/P7/S7=`IN PROGRESS`，Gate E/G5/G2/G4 保持开放，S2-04=`BLOCKED`；不声明 OTel/Prometheus、distributed/cross-process/multi-event、MCP、external alert、Cloud/production、sink guarantee 或 full-database read-only；本证据文档提交晚于且不等于受测 RC。 |
