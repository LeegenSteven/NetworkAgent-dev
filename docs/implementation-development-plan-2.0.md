# 实施开发计划 2.0

> 文档状态：Active
> 计划版本：2.0
> 建立日期：2026-08-30
> 最近更新：2026-08-30
> 当前执行阶段：Sprint 1 — Governance HTTP 服务与 Loopback Replay Transport（`IN PROGRESS`）
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

### 3.1 已完成能力

| 能力 | 状态 | 当前证据与边界 |
|---|---|---|
| P1 Canonical Domain | `DONE` | 统一 Incident、状态机、证据、RCA、审批、动作、验证和审计契约；历史双 Pydantic 与远程 CI Gate 已通过。 |
| P2a Local Profile | `DONE` | DuckDB、本地 LTE 数据适配、Detector、规则 RCA、显式 Incident 确认和安全 CLI 已完成。 |
| P2b Assurance/A2A | `DONE` | 本机限定 A2A、持久 challenge/task、Supervisor 结构化桥接及真实 HTTP 契约测试已完成。 |
| P3a–P3d Cloud 代码与 Emulator | `DONE` | Spanner v2、事务型 Inbox/Outbox、Fault Ingress、只读 MCP、FGAC 制品和迁移逻辑已通过远程 Spanner Emulator Gate；不代表 Cloud Staging 已通过。 |
| Local 模拟治理闭环 | `DONE` | 13,440 条 KPI、579 条安全 Trace、15 个候选；独立审批后可到 `RESOLVED`，验证失败到 `REOPENED`，无真实网络副作用。 |
| Local Stack | `DONE` | `doctor/init/status/demo/serve/reset` 已具备工作区所有权、loopback、默认禁用动作、安全 reset 与提交响应丢失恢复。 |
| BubbleRAN Data Lab | `IN PROGRESS` | 下载锁定、隐私投影、离线评估、immutable `ReplayPlan`、公开 `ReplayWirePayload`、loopback transport、单调 paced runner 与 Assurance Canonical Fault 持久接收器已完成；每个 source event 独立映射 Incident，不做跨事件聚合，仓内 checkpoint 持久化和 RCAEval 尚未完成。 |

### 3.2 当前发布证据

- `telco-lab` 完整本地套件在 Pydantic 2.5.3 与 2.13.4 下各 197 项通过；Assurance Fault receiver 定向 22 项、Assurance 全套 50 项、组合发布回归 133 项通过。
- 真实 loopback TCP E2E 1 项通过，覆盖 `RESOLVED`、验证失败 `REOPENED`、审批 `REJECTED`、审批过期 `FAILED` 和 settled exact replay 零新增写入。
- 最新 `telco-lab 0.1.0` wheel 为 67,653 bytes，SHA-256 为 `96B5D696CB769E29256C5319FF391DA5CC30F2B25D108F5730FF9F8BD467C40B`；该摘要是本地构建证据，正式 RC 仍须在同一提交的远程 CI 重新生成。
- P1、P2a、P2b 和 P3 Cloud Emulator 已有历史远程成功证据。
- 当前 Local Governance 和 Data Lab 增量仍处于未提交工作树；对应的新远程 CI 仍为 `PENDING`，因此不能宣称 2.0 发布完成。

### 3.3 已知缺口

- Governance HTTP 薄层、公开 Replay wire 契约、持久 Canonical Fault receiver 和 paced runner 已有本地证据，但远程 CI 尚未运行。
- `ReplaySink` 同时支持立即串行投递与单调节奏 runner；只有明确启用的瞬时 network/timeout 失败才执行有限次重试。checkpoint 仍由调用方持有，仓内未提供持久化存储。
- BubbleRAN 事件已进入 Canonical Incident 治理链路，但当前是每 source 独立 Incident，不做跨事件聚合；RCA 仅识别受控 `5G_SA` BubbleRAN UL BLER 签名，不得外推生产。
- 新 Local/Data Lab 组件尚无统一的最小权限容器和 Compose 发布入口。
- 新增 CI 尚未在提交后的 Python 3.12/3.13 远程环境运行。
- 跨组件结构化日志、指标、分布式追踪、本地 SLO、告警和完整 Runbook 尚未交付。
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

**状态**：`NOT STARTED`。

**Gate B**：镜像扫描无 Critical/High；容器无 GCP/模型凭据；安全上下文不降级；成功和失败验证路径均可复现；重启后幂等恢复；发布镜像不包含测试、缓存、构建目录或第三方原始数据。

### C. Governance HTTP 服务

**目标**：以薄适配层公开现有 `LocalGovernanceEngine`，不复制状态机或授权逻辑。

**交付物**：

- 固定 loopback 的前台 HTTP 服务、健康/就绪端点和版本端点。
- Canonical Fault 接收、Incident 查询、治理准备、审批决定、模拟执行和验证接口。
- 请求大小、深度、字段、超时、并发和分页预算；稳定 JSON 错误码且不回显敏感值。
- `Idempotency-Key`、`trace_id`、`source_event_id`、Incident revision、action hash 和审批 TTL 的端到端绑定。
- 服务崩溃、超时和提交响应丢失后的精确恢复测试。

**状态**：`IN PROGRESS`。

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

**状态**：`IN PROGRESS`。

**Gate D**：相同计划精确重放不重复创建活动 Incident；重复/乱序不产生重复审计或动作；任何非 loopback、Cloud 配置、真实动作模式、标签字段、校验和错误或容量越界均失败关闭；传输失败可从持久 checkpoint 恢复。

### E. 可观测性、SLO 与 Runbook

**目标**：使答辩中的每一步都可关联、可度量、可告警、可复盘，同时不泄露数据。

**交付物**：

- 结构化 JSON 日志以及贯通 HTTP、Replay、A2A、MCP、Repository 的 trace context。
- 本地 OpenTelemetry Collector、Prometheus 和 Tempo/Jaeger/Grafana 组合。
- 低基数指标：事件接收、去重、状态迁移、RCA 结论、审批等待/拒绝/过期、动作/验证结果、Replay 重试、Outbox lag、MCP/A2A 延迟与错误、数据库冲突与恢复。
- 本地 SLI/SLO、告警规则和故障处置 Runbook。
- 锁竞争、进程重启、接收超时、poison event、Collector 不可用等演练。

**状态**：`NOT STARTED`。

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
| S1 本地事件治理接入 | Governance HTTP + Loopback Replay Transport | C、D | `IN PROGRESS` | Gate C、D 全部通过，真实本地 E2E 可重复。 |
| S2 本地发布包 | 安全容器、Compose、一键演示 | B | `NOT STARTED` | Gate B 通过，新机器从零完成演示。 |
| S3 发布与供应链 | 远程 CI、SBOM、扫描、签名/证明 | A | `IN PROGRESS` | Gate A 通过并绑定提交和远程证据。 |
| S4 可观测与运维 | OTel、指标、SLO、告警、Runbook、演练 | E | `NOT STARTED` | Gate E 通过并形成演练报告。 |
| S5 Cloud 就绪 | IaC、权限矩阵、Staging 验收包 | F | `IN PROGRESS` | Gate F-local 通过，状态变为 `READY FOR STAGING`。 |
| S6 Cloud Staging | 真实 IAM/OIDC/DLQ/WI/Spanner/GKE | F | `WAITING FOR CLOUD` | 获得最小权限身份并通过真实 Cloud Gate。 |
| S7 答辩发布 | 固化版本、证据包、演示脚本、限制声明 | A–F | `NOT STARTED` | Local Release Gate 通过；Cloud 能力按真实状态单列。 |

## 7. Sprint 1：Governance HTTP + Loopback Replay

> Sprint 状态：`IN PROGRESS`
> Sprint 目标：完成“锁定公开数据 → 有界 Replay → 本地 HTTP Fault 入口 → Canonical Incident → RCA → 独立审批 → 模拟动作 → 验证”的真实本地链路。

### 7.1 工作包

| ID | 工作包 | 状态 | 验收摘要 |
|---|---|---|---|
| S1-01 | HTTP 契约与威胁模型冻结 | `READY FOR REVIEW` | 已冻结四个 `/local/v1/incidents` 查询/治理路由及 `POST /local/v1/faults/replay`；Fault 入口要求 loopback Host+peer、`replay-v1`、唯一幂等键和严格 JSON。 |
| S1-02 | Governance HTTP 薄适配层 | `READY FOR REVIEW` | 不复制引擎逻辑；查询、准备、决定、执行/验证接口已完成真实 DuckDB/ASGI 回归并通过本地独立终审，等待远程 CI。 |
| S1-03 | 持久接收与幂等恢复 | `READY FOR REVIEW` | 公开 `ReplayWirePayload` 为唯一 sender/receiver 契约；HTTP 202 只在有界回读 current Incident 不可变事实、初始 revision-0 Audit 与 SourceAssociation 通过后返回。删除 Incident 或初始 Audit 时返回 503 且零新增写；变更 payload 冲突，response-loss exact replay 返回首次持久回执。 |
| S1-04 | Loopback HTTP ReplaySink | `READY FOR REVIEW` | 在原有禁代理/重定向、逐事件重验、固定预算和 plan/event-bound checkpoint 上，新增单调 paced runner、硬 deadline、cancel/不确定序号证据与仅 network/timeout 的有限 transient retry；checkpoint 尚未仓内持久化。 |
| S1-05 | BubbleRAN → Governance E2E | `READY FOR REVIEW` | 真实 loopback TCP E2E 使用受控 5G SA UL BLER 规则的 exact provenance，覆盖 `RESOLVED`/`REOPENED`/`REJECTED`/审批过期 `FAILED`、标签不泄漏和 settled exact replay 零写。 |
| S1-06 | 安全与兼容矩阵 | `IN PROGRESS` | 本地双 Pydantic、边界用例和公共导出已通过；CI 已配置 Python 3.12/3.13 + Pydantic 2.13.4，以及 Python 3.12 + 声明下限 2.5.3，待提交后执行。 |
| S1-07 | 操作文档与证据 | `IN PROGRESS` | README、Gate、bridge 边界、测试计数与本地 wheel 摘要已回填；health/ready 操作说明、checkpoint 持久化和远程 CI URL 待补。 |

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

以上五个 `/local/v1` 路由已经由契约测试冻结；后续破坏性调整必须提升 API 版本。`healthz`、`readyz` 和版本接口仍属于后续待冻结接口，当前不得宣称存在。服务不得提供“真实动作”“任意工具调用”“任意 URL 转发”或自由 SQL 接口。

### 8.3 Replay transport 冻结项

- transport 输入必须是已通过 `build_replay_plan()` 生成并重新验证的 immutable plan。
- sink 只接收校验后的 `ReplayEvent` 安全 payload，不接收任意 Mapping、任意 URL 或调用方自报的 artifact 绑定。
- 默认顺序投递；重复、乱序和 resume 只能由显式测试策略启用，并保持同一 plan/window 的 source-event/idempotency identity。
- transport 只把 202/204 解释为接收端 ACK，不代表 RCA、审批或动作成功。仓内 Assurance receiver 的 202 已由真实 Repository 测试证明 durable-before-ACK；其他自定义 sink 仍不自动获得这一语义。
- 当前 transport 只接受完整 `ReplayDeliveryCheckpoint`：精确绑定 plan ID、最高连续成功序号、对应 source event ID 与 payload SHA-256。plan ID 本身绑定完整 policy/endpoint、回放窗口和事件序列；跨 plan、旧窗口或字段漂移会在调用 transport 前拒绝。checkpoint 仍只是 caller-owned continuation claim，不是接收端签名的 ACK 证明，也不由本包持久化。
- `deliver_replay_plan()` 保持立即、串行、每 occurrence 一次尝试，专用于确定性 fault injection。`run_paced_replay()` 按计划偏移与速率执行单调节奏，设硬 deadline，并在 deadline/cancel 时保留最后已确认 checkpoint 及不确定序号证据。
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
| 单进程 DuckDB 并发边界 | 中 | 多写者锁冲突、长事务或恢复时间超标 | 明确单写者、限流、备份/恢复演练 | 本地负载基准满足已声明 Local SLO。 |

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

在上述项目完成前，Cloud 总状态保持 `IN PROGRESS / WAITING FOR CLOUD`；Local Demo 的通过不会自动改变该状态。

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
