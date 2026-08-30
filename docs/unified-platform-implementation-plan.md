# NetworkAgent 统一智能运维平台实施计划

> 文档状态：Active（统一平台架构、历史阶段与总路线事实来源）
> 首次建立：2026-08-28  
> 最近更新：2026-08-31
> 下一轮执行基线：[实施开发计划 2.0](implementation-development-plan-2.0.md)
> 当前里程碑：P0、P1、P2 已完成 / 实施开发计划 2.0 的 Sprint 1 Governance HTTP + Loopback Replay 已通过本地独立 Gate 与同一提交远程 RC；Sprint 2 安全容器/Compose 保持 `IN PROGRESS`，S2-01 安全容器基线、S2-02 容器成功/失败治理重启恢复与 S2-03 Trivy 双报告 runner-local release evidence 已分别在精确绑定 RC 的远程 Docker workflow 上通过并标记 `DONE`，S2-04 为 `BLOCKED`，Gate B/G2、Gate A/S3/G4 仍开放；S4-01 本地答辩可观测证据与 S4-02 Canonical 生命周期安全投影窄切片为 `DONE`，但 S4/Workflow E/P7/S7 保持 `IN PROGRESS` 且 Gate E/G5 仍开放；P3 Cloud 代码与远程 Emulator Gate 已通过但仍等待 Cloud Staging；P3e BubbleRAN 已完成锁定下载、适配、离线评估、受控回放、caller-owned 持久 checkpoint、持久 Canonical Fault 接收、本地治理 E2E 与 RC 制品证据，但每 source 独立 Incident、跨事件聚合、RCAEval 与完整发布终验仍待处理；Cloud Resolver、真实动作和统一 UI 仍待开发
> 目标主仓库：`NetworkAgent-dev`  
> 输入项目：`NetworkAgent-dev`、`telco-autonomous-networks-data-demo-main`

## 1. 结论与总体策略

两个项目可以重构为一个完备项目，但不应直接复制目录或并排运行两套 Incident/RCA 流程。目标应是以 `NetworkAgent-dev` 为主仓库，将第二个项目中已经验证的本地异常检测、规则驱动 RCA、中文交互、DeepSeek 模型适配和 DuckDB 运行能力，整合进现有的 Supervisor、Resolver、Engineer、MCP Tools、Fault Service、Spanner 和 Network Operator 体系。

统一平台应支持两种运行模式：

- **Local 模式**：DeepSeek + DuckDB + 合成 CSV + 模拟网络动作，不依赖 GCP，适合开发、演示和离线测试。
- **Cloud 模式**：Gemini 或 DeepSeek + Spanner + GKE/GitOps + Network Operator，处理实时指标、日志和真实网络资源。

两种模式必须共用领域模型、Agent 工作流、规则、审批策略和测试契约，只替换模型、数据与动作适配器。最终只保留一条 Incident 生命周期：

```text
指标/日志/测试故障
        ↓
检测、关联和去重
        ↓
Canonical Incident
        ↓
规则检索 → 证据采集 → RCA → 严重度 → 历史案例 → 报告
        ↓
人工审批（任何有副作用的动作）
        ↓
Engineer Agent → MCP → GitOps / Network Operator
        ↓
Tester Agent 验证 → Incident 关闭或重新调查
```

## 2. 目标与非目标

### 2.1 目标

1. 建立版本化、可审计的统一 Incident 数据模型和状态机。
2. 将 KPI 异常检测接入现有日志/故障触发机制，避免重复 Incident。
3. 将本地项目的多 Agent RCA 能力整合到现有 Resolver，而不是保留第二套 Resolver。
4. 通过端口/适配器隔离 DuckDB 与 Spanner、DeepSeek 与 Gemini、模拟动作与真实工程动作。
5. 保留 A2A 作为 Agent 间协议，MCP 作为数据和基础设施工具协议。
6. 在 Supervisor/Dashboard 中统一展示检测、RCA、审批、执行和验证过程。
7. 本地模式可以在没有 GCP 凭据的情况下完成端到端演示。
8. 云端模式可以从真实指标或故障开始，经过审批后安全执行网络变更。
9. 对 IMSI、MSISDN、IMEISV 等敏感字段实行最小化、聚合和脱敏。
10. 为关键流程建立离线、契约、集成、端到端和安全回归测试。

### 2.2 第一阶段非目标

- 不一次性重写所有现有 Agent、Dashboard 或 `install.sh`。
- 不在没有审批、幂等保护和回滚设计的情况下开放自主网络变更。
- 不让 DuckDB 和 Spanner 长期承担同一环境中的双主写入。
- 不在初期统一所有历史 UI；先保证协议和工作流统一。
- 不把模型生成结果当作事实来源；RCA 结论必须能追溯到工具证据。

## 3. 当前基线

| 维度 | NetworkAgent-dev | Telco Data Demo | 统一后的处理方式 |
|---|---|---|---|
| 主要职责 | 网络设计、部署、查询、测试、故障修复 | LTE KPI 异常检测与证据化 RCA | 前者作为平台，后者贡献分析能力 |
| Agent 框架 | ADK + LangGraph | ADK | 保留混合框架，通过 A2A 隔离 |
| Agent 协议 | A2A | 当前以 ADK 本地应用为主 | Detector/RCA 对外统一提供 A2A |
| 工具协议 | FastMCP/MCP | Python 本地工具 | 领域工具先抽象，远程能力通过 MCP |
| 模型 | Gemini 2.5 Flash | DeepSeek via LiteLLM | 增加按角色配置的 ModelProvider |
| 数据 | Spanner、GKE、日志、指标 | DuckDB、CSV、JSON、Markdown | Repository 接口 + Local/Cloud 适配器 |
| Incident | Fault Service + Spanner | DuckDB `incidents` | 单一 Canonical Incident 契约 |
| RCA | Strategy → Troubleshoot → Resolution | 规则、Analyzer、Severity、Docs、History、Report | 合并为统一 Resolver Pipeline |
| 动作 | Engineer → MCP → GitOps/Operator | 本地模拟动作 | ActionGateway + 强制审批 |
| UI | Dashboard/Incident/Portal | 中文本地 Web UI | Supervisor API 为统一后端，逐步合并 UI |
| 部署 | GCP/GKE/Cloud Run | 本地 Python | Local 与 Cloud 两套 Profile |

已验证基线：

- `telco-autonomous-networks-data-demo-main` 离线测试为 `10 passed`。
- 本地数据包含 13,440 条 Performance、579 条 Cell Trace；现有 DuckDB 中有 4 个 `NEW` Incident。
- `NetworkAgent-dev` 初始目录缺少 Git 元数据；现已恢复 GitLab 历史并建立可回滚开发分支。
- 两个项目的运行时并不兼容：主项目镜像主要使用 Python `3.13.2`、ADK `1.18.0`；本地 RCA 虚拟环境实际为 Python `3.12.4`、ADK `2.6.3`、LiteLLM `1.96.0`、DuckDB `1.5.5`。
- 主项目的 A2A SDK 也存在 `0.2.16`（Fault Service/Portal）与 `0.3.11`（主要 Agent）并存，必须通过协议契约测试后逐步统一。
- 本地 RCA 的 `pyproject.toml` 使用宽松下界且遗漏部分直接依赖，主项目各服务也存在依赖重复或依赖传递安装；合并前需要按服务补齐直接依赖并生成锁文件。

### 3.1 必须正视的业务语义差异

第二项目的样例是 LTE eNodeB/Cell 数据，核心 KPI 是 ERAB 成功率、Retainability 和 LTE Cell Trace；主项目运行 Free5GC/UERANSIM 5G SA 实验网络，现有 `NetworkMetrics` 主要保存 CPU、网卡吞吐等主机指标，另有 Service Performance 和进程/测试故障。

因此，两个项目当前只有“Incident/RCA 工作流”可以直接融合，数据不能硬映射。统一平台必须显式支持：

- `lte-demo`：保留现有 ERAB、Retainability、eNodeB/Cell 和合成 Trace 数据。
- `5g-live`：新增注册成功率、PDU Session 建立成功率、NG/RRC/认证失败、用户面连通性等 5G KPI/事件。
- `ResourceReference`：建立 eNodeB/Cell 或 gNB/NR Cell 到 Spanner Graph `NetworkNode`、Network Service 和 Engineer 目标资源的映射。

在 5G 原生遥测和资源映射完成前，LTE RCA 结论不得自动转换成主项目中的真实网络变更。

## 4. 设计原则

1. **领域优先**：先统一 Incident、Evidence、Recommendation、Approval、Action 和 Verification 契约，再迁移 Agent。
2. **单一生命周期所有者**：Resolver 负责从调查到验证的完整状态推进；Detector 只发现/提交候选事件。
3. **协议边界稳定**：Agent 间只依赖版本化 A2A 数据契约；Agent 对基础设施只依赖 MCP 或明确的 Repository/Gateway 接口。
4. **Local/Cloud 同构**：业务流程不包含 `if GCP` 分支，环境差异由依赖注入和适配器解决。
5. **只读与写操作分离**：证据采集默认只读；任何写操作必须进入审批、幂等、执行和验证链路。
6. **可追溯性**：所有结论记录使用的规则、工具、数据时间窗、模型、提示版本和执行结果。
7. **渐进迁移**：用可独立验收的纵向切片替代大爆炸式重写。
8. **兼容优先**：在 ADK 版本统一前，各 Agent 保持进程/容器隔离，避免依赖冲突扩散。

## 5. 目标架构

```text
                         ┌─────────────────────────┐
                         │ Dashboard / Local Web UI │
                         └────────────┬────────────┘
                                      │ Socket / REST / AG-UI
                              ┌───────▼────────┐
                              │ Supervisor Agent│
                              └───────┬────────┘
                                      │ A2A
             ┌────────────────────────┼────────────────────────┐
             │                        │                        │
      ┌──────▼──────┐          ┌──────▼──────┐         ┌──────▼──────┐
      │ Operations  │          │ Incident     │         │ Engineer /  │
      │ / Logs/Test │          │ Resolver     │         │ Tester      │
      └──────┬──────┘          └──────┬──────┘         └──────┬──────┘
             │                        │                         │
             │              ┌─────────▼─────────┐               │
             │              │ Unified RCA Core  │               │
             │              │ Rules/Evidence/   │               │
             │              │ Severity/History/ │               │
             │              │ Report/Approval   │               │
             │              └─────────┬─────────┘               │
             │                        │                         │
             └────────────────────────┼─────────────────────────┘
                                      │ MCP / Ports
                   ┌──────────────────┴──────────────────┐
                   │                                     │
          ┌────────▼────────┐                   ┌────────▼────────┐
          │ Local Adapters   │                   │ Cloud Adapters   │
          │ DuckDB/CSV/JSON/ │                   │ Spanner/GKE/Git/ │
          │ Markdown/Sim     │                   │ Operator/Logs    │
          └─────────────────┘                   └─────────────────┘
```

### 5.1 推荐的目标目录

```text
NetworkAgent-dev/
├── packages/
│   └── telco-domain/             # 独立、无 Agent/云框架依赖的领域与契约包
│       └── src/telco_domain/
│           ├── models.py         # Incident/Evidence/RCA/Approval/Action/Verification
│           ├── state_machine.py  # 合法转换、revision 与不可变更新
│           ├── contracts.py      # 版本化 A2A Data Part DTO
│           ├── ports.py          # Repository/Gateway Protocol
│           └── memory.py         # 确定性测试实现
├── lib/src/agent_library/
│   ├── model_providers/          # Gemini/DeepSeek/测试模型工厂
│   └── runtime_adapters/         # ADK/A2A/MCP 运行时适配，依赖 telco-domain
├── networkagents/
│   ├── incident_detector/         # KPI/日志候选事件检测 A2A Agent
│   └── resolver/
│       └── src/resolveragents/
│           ├── evidence/          # 证据采集
│           ├── rules/             # RCA 规则选择
│           ├── analysis/          # 根因分析
│           ├── severity/          # 严重度分类
│           ├── knowledge/         # 内外部资料、历史案例
│           ├── reporting/         # 报告生成
│           ├── approval/          # 动作审批
│           └── verification/      # 修复后验证
├── tools/src/
│   ├── ports/                     # Incident/Metric/Trace/Action 接口
│   ├── adapters/local/            # DuckDB、CSV、JSON、Markdown、模拟动作
│   └── adapters/cloud/            # Spanner、日志、GKE、Engineer A2A
├── data/
│   ├── samples/                   # 合成演示数据
│   └── rca-rules/                 # 版本化规则
├── ui/                            # 逐步统一后的事件/RCA 页面
└── tests/
    ├── contract/
    ├── integration/
    ├── e2e/local/
    ├── e2e/cloud/
    └── safety/
```

目录可在首个实现阶段微调，但“领域模型、Agent 编排、基础设施适配器”三层不得重新耦合。

## 6. 核心契约

### 6.1 Canonical Incident

至少包含：

- `schema_version`
- `incident_id`、`correlation_key`、`source_event_ids`
- `technology`（如 `LTE`、`5G_SA`）与 `vendor_profile`
- `status`、`severity`、`title`、`description`
- `affected_resources`（版本化 `ResourceReference`：位置、服务、节点、eNodeB/Cell、gNB/NR Cell）
- `detected_at`、`window_start`、`window_end`
- `violated_kpis`
- `evidence_refs`（不直接嵌入大体量或敏感原始数据）
- `hypotheses`、`root_cause`、`recommendations`
- `approval`、`action_runs`、`verification_runs`
- `model_metadata`、`rule_versions`、`trace_id`
- `created_at`、`updated_at`、`revision`

### 6.2 状态机

```text
DETECTED
  → TRIAGED
  → INVESTIGATING
  → RCA_COMPLETE
  → AWAITING_APPROVAL
  → REMEDIATING
  → VERIFYING
  → RESOLVED
  → CLOSED
```

异常分支：`DUPLICATE`、`REJECTED`、`FAILED`、`CANCELLED`、`REOPENED`。

状态变更必须满足：预期旧版本匹配、合法转换、幂等键唯一、审计事件写入成功。Cloud 模式采用 Spanner 事务；Local 模式采用 DuckDB 事务实现相同语义。

### 6.3 端口接口

- `IncidentRepository`
- `MetricRepository`
- `TraceRepository`
- `LogRepository`
- `RuleRepository`
- `KnowledgeRepository`
- `ModelProvider`
- `ApprovalService`
- `ActionGateway`
- `VerificationGateway`
- `EventPublisher`

Agent 不得直接导入 DuckDB、Spanner、GKE 或 Gitea SDK。

报告持久化确认与网络动作批准是两种不同的 `ApprovalDecision`，必须分别记录。提示词中的“逐步确认”只能改善交互，不能替代具有审批人、作用域、参数摘要、有效期和幂等键的持久授权。

### 6.4 A2A 与关联标识

共享契约包至少定义以下结构化 Data Part：

- `IncidentTrigger`
- `RcaRequest` / `RcaResult`
- `NetworkChangeRequest`
- `VerificationRequest` / `VerificationResult`
- `ApprovalDecision`

用户聊天可以使用文本，Agent 间业务数据必须使用带 `schema_version` 的结构化载荷。最终响应同时包含中文摘要和结构化 Artifact。

标识语义必须分离：

- `context_id`：同一工作流上下文。
- `incident_id`：领域 Incident，显式存在载荷中。
- `task_id`：A2A 任务实例。
- `trace_id`：跨 A2A、MCP、数据库和 Operator 的链路追踪标识。
- `idempotency_key`：一次业务意图的防重键。

不得继续让 `context_id` 同时承担聊天 Session、Incident 和 Trace 的含义。

## 7. 分阶段实施

> 估算为单名熟悉 Python/ADK/GCP 的工程人员的相对工作量，不是交付日期承诺。每阶段只有在验收证据写回本文档后才可标记完成。

| 阶段 | 目标 | 估算 | 当前状态 |
|---|---|---:|---|
| P0 | 基线、目标架构和决策冻结 | 1–2 人日 | **DONE** |
| P1 | 统一领域模型、接口和测试骨架 | 3–5 人日 | **DONE** |
| P2 | 本地 Detector/RCA 迁入主仓库 | 5–8 人日 | **DONE** |
| P3 | 接入 Spanner/MCP/实时事件 | 5–8 人日 | **IN PROGRESS** |
| P3e | 本地开放数据实验室：受控下载、脱敏、适配、离线评估与回放 | 3–5 人日 | **IN PROGRESS** |
| P4 | 合并并增强 Resolver Pipeline | 6–10 人日 | **IN PROGRESS** |
| P5 | 审批、真实修复和修复后验证 | 5–8 人日 | **IN PROGRESS** |
| P6 | 统一 Supervisor 与 UI 体验 | 4–7 人日 | NOT STARTED |
| P7 | 部署、CI、可观测性与安全加固 | 5–8 人日 | **IN PROGRESS** |
| P8 | 灰度切换、旧路径下线和发布验收 | 3–5 人日 | NOT STARTED |

### P0：基线与架构冻结

交付物：

- 保存两个项目当前测试、依赖、数据和主要调用链基线。
- 确认 `NetworkAgent-dev` 为目标主仓库并建立 Git 可回滚基线。
- 建立本计划、架构决策日志和风险清单。
- 明确 Local/Cloud 两种 Profile 的支持边界。
- 执行秘密扫描并处理 `setenv.sh`、凭据对象日志和明文 Git 登录风险。
- 记录 Apache 2.0 代码许可、DigitalRoute 合成数据说明以及 NetworkAgent CC-BY 资源归属。

退出标准：

- 目标架构与首个纵向切片得到确认。
- 主仓库可以创建功能分支或等价的可回滚快照。
- 所有未决策问题均有负责人或默认方案。

### P1：领域与接口基础

交付物：

- 实现版本化 Pydantic Incident、Evidence、RCAReport、Approval、ActionRun、VerificationRun 模型。
- 实现状态机、乐观并发、幂等和敏感字段过滤规则。
- 定义 Repository/Gateway Protocol 及内存测试实现。
- 定义 A2A `DataPart` 请求/响应 Schema 和兼容文本格式。
- 建立契约测试、状态机测试和序列化兼容测试。

退出标准：

- 可复用的 Repository/Gateway 契约测试套件已经建立，内存参考实现全部通过；DuckDB 与 Spanner 分别在 P2、P3 接入同一测试套件。
- 非法状态转换、重复事件和未审批动作会被确定性拒绝。
- 契约不依赖任何具体 Agent 框架或云 SDK。

2026-08-28 安全 Gate 复核：第一版 276 项离线测试虽已通过，但额外攻击性测试发现仓储 CAS 可绕过状态机、审批尚未绑定 Incident/RCA 版本、空 RCA/执行/验证记录可伪造阶段完成，以及历史审计集合可被替换。修复采用“先红测、再实现”：新增攻击性回归最初确定性失败，修复后在 Pydantic 2.5.3 与 2.13.4 环境中均为 315 项通过；独立复审逐条重放原 10 类阻断并给出 P1 Gate `PASS`。wheel 已完成构建及源代码树外导入验证。

### P2：迁入本地 Detector/RCA

执行拆分：

- **P2a（已完成）**：完成无 GCP、无 ADK、无模型 API 的确定性离线核心，即 CSV → DuckDB → 候选预览 → 显式确认/幂等创建 → 聚合证据 → 规则 RCA → 中文报告。RCA 在本阶段严格只读，不推进或持久化 Incident，也不执行任何动作；后续由唯一 Resolver 按审批状态机接管。
- **P2b（已完成）**：用独立运行环境包装标准 A2A Agent，验证 `working → input_required → 确认 → completed`、DataPart/TextPart 双输出和 Supervisor 跨版本传递。Assurance 运行时只依赖 A2A 0.3.11 与 P2a 领域/本地包，不引入 ADK、模型或云 SDK；旧 ADK FastAPI 前端不直接复制。

交付物：

- 迁移 DuckDB 初始化、CSV 导入、KPI 视图和本地规则/文档。
- 迁移 Incident Detector，并包装为标准 A2A Agent。
- 迁移规则选择、Analyzer、Severity、Docs、History 和 Report Generator。
- 将本地工具改为 P1 定义的端口实现。
- 增加 Local Profile 启动命令和无 API 的确定性测试模型。
- 为所有样例和规则标记 `technology=LTE`，禁止将 LTE 动作建议路由到 5G Engineer。

退出标准：

- 在无 GCP 环境下，从 CSV 检测异常、创建 Incident、生成 RCA 报告全链路通过。
- 现有第二项目的 10 项离线测试全部迁移或由更强的新测试替代。
- 原始 IMSI/MSISDN/IMEISV 不会出现在模型输入、日志或报告中。

P2a 验收证据（2026-08-28）：

- 新增独立 `telco-local` 包、显式 Local Profile 组合根与 `telco-local` CLI；共享领域层不引入 DuckDB/ADK/A2A/云 SDK，本地包也不加载云凭据、模型提供商或网络客户端。
- 完整合成资产固定为 13,440 条 LTE performance 与 579 条安全 Trace；扫描得到 15 个唯一 episode 候选（ERAB 2、retainability 13），扫描后 Incident 数仍为 0。
- Performance/Trace 只导入显式白名单列；Local LTE eNodeB/Cell 标识统一为 1–9 位 ASCII 十进制且限定在 `0..268435455`，导入前规范化、查询出站前再次校验，拒绝将无标签订户号伪装成网元标识。
- `confirm` 是唯一 Incident 写入口：提交前重扫并绑定规则内容、资源、时间窗、观测值和证据摘要；成功确认仅产生 1 条 Incident/1 条初始审计，同 key 精确重放不重复写，不同请求指纹确定性冲突。
- RCA 按历史规则版本和内容哈希精确解释，METRIC/TRACE fact 隔离，证据资源与时间窗严格匹配；不一致一律返回 `INCONCLUSIVE`。完整样例得到 `EXACT/CONCLUSIVE` 八节中文报告，但 Incident 仍为 `DETECTED` revision 0，报告、建议、动作均不落库。
- 两个 Pydantic 依赖矩阵均通过 `telco-local 149 + telco-domain 318 + Local E2E 1`，合计 468 项；两个 wheel 均完成构建、源码树外安装、`pip check`、导入隔离与 CLI 冒烟。安全复核结论见 [P2a Local Profile Gate 审计](security/p2a-gate-audit.md)。
- 提交 `e2a632b` 的远程验收已通过：[telco-local CI（Python 3.12/3.13、测试、wheel 与安装冒烟）](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33165120216) 与 [telco-domain CI](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33165120260) 均为 `success`。
- P2b 新增纯 A2A 0.3.11 `telco-assurance-agent`：`init` 与 `run` 分离，运行时不执行 DDL/导入，只允许 loopback 地址；AgentCard、JSON-RPC、流式状态、取消和只读 RCA 均通过真实 ASGI/HTTP 验收。
- 确认只接受服务端 challenge 与权威 DataPart。challenge 仅保存 SHA-256，绑定 task/context/workflow/trace/candidate/snapshot；预览与拒绝零写，确认后唯一写，崩溃重启使用同一业务幂等指纹精确重放且 revision/history 不增长。
- DuckDB Task/Pending Store 使用 schema `1.1`，支持从 `1.0` 显式迁移；未完成 claim 与孤立 `input-required` Task 有界回收，存在持久 Incident 写时仅在 challenge 到期后保留 15 分钟精确恢复窗口。
- Supervisor 只把 TextPart 用于展示，DataPart 使用 exact allowed/required schema、大小/深度/隐私与关联标识校验；A2A 事件仅单播到 thread 所属 Socket，流结束、错误响应、错误 task/context 或未知字段均 fail closed。
- Dashboard 审批回程只接受服务端记录的 `requestTaskApproval` 调用；普通 `agui_message` 的 ToolMessage 被拒绝，ADK continuation 保留真实 FunctionResponse 工具名，持久化或消费失败可安全重试，不再吞掉续跑事件。
- 本地发布门禁：既有领域/Local 回归 476 项、Assurance 26 项、真实 HTTP E2E 4 项、A2A 0.3.11/legacy wire 兼容 70 项、精确 ADK 1.28.1 Supervisor 57 项全部通过；三个 wheel 已完成源码树外安装与 `pip check`。安全结论见 [P2b Assurance/A2A Gate 审计](security/p2b-gate-audit.md)。
- 提交 `974db42` 的远程验收已通过：[Assurance CI（Python 3.12/3.13、Supervisor ADK 1.28.1、legacy wire、真实 HTTP、三 wheel）](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33179490152) 与 [Local Profile 回归 CI](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33179490104) 均为 `success`。
- P2 的退出标准已经满足并标记为 `DONE`；P3 将接入 Spanner/MCP/实时事件，P6 再收敛统一 RCA 展示，P7 才允许在认证、TLS 与部署加固后扩展到非本机网络。

### P3：接入云端数据与事件

执行拆分（2026-08-30 更新）：

- **P3a（代码与远程 Emulator Gate 完成）**：已建立 Canonical Spanner v2 表族、事务型 Incident/Telemetry/Event/Outbox Repository、审计/幂等/source-event 关联和三实现共享契约；旧 `Incident` 表保持只读，不原地更名或长期双写。
- **P3b（本地完成）**：已建立严格 Pub/Sub push Fault Ingress，以同一 Spanner 事务写 Inbox + Incident/Audit + SourceAssociation + Outbox；默认 `shadow`，仅 durable success/replay 返回 2xx，瞬时故障返回可重试状态，poison message 交给订阅 DLQ。
- **P3c（本地完成）**：已建立与 Engineering 写工具分离的独立 FastMCP 服务，只注册六个只读 Canonical Incident/KPI/Evidence/Resource 工具，并在出站边界执行逐项与累计预算。
- **P3d（本地完成）**：已建立 checksummed Canonical DuckDB → Spanner 一次性迁移；运行时/导出无 DDL，保留完整来源证明，重放交叉核验持久组件。legacy Spanner 语义不明确的行只读保留并进入人工映射队列。
- **P3e（IN PROGRESS）**：新增本地开放数据实验室。现有 P3c 已由只读 Cloud MCP 占用，因此开放数据工作正式编号为 P3e，不复用 P3c。当前已完成安全 catalog/lock、许可证据与归属锁定、显式许可下载、BubbleRAN 精确 schema CSV/JSON 适配、候选有界的离线评估、immutable label-free `ReplayPlan` 和公开 `ReplayWirePayload`。loopback transport 现支持立即投递与单调 paced runner，只对显式启用的 network/timeout 瞬时失败做有限重试，并保留 deadline/cancel 证据；可选 caller-owned store 以严格 plan-bound JSON、原子替换和非阻塞单-writer 锁持久化 checkpoint。Assurance `POST /local/v1/faults/replay` 只在 Canonical Incident/source association 持久后返回 202；每 source 独立 Incident，精确锁定的 5G SA BubbleRAN `ran.mac.ul_bler > 0.15` 只使用服务端规则版本和内容摘要作 provenance，不得外推生产。RCAEval 与多源/跨事件聚合仍未实现。
- 共享 Repository 已统一：source event 全生命周期只属于一个 Incident；新关联不增加 revision；分裂 selector fail closed；`CLOSED → REOPENED` 在同一事务重新获取全部活动键。
- 本地发布矩阵已通过：领域 323 项、Local 166 项、共享 Repository 合同 2 项、Cloud/迁移/FGAC 146 项、Fault Ingress 与 Cloud MCP 101 项；P2b Assurance 26 项和 Local E2E 1 项也保持绿色。Spanner 迁移与常规生命周期均已覆盖 1,000 条来源的批量边界。提交 `fa07096` 的 [Cloud CI run 33202370157](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33202370157) 已在 Python 3.12/3.13 上分别通过真实 Spanner Emulator 对象 DDL、共享 Repository、50 路并发、迁移重放、1,000 条来源、reopen 与 Inbox/Outbox 验收，并完成五 wheel 源码树外安装、四 CLI 冒烟和 `pip check`。

Sprint 1 当前远程回归绑定 release candidate `7cbff490ccb71befb42c7cd30204f7f88e3b2f38`；下列三个成功 run 的 `headSha` 均精确等于该 SHA：[Assurance CI run 33308634938](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634938) 的 4 个 job、[Data Lab CI run 33308635073](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308635073) 的 3 个 job，以及 [Local CI run 33308634955](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634955) 的 2 个 job 全绿。本地独立 Gate C/D 证据为 Assurance `76 passed`、local-stack `22 passed`、Local E2E `3 passed`、A2A contracts `33 passed` 与 A2A E2E `4 passed`；Data Lab + Lab E2E 双 Pydantic 各为 `222 passed, 1 skipped`。

Assurance、Data Lab、Local 的 Python 3.12 jobs 分别发布 [artifact 9731341117](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634938/artifacts/9731341117)、[artifact 9731281738](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308635073/artifacts/9731281738) 与 [artifact 9731294281](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33308634955/artifacts/9731294281)，保留 14 天并分类为 `VERIFIED RC`；archive digest 依次为 `30cee4d4ca7c8e7d09cdde27449a8165a5e1da3e16efa8dc0fc30c4af44d454e`、`2e314321e990f38ef82696a6df78fe9f11538f6c582996004d4b66d2d11a2231`、`adee5fba5887a4d61a4f59fba9a946c8d211038144095918e3045a6f56b0bee0`。runtime inventory / dependency audit / SBOM component 计数分别为 Assurance `34/0/38`、Data Lab `5/0/7`、Local `7/0/9`。canonical wheels 为 Domain 32,547 bytes / `53f0b118041c5897d4e01813b777744263f849894f6c211cc67cb9df41fd104e`，Lab 74,425 / `4c646e7ad618884284bf5f0b484b579c19dbcaccc8ef01571eccfc4ea197d900`，Local 66,728 / `f86b66dbd9a157ca0ecbdb0fb1d63743f48fc96195a12629e01780f809dd7e3f`，Assurance 56,893 / `9f7d47ea0c45d2a01a60a5a726055a7368f3d2cf86d4d8a8ac1445bde08ce96d`。

本次证据回填提交晚于且不等于受测 RC。S1-01..07、Workflow C/D 与 Sprint 1 均为 `DONE`。Sprint 2/Workflow B 保持 `IN PROGRESS`。S2-01 已标记 `DONE`：本地政策/制品门禁为 `75 passed, 1 skipped`，精确绑定 RC `d0a020fb7a5d8a33cd136cd18917d21b7e067946` 的 [telco-container run 33311995755](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755) 两个 job 全绿，远程 Linux 政策门禁为 `76 passed, 0 skipped`。runner 完成真实 Compose、build/inspect、应用层与合并 rootfs 扫描、初始化、health/隔离/shared-loopback smoke 和 probe steps；probe 无 stdout，reset 删除 state/artifacts/marker 且 `workspace_removed=true`，cleanup 成功。本地构建 image ID `sha256:0acef50a2ee7978ea67a8b37582a19698a21b1303451ce37a0a569d48fef6cff` 不是 registry digest。

S2-02 也已标记 `DONE`：精确绑定 RC `d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4` 的 [telco-container run 33314782750](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750) 两个 job 99266075811/99266104885 全绿，Linux 政策门禁 `128 passed`；真实成功/故意验证失败分支分别到 `RESOLVED`/`REOPENED`，且均为 `restart_observed=true`、`exact_replay=true`、`real_network_side_effects=false`；顶层 `projects_removed=true` 证明两个 Compose 项目均已清理。同一 RC 的 [Local CI run 33314782757](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757) 两个 job 99266075954/99266075805 全绿，两版 Python 各通过 Domain + Local `518 passed`、local-stack `29 passed, 2 skipped`、Local E2E `2 passed`；Python 3.12 [VERIFIED RC artifact 9733117877](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757/artifacts/9733117877) archive digest 为 `sha256:3d557a52a80960add94c04b443c14f613892701c5b5b93dcfba4174fd78f3469`。

S2-03 也已标记 `DONE`：精确绑定 RC `68b16ea528a85b743aa8c05044948bac195ee8ec` 的 [telco-container run 33320667296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296) 两个 job 99281949020/99281979960 全绿，并发布 14 天 [artifact 9734817516](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296/artifacts/9734817516)；其名称为 `telco-container-release-attempt-1`，分类 `VERIFIED RUNNER-LOCAL EVIDENCE`，archive digest 为 `sha256:e35d8eb12484feeb474477bae0f3d937019f3ab19e9f8ccf1fd491b8a95f0394`。该 artifact 绑定 runner-local image/config digest `sha256:0e8caa8418d93e1f9654655b84331723e8223d9dc94e66274aed1ca3fa7d00bb`、Trivy 0.74.0 fixable Critical/High gate `0/0`、full diagnostic `5` Critical + `29` High 且全部 unfixed，以及 CycloneDX 1.7 SBOM `145` components。

S2-01/S2-02 的 container runs 上传 0 artifacts，保留为历史行为证据；当前容器供应链状态以 S2-03 为准。但 S2-03 artifact 仍不是 container registry artifact：未发布 registry image/digest，未提供签名、attestation、provenance 或 Trivy DB OCI digest/signature，且由于未上传镜像、scanner binary 与数据库，不能离线独立重验。因此 Gate B、G2、Gate A、S3 与 G4 仍开放。上一 RC 的 [Cloud CI run 33301104595](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104595) 仅保留为历史 Emulator 证据，其 `headSha` 不等于当前 S2-03 RC。P3e 与 P3 总阶段继续 `IN PROGRESS`：A2A 协作式 timer/后台任务、每 source 独立 Incident、hash-locked 离线安装、SPDX、独立 secret/SAST/license policy、RCAEval、跨事件聚合和 Cloud Staging 均未完成。

交付物：

- 实现 Spanner Incident、Radio KPI、Safe Evidence、Resource Mapping、Inbox/Outbox Repository。
- 将新的 Pub/Sub Fault Ingress 输出转换为严格 Canonical Event；legacy Fault Service 仅作为 `legacy`/回滚路径，不扩展其 0.2.16 A2A 主链。
- 增加事件驱动 KPI 检测端口并统一走事务关联/去重服务；Outbox 在 P4 前不派发给 legacy Resolver。
- 通过独立只读 MCP 暴露 Incident、审计、证据、KPI 和资源映射，不复用 legacy 写工具或任意 SQL。
- 为 Canonical DuckDB 与 Spanner 建立一次性导入/导出工具，不使用长期双写；legacy 数据禁止猜测映射。
- 定义 5G PM/Trace 数据契约和 `ResourceReference` 映射；不能把现有主机 `NetworkMetrics` 当作 LTE/5G 无线 KPI。
- 通过容量、保留期和查询基准决定高频 Trace/PM 明细使用 Spanner、BigQuery 或其他分析型存储；Agent 只依赖 `TelemetryRepository`。
- P3e 以 catalog/lock 驱动的本地端口接入许可明确的开放数据；原始大文件仅进入 `.local/telco-lab` 摘要锁定缓存，Git 只保存 catalog、适配器和代码生成的极小脱敏 fixture。
- P3e 首批政策白名单为 BubbleRAN（CC BY-SA 4.0）、NIST RAN Anomalous State（NIST Open Data）、RANalyzer（CC BY 4.0）、TelecomTS（MIT）和 RCAEval（MIT）；许可不清或无法固定版本的数据默认禁用。
- P3e 先交付 BubbleRAN Detector 与 RCAEval 多源 RCA 两个互补纵向切片，再扩展其余白名单；完整设计见[本地开放数据实验室](local-data-lab.md)，发布边界见[P3e Data Lab Gate](security/p3e-data-lab-gate.md)。
- P3e 的回放计划只接受已验证且与 lock 精确绑定的 bundle、审批过的 adapter 字段/单位投影、显式 loopback URL 和 `disabled|simulate`；限制事件数、速率、时长、单项/累计载荷、资源数、并发与倍速，并为重复、乱序和断点续传测试提供稳定序列。仓内 transport 已能以公开 wire 契约执行立即或单调节奏 loopback HTTP 投递，并在默认零重试基础上可显式选择有限 network/timeout transient retry。caller-owned store 在有效 202/204 后先原子保存再推进，拒绝损坏/跨 plan/回退/多 writer/路径与 Windows drive 越界；真实 TCP E2E 已验证 checkpoint 重启零投递。它仍不是接收端签名 ACK，response loss 依赖 receiver 幂等，POSIX mount 与同用户 TOCTOU 属本机文件系统信任边界；当前也无跨事件聚合或真实动作。

退出标准：

- 同一故障从日志、测试和 KPI 多次到达时只产生一个活动 Incident。
- 云端证据查询具有时间窗、资源范围和结果大小限制。
- Repository 契约测试在 DuckDB 与 Spanner 测试环境均通过。
- Linux CI 中真实 Spanner Emulator 完成对象 DDL、事务、并发、reopen、迁移重放和 Outbox 验收；Cloud Staging 的 FGAC/OIDC/DLQ 结果单独回填，不以 Emulator 代替。
- P3e 的 BubbleRAN 与 RCAEval 完成精确版本/许可/校验和锁定、隐私扫描、Canonical 适配、确定性离线评估和 loopback 受限回放；仓库与发布 wheel 不包含第三方原始数据。
- P3e 即使完成也不能替代 Cloud Staging 的 IAM/OIDC、Pub/Sub DLQ 和 Workload Identity 验收，P3 总阶段仍以两个 Gate 各自真实证据判断。

当前 P3e 尚未满足退出标准：BubbleRAN 已交付确定性计划、公开 wire、loopback transport、paced runner、有界 transient retry、caller-owned 持久 checkpoint、Canonical Fault 持久接收器、真实 TCP 治理 E2E 与远程 RC artifact/SBOM/`pip-audit` 证据；但跨事件聚合、RCAEval 纵向切片、独立答辩、签名/attestation 与完整发布终验尚未完成。因此 P3e 保持 `IN PROGRESS`。

### P4：统一 Resolver Pipeline

Local 子切片（2026-08-30 已完成）：新增无框架的
`LocalGovernanceEngine`，在同一 Canonical Incident/DuckDB Repository 上可恢复地推进
`DETECTED → TRIAGED → INVESTIGATING → RCA_COMPLETE → AWAITING_APPROVAL`。
确定性 RCA 必须绑定 Incident revision、包含本地证据；仅 `CONCLUSIVE` 报告可由独立策略附加一个固定
`LOCAL_SIMULATION` 建议，`INCONCLUSIVE` 停在 `RCA_COMPLETE`。每一步使用派生幂等键，已提交但响应丢失时可安全续跑。

该子切片验证了 Resolver 编排骨架，但没有合并现有 Strategy/Troubleshoot/Resolution Agent、模型调用、Cloud checkpoint 或 A2A Outbox 消费，因此 P4 总阶段保持 `IN PROGRESS`。

目标流程：

```text
Triage
 → Rule Selection
 → Evidence Collection
 → Hypothesis/RCA
 → Severity
 → Knowledge/Prior Incidents
 → Report
 → Remediation Proposal
```

交付物：

- 将现有 Resolver 的 Strategy/Troubleshoot 能力与本地 RCA 子 Agent 合并。
- 去除重复 Incident 状态和重复数据库写入逻辑。
- 每个阶段输出结构化结果，同时生成简体中文用户摘要。
- 持久化 Checkpoint，使 Cloud Run 重启后可以恢复。
- 记录模型、提示、规则、工具调用和证据引用版本。

退出标准：

- 相同 Incident 重放不会产生重复动作或重复报告版本。
- 任一阶段失败都能得到可恢复状态和明确错误原因。
- 最终报告中的事实均可追溯到证据引用。

### P5：审批、修复与验证闭环

Local 子切片（2026-08-30 已完成）：`prepare` 只创建 append-only `PENDING` 审批并返回动作哈希与 Incident revision；用户必须在另一次 `decide` 调用中同时提交精确 `expected_action_hash`、`expected_revision`、操作者与非空理由。审批默认 15 分钟、最多 24 小时，执行前由可信 Repository 与时钟重新解析最新批准。拒绝、过期、hash/revision 变化或幂等载荷冲突均不会创建 `ActionRun`。

批准后只能执行固定参数、低风险、无外部 I/O 的 `LOCAL_SIMULATION`，随后生成本地 `TEST_RESULT` 证据：通过进入 `RESOLVED`，失败进入 `REOPENED`。该切片没有真实 Engineer A2A、MCP 写工具、GitOps/Operator、网络 dry-run 或生产回滚能力，因此 P5 总阶段保持 `IN PROGRESS`，不能用本地 Gate 代替 Cloud Staging 验收。

交付物：

- 建立不可伪造、具有作用域和有效期的审批记录。
- 将模拟 `Action Executor` 替换为 `ActionGateway`；Cloud 实现通过 A2A 请求 Engineer。
- Engineer 的计划确认与 Resolver 的动作审批合并为一次明确且可审计的用户决策。
- 写操作必须先生成 dry-run/diff；审批绑定动作参数哈希，任何参数或作用域变化都必须重新审批。
- 增加幂等键、超时、重试边界、补偿/回滚说明和最大动作范围。
- 修复后调用 Tester/Operations 验证，失败则自动转入 `REOPENED` 或人工接管。

退出标准：

- 未审批、审批过期、参数变化或作用域扩大时无法执行写操作。
- 重复消息不会重复执行网络变更。
- 每个修复动作都有前后状态、执行结果和验证结果。

### P6：统一 Supervisor 与 UI

交付物：

- Supervisor 可路由 Detector、Resolver、Engineer、Tester，并保持同一会话上下文。
- Dashboard 增加 Incident 列表、RCA 时间线、证据摘要、审批卡片和验证状态。
- 保留中文交互；界面不展示模型私有思维链，只展示阶段、工具、证据和结果。
- 评估本地 Web UI：将其作为 Local Profile 壳层，或在功能等价后下线。

退出标准：

- 用户可以从一个界面完成“发现 → 调查 → 审批 → 修复 → 验证”。
- 页面刷新、断线重连和 Cloud Run 重启不会丢失任务状态。
- 审批对象、参数、风险和预期影响对用户可见。

### P7：部署、CI、可观测性与安全

Local 部署子切片（2026-08-30 已完成）：新增
`tools/local-stack/local_stack.py` 作为跨平台 JSON 入口，提供 `doctor`、`init`、`status`、`demo`、前台 `serve` 和显式 `reset --yes`。工作区必须显式指定且由 marker 所有，仓库内只允许 `.local` 子目录；root/home/repository、symlink/junction/reparse、UNC/device、非固定 Windows 驱动器和 unowned 路径失败关闭。服务固定绑定 `127.0.0.1`，只允许 `ACTION_MODE=disabled`，动作演示默认禁用且只能显式切换为 `simulate`。Assurance 另提供直接 loopback 的 `healthz/readyz/version`：liveness 不读依赖，readiness 以 1 秒预算做一次 Repository 读，失败/超时/已有卡住 worker 固定 503，版本端点只返回未签名 allowlist 元数据；所有标准非 GET 方法使用固定有界 JSON 405 契约，HEAD 按 HTTP 语义省略 body。三者都不证明 Cloud readiness。Local CI 已纳入 stack 安全测试、治理单元/E2E 与公共导出冒烟。

S2-01 安全容器基线（2026-08-30）已冻结 digest-pinned 多阶段镜像、numeric non-root、只读根、`cap_drop: ALL`、`no-new-privileges`、资源/tmpfs 限制、只读输入 manifest 与 named workspace volume。`assurance/init/reset` 使用 `network_mode: none`，`probe/smoke` 使用 `network_mode: service:assurance` 共享直接 loopback；无 ports/expose、bridge 或反向代理。本地 Python 门禁为 `75 passed, 1 skipped`（Windows symlink 条件 skip），Black/flake8/YAML/JSON/diff clean；本机仍无 Docker/actionlint。精确绑定 RC `d0a020fb7a5d8a33cd136cd18917d21b7e067946` 的 [telco-container run 33311995755](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755) 已通过远程 Linux `76 passed, 0 skipped`、compose-policy job `99258612862` 与 build-inspect-smoke job `99258640065`：验证真实 config/build/inspect、5 层 / 2,570 成员应用层扫描、9,148 成员合并 rootfs 扫描、初始化 `13440/579/0` 且 `external_access=false`，以及 health/隔离/shared-loopback smoke 和 probe steps。probe 无 stdout；reset 删除 state/artifacts/marker 且 `workspace_removed=true`，随后 cleanup 成功。本地构建 image ID `sha256:0acef50a2ee7978ea67a8b37582a19698a21b1303451ce37a0a569d48fef6cff` 不是 registry digest。

S2-02 容器治理恢复（2026-08-30）在精确绑定 RC `d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4` 的 [telco-container run 33314782750](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750) 通过 compose-policy job 99266075811 与 build-inspect-smoke job 99266104885，Linux 政策门禁为 `128 passed`。两个独立 Compose 项目分别完成 `RESOLVED` 成功分支和 `REOPENED` 故意验证失败分支；两者均证明 Assurance 重启、原 prepare/decide/execute 请求 exact replay 与 `real_network_side_effects=false`，顶层 `projects_removed=true` 证明两个 Compose 项目均已清理。同一 RC 的 [Local CI run 33314782757](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757) 两个 job 全绿，两版各为 Domain + Local `518 passed`、local-stack `29 passed, 2 skipped`、Local E2E `2 passed`；Python 3.12 `VERIFIED RC` artifact 9733117877 的 archive digest 为 `sha256:3d557a52a80960add94c04b443c14f613892701c5b5b93dcfba4174fd78f3469`。

S2-03 容器 Trivy 双报告证据（2026-08-30）在精确绑定 RC `68b16ea528a85b743aa8c05044948bac195ee8ec` 的 [telco-container run 33320667296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296) 通过 compose-policy job 99281949020 与 build-inspect-smoke job 99281979960。该 run 发布 14 天 `VERIFIED RUNNER-LOCAL EVIDENCE` artifact 9734817516，archive digest 为 `sha256:e35d8eb12484feeb474477bae0f3d937019f3ab19e9f8ccf1fd491b8a95f0394`；其绑定 runner-local image/config digest `sha256:0e8caa8418d93e1f9654655b84331723e8223d9dc94e66274aed1ca3fa7d00bb`、Trivy 0.74.0 fixable Critical/High gate `0/0`、full diagnostic `5` Critical + `29` High 且全部 unfixed，以及 CycloneDX 1.7 SBOM `145` components。

S2-04 基础镜像关闭评估（2026-08-31）在相同 Trivy 0.74.0/冻结数据库下实扫四个候选。没有候选同时保持现有 CPython 3.12、glibc、公开可取得和 provenance 可追溯契约，并达到完整 Critical/High `0/0`；忽略 unfixed、漏洞白名单或丢弃包身份均不接受。因此 S2-04 为 `BLOCKED`，S2/Workflow B、Gate B 与 G2 继续开放。

S4-01 本地答辩可观测证据（2026-08-31）已由精确绑定 RC `cb4a4e7191f67aa71ef980668352d55001e23142` 的 [Local run 33330915665](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665) 验收。Python 3.12/3.13 jobs 99309192438/99309192337 均成功；每版为 Domain + Local `518 passed`、local-stack `66 passed, 2 skipped`、Local E2E `2 passed`，3.12 release boundary 为 `18 tests passed`。两版核对 22 个有界进程内阶段事件、诊断性单次时序、只使用 `branch/error_class/outcome/stage` 的报告内指标聚合、四项报告内固定告警、隐私字段与 `propagated_trace=false`。579 条安全 Trace rows 是输入数据记录，不是 OpenTelemetry span。

Python 3.12 [VERIFIED RC artifact 9737683310](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665/artifacts/9737683310) 为 106,309 bytes，archive digest `sha256:5b26c3ccaff8e57c10c4bb3375ebbad8156d151e26158b1123639e3423006eca`，到期时间 `2026-09-13T19:33:25Z`。独立下载 11 文件闭包与 10 条 manifest 记录加 manifest 自身精确一致；defense/observability supplemental evidence 分别为 3,379/9,178 bytes，SHA-256 分别为 `14f04bf556f03fd7c22edf0272240dba566610466546362442abdab3dd06a9b7` / `2741c3a25983056a73ea0bcd6ea99ffc14bf83dbd6209e4a9811b93c0a98df49`。该 RC 只有 Local workflow 因路径规则触发，不存在同 SHA Data Lab/Assurance 回归。

S4-02 Canonical 生命周期安全投影（2026-08-31）已由精确绑定 RC `69643e8a6f79b1264d60e5517eeb9a24035c8e7d` 的 [Local run 33336341831](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831) 验收。Python 3.12/3.13 jobs 99323794962/99323795037 均成功；每版为 Domain + Local `576 passed`、local-stack `89 passed, 2 skipped`、Local E2E `2 passed`，3.12 release boundary 为 `18 tests passed`。双分支各投影 8 个连续 revision group / 14 个 allowlisted 事件，分别为 `RESOLVED/PASSED` 与 `REOPENED/FAILED`，并核对 `read_only=true`、精确绑定、单执行尝试、exact retry、双 cleanup、`side_effects=false`、隐私边界和 `distributed_trace=false`。

Python 3.12 [VERIFIED RC artifact 9739212391](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831/artifacts/9739212391) 名称 `telco-local-release-py3.12-attempt-1`，115,482 bytes，archive digest `sha256:237bcb207e29e53aba6c907f94efcb1999b77b1640f59dd95b8d3c9c30b27aa7`，到期时间 `2026-09-13T21:30:29Z`。独立下载 12 文件闭包与 11 条 manifest 记录加 manifest 自身精确一致；第三个 supplemental evidence `local-lifecycle-summary.json` 为 8,431 bytes / SHA-256 `5726b4eec6f0c4621a3d804b0f6973a24d41ae14ea1878b2e57205a299966e45`，重建持久报告为 8,290 bytes / SHA-256 `21528fd8694da0cb0c51452b14c45af864b24951282475367addbcd1b74fa004`。同一 SHA 的 Assurance run 33336341877、Container run 33336341805 与 Cloud run 33336341859 全绿，Data Lab 未触发；Cloud workflow 仍只是 CI/Emulator 证据，不是 Cloud Staging 或生产验收。

因此 S2-01、S2-02、S2-03 与 S4-01、S4-02 窄切片均为 `DONE`，S2-04 为 `BLOCKED`，但 S2/Workflow B、S4/Workflow E、P7 与 S7 总阶段保持 `IN PROGRESS`。Gate B、G2、Gate A/S3/G4、Gate E/G5、统一 Dashboard 和 Cloud 组合部署仍未通过。S4-01 只有报告内诊断事件、聚合与告警求值；S4-02 只有 durable Canonical records 的只读、revision-grouped 投影。运行时结构化日志、跨组件 trace、OpenTelemetry export/Collector、Prometheus、外部告警、SLI/SLO、Collector 故障容忍、备份/恢复和完整故障演练仍未交付。

交付物：

- 锁定 Python 与依赖版本；在兼容性验证后统一 ADK 版本。
- 补齐每个服务实际导入的直接依赖，生成按服务锁文件和全仓约束文件，禁止仅依赖传递安装或无上界漂移。
- 为 Local Profile 提供一条启动入口，为 Cloud Profile 提供独立可组合部署脚本。
- 避免继续扩张单体 `install.sh`：新组件采用可独立调用脚本，并由其编排。
- CI 覆盖静态检查、单元、契约、Local E2E、容器冒烟和安全测试。
- 建立跨 Agent `trace_id`、结构化日志、指标、成本和延迟监控。
- 密钥全部进入 Secret Manager/环境注入；删除明文 Git 密码路径。

退出标准：

- 新环境可以按文档重复启动 Local Profile。
- Cloud 预发布环境可以一键部署/回滚相关组件。
- 日志不得包含密钥、原始用户标识或不受控的模型上下文。
- 关键 SLO、告警和 Runbook 已定义并演练。

### P8：灰度与旧路径下线

交付物：

- 影子模式运行新 Detector/RCA，只比较结果不执行动作。
- 对 Incident 数量、严重度、根因、建议和延迟进行新旧路径对比。
- 逐步开启人工审批后的真实修复，设置 Kill Switch。
- 迁移必要数据，归档第二项目和重复 Resolver 路径。
- 完成演示脚本、操作手册、故障手册和发布说明。

退出标准：

- 连续灰度窗口内无重复修复、未授权写入或关键 Incident 遗漏。
- 回滚演练通过。
- 旧路径不再接收新事件，且历史数据仍可查询。

## 8. 首个最小纵向切片

第一个可运行切片应利用现有 Dashboard 和 Supervisor，避免先重写 UI：

```text
现有 Flutter Dashboard
 → Supervisor
 → Assurance A2A 0.3.11（无 ADK、无模型、独立运行环境）
 → 本地 Performance CSV / DuckDB
 → 展示候选并请求确认（A2A input_required）
 → Canonical Incident
 → 中文结果返回现有 Dashboard
```

该切片已完成候选展示与显式确认，且不执行修复；它使用最终领域契约、端口和 A2A 消息格式，验证了 A2A Agent Card、流式状态/`input_required`、状态传递、幂等、数据脱敏和 Supervisor 路由。用户未确认不得写入；重复确认只能产生一个 Incident；task/context/workflow/trace/idempotency 各自保持独立语义。

第二个本地切片“Canonical Incident → 确定性只读 RCA → 中文报告”也已由 Assurance A2A 的 `assurance_analyze_request` 完成；报告不落库、不生成动作、不推进 Incident。随后按 P3–P5 分别接入 Spanner、统一 Resolver、Engineer 和 Tester，避免同时调试云数据、模型和真实写操作。

第三个本地切片已用仓库级 `local-stack` 完成“LTE CSV → Detector → 显式 Incident 确认 → 持久 RCA → hash/revision 双绑定审批 → `LOCAL_SIMULATION` → 本地验证”。通过时 Incident 为 `RESOLVED`，失败时为 `REOPENED`，重复执行不增加 action/verification/history。它是隔离、无网络副作用的本地治理验收链，不替代第二个切片的 A2A 用户体验，也不代表 Cloud Resolver/Engineer/Tester 已接通。

## 9. 数据迁移与一致性

1. `incidents` 以 Canonical Incident 为源模型，DuckDB/Spanner 分别映射，禁止在 Agent 中拼 SQL。
2. Canonical DuckDB schema `1.1` 通过 checksummed 一次性 bundle 导入 Spanner v2，保留 Incident 快照及每条 SourceEventAssociation；不启用长期双写。
3. legacy Spanner `Incident` 缺少 Canonical revision/provenance/状态语义，P3 不自动猜测；旧表只读保留，带 `legacy_source`/`legacy_id` 的候选进入可追踪隔离清单，待人工批准映射。
4. `correlation_key` 建议由资源范围、故障类别和时间桶组成，并允许规则化扩展。
5. 原始指标、Trace 和日志保留在各自数据源；Incident 只保存摘要和引用。
6. 报告采用不可变版本，审批绑定具体 `report_version` 和 `action_hash`。
7. 所有时间统一为 UTC 存储，UI 按用户时区展示。
8. Local 与 Cloud 模式每次只配置一个写入型 `IncidentRepository`。

## 10. 模型与 Agent 兼容策略

- 第一阶段不强行把所有服务装进同一个 Python 环境；继续使用服务/容器隔离。
- 共享 `contracts/domain` 包不得依赖 ADK、A2A SDK、FastMCP、LangGraph 或云 SDK；框架适配代码留在各服务内。
- 为 Agent 角色配置模型，不在业务代码写死 `gemini-2.5-flash` 或 DeepSeek 型号。
- `ModelProvider` 至少支持 Gemini、DeepSeek、Fake/Replay 三种实现。
- 提示词、输出 Schema 和安全策略独立版本化。
- Supervisor 已精确锁定并验证 ADK `1.28.1`；其余仍锁定 `1.18.0` 的 legacy Agent 继续进程隔离，完成同类回归门禁前不得作为新增外部入口。
- 实际目标版本需以兼容性矩阵为依据；不能因为第二项目当前解析到 ADK `2.6.3` 就直接全仓升级。
- LangGraph Engineer 可以继续独立演进；只要求遵守 A2A 合同。
- 生产中禁止依赖自由文本解析推进状态，关键输出必须通过 Pydantic Schema 校验。

## 11. 测试与质量门禁

### 11.1 测试层级

- **单元测试**：KPI 计算、规则选择、相似度、状态机、脱敏、幂等、审批。
- **契约测试**：DuckDB/Spanner Repository、A2A 消息、MCP 工具、模型结构化输出。
- **Agent 场景测试**：固定输入与 Fake/Replay 模型，验证工具顺序和状态推进。
- **Local E2E**：CSV → Detector → Resolver → Report → 模拟审批/动作/验证。
- **开放数据回放测试**：verified lock/bundle → 无标签 `ReplayPlan` → 公开 wire → paced loopback transport → durable Canonical Incident → 本地治理；分开验证重复/乱序/续传、截止/取消和 settled exact replay 零写。
- **Cloud 集成测试**：测试 Spanner、Cloud Run A2A、MCP、Engineer、Operator 沙箱。
- **安全测试**：提示注入、越权工具、审批绕过、敏感数据泄漏、重放攻击。
- **韧性测试**：超时、重复事件、断线、部分失败、服务重启和模型限流。

### 11.2 合并门禁

每个功能变更至少满足：

1. 新增或更新相应测试。
2. Local 离线测试全绿。
3. 契约兼容测试全绿。
4. 无新增高危安全问题和明文凭据。
5. 本计划中的阶段状态、证据或决策日志已同步更新。

## 12. 部署与配置

建议配置：

- `RUNTIME_PROFILE=local|cloud`
- `MODEL_PROVIDER=deepseek|gemini|fake`
- `INCIDENT_STORE=duckdb|spanner`
- `ACTION_MODE=simulate|engineer_a2a|disabled`
- `REQUIRE_ACTION_APPROVAL=true`（生产不可关闭）
- `EXTERNAL_DOCS_ENABLED=false`（默认）
- `INCIDENT_SCHEMA_VERSION=v1`

Local Profile 只加载本地适配器；Cloud Profile 不应携带合成数据或本地数据库文件。配置启动时必须执行组合合法性校验，例如 Cloud + `ACTION_MODE=engineer_a2a` 必须同时存在 Engineer 地址和审批服务。

当前 Local 部署入口从仓库根目录运行：

```text
python tools/local-stack/local_stack.py --workspace .local/networkagent-stack doctor
python tools/local-stack/local_stack.py --workspace .local/networkagent-stack init
python tools/local-stack/local_stack.py --workspace .local/networkagent-stack status
```

`demo` 的 Incident 确认与动作批准必须分两次调用；第二次批准必须复制第一次返回的 `action_hash` 与 `expected_revision`。`serve` 只以前台、loopback、禁用动作模式运行；`reset` 必须使用 `--yes` 且只删除 marker-owned state/artifacts。完整命令见 [Local Profile 文档](../packages/telco-local/README.md)，安全结论见 [Local Governance Gate](security/local-governance-gate.md)。

## 13. 主要风险与缓解措施

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| ADK 1.18 与 1.22+ 行为不兼容 | 回调、Session、Toolset 失效 | 进程隔离、契约测试后再统一版本 |
| A2A 0.2.16/0.3.11 混用 | Agent Card、消息和任务状态不兼容 | 锁定新服务 0.3.11，建立跨服务协议契约测试 |
| 部分 legacy Agent 仍使用受 [GHSA-rg7c-g689-fr3x](https://github.com/advisories/GHSA-rg7c-g689-fr3x) 影响的 ADK 1.18.0 | 未认证远程代码执行；不能作为新增外部入口 | Supervisor 已升级并精确锁定修复版 1.28.1，Assurance 完全不依赖 ADK；其余 legacy 服务继续隔离并在 P7 分批升级 |
| LTE 样例与 5G 实验网语义不一致 | RCA 得出错误目标或错误动作 | `technology`、5G KPI、ResourceReference 映射和动作能力校验 |
| 两套 Incident 逻辑并存 | 重复事件和状态冲突 | Canonical Incident + 单一 Resolver 所有权 |
| DuckDB/Spanner 语义差异 | 事务和并发错误 | Repository 契约、乐观锁、禁止双主写 |
| 模型供应商差异 | Tool Calling/Schema 表现不同 | ModelProvider + 场景回放 + 结构校验 |
| 重复或乱序事件 | 重复修复 | correlation_key、revision、idempotency_key |
| 审批链重复 | 用户混淆或越权 | Resolver 与 Engineer 共享一个版本化审批对象 |
| 提示或规则诱导写操作 | 网络风险 | 工具权限分层、审批、作用域、Kill Switch |
| 敏感标识泄漏 | 合规风险 | 聚合、脱敏、日志过滤、出站检查 |
| 无标签订户号伪装为 LTE 网元标识 | 订户标识通过资源字段进入证据、日志或报告 | Local Profile 对 eNodeB/Cell 使用 28-bit 十进制白名单、规范化与导入/出站双边界校验；错误不回显原值 |
| `install.sh` 继续膨胀 | 部署不可维护 | 新组件独立脚本/清单，顶层只编排 |
| UI 与后端状态不同步 | 错误审批或误判 | 服务端持久状态、事件游标、断线恢复 |
| 配置/依赖/凭据漂移 | 构建不可重复或秘密泄露 | 按服务锁文件、秘密扫描、Secret Manager、禁止记录凭据对象 |
| LTE CSV 时间戳没有时区 | 窗口关联结果随运行机器时区变化 | 数据 manifest 明确记录 `assumed UTC`，导入时强制转换为 aware UTC |
| 旧本地前端绑定 ADK 专用 REST | 无法可靠接入 Supervisor/A2A DataPart | 保留为参考，不复制；P2b 已建立跨版本 A2A/`input_required` 契约，P6 再完成统一 RCA 视图 |
| Local DuckDB 跨进程写竞争 | 独立 CLI/服务进程同时写时一方可能得到文件锁错误 | P2a 明确单 writer、失败关闭；Cloud 采用 Spanner 事务，后续部署 Profile 增加外部互斥或有界重试 |
| Local Detector 内部扫描仍以有界全量查询为主 | 数据量超过本地演示预算时会 fail closed，不能当作生产流处理 | 规则、观测、episode、候选均设硬上限；P2b 已增加显式时间窗、资源范围和候选分页，P3 改为增量/事件驱动查询 |
| pre-v3 Incident 缺规则内容哈希 | 只能证明版本号，不能证明同版本规则未被改写 | 新候选使用内容哈希；旧数据迁移时补规则快照/哈希并标记 legacy，不回退 current 猜测 |
| 多规则/多资源证据归因过粗 | 未来聚合 Incident 可能把一条证据错误用于另一规则或资源 | 当前限制为单规则、单资源 episode；扩展前将 evidence scope 细化到 violation/rule 级 |
| Local DuckDB 初始化与运行权限混用 | 严格只读部署可能误执行 DDL/导入并触发锁竞争 | P2b 已拆分 `init`/`run`；运行进程只打开已初始化 schema，部署时继续采用单 writer 与最小文件权限 |
| 第三方数据许可不清或上游许可漂移 | 公司答辩、再分发或商用存在合规风险 | P3e 许可白名单默认拒绝；锁定许可证据、归属和版本，许可变化必须人工复核；对外交付前由公司合规/法务确认 |
| 外部归档或结构化数据携带恶意内容 | 路径穿越、解压炸弹、解析器耗尽或本机文件污染 | staging 隔离、SHA-256、主机/类型/容量白名单、安全解包、解析预算；第三方代码与 Notebook 永不执行 |
| 开放数据含订户/设备/UE 标识或原始 payload | 敏感信息进入 Canonical、日志、报告、模型或 Git | 已启用适配器采用精确 schema，未知列在行读取前拒绝并且只输出安全投影；通用自由文本扫描/quarantine 完成前不启用其他格式 |
| 不同数据集 KPI/标签语义被强行统一 | 离线指标看似优秀但 RCA 和资源目标错误 | 每字段记录单位/时区/聚合/标签 provenance；未知语义保留数据集命名空间，不伪造 ERAB/RRC/PDU Session 等 KPI |
| 大型数据集和派生文件进入 Git/构建产物 | 仓库膨胀、许可扩散、发布包泄漏和 CI 不稳定 | 原始/派生数据只存 `.local/telco-lab` 摘要锁定缓存；提交前与 wheel/容器构建均扫描，Git 仅含代码生成的小型脱敏 fixture |
| 历史事件回放误入 Cloud 或触发真实动作 | 污染 Staging/生产 Incident 或造成网络变更 | 回放仅 loopback Local Profile，`ACTION_MODE=disabled|simulate`，检测 GCP/Engineer 配置即拒绝；速率、总量与时间平移有界 |
| 训练/测试标签泄漏或选择性报告 | 答辩结果不可复现或被高估 | 固定数据锁、split、种子、场景和代码提交；有/无标签指标分开，机器可读报告保留拒绝与失败计数 |

## 14. 发布与回滚策略

1. 所有新路径使用 Feature Flag。
2. 先在 Local Profile 完成纵向切片，再进入 Cloud 测试环境。
3. 云端先运行影子模式，只记录比较结果。
4. 真实动作最初仅对白名单服务、低风险操作和人工审批开放。
5. 每次动作保留 Kill Switch 和明确的补偿/人工恢复步骤。
6. 数据 Schema 采用向前兼容迁移；发布时先扩展、切换后再收缩。
7. 回滚优先切回旧 Agent 路由，不删除新数据；用 `schema_version` 保持可读。

## 15. 架构决策日志

| ID | 日期 | 决策 | 状态 | 原因 |
|---|---|---|---|---|
| ADR-001 | 2026-08-28 | 以 `NetworkAgent-dev` 为统一主仓库 | Accepted | 它已包含完整网络生命周期、A2A/MCP、Operator 和 Dashboard |
| ADR-002 | 2026-08-28 | Resolver 是唯一 Incident 生命周期编排者 | Accepted | 避免两个 RCA/Resolution 流程竞争状态所有权 |
| ADR-003 | 2026-08-28 | Local/Cloud 使用端口与适配器共享同一业务流程 | Accepted | 保留离线易用性，同时支持真实网络 |
| ADR-004 | 2026-08-28 | Agent 间使用 A2A，基础设施能力使用 MCP/Repository | Accepted | 延续现有协议边界并降低框架耦合 |
| ADR-005 | 2026-08-28 | 模型供应商可配置，按 Agent 角色选择模型 | Accepted | 避免锁定 Gemini 或 DeepSeek |
| ADR-006 | 2026-08-28 | 所有有副作用动作强制人工审批和幂等验证 | Accepted | 网络变更属于高风险操作 |
| ADR-007 | 2026-08-28 | 初期保持 Agent 进程隔离，再统一 ADK 版本 | Proposed | 降低依赖升级与合并同时发生的风险 |
| ADR-008 | 2026-08-28 | LTE Demo 与 5G Live 使用共同遥测契约但不同 KPI/规则集 | Accepted | 两者网络技术和现有数据语义不可直接互换 |
| ADR-009 | 2026-08-28 | 高频 PM/Trace 的云端物理存储在容量基准后决定 | Proposed | Incident 状态适合 Spanner，但高频明细不应未经评估直接写入同一存储 |
| ADR-010 | 2026-08-28 | 领域模型与协议 DTO 建立独立 `telco-domain` 包 | Accepted | 现有 `agent_library` 强依赖 ADK 1.18，无法作为跨 ADK 版本的无框架共享核心 |
| ADR-011 | 2026-08-28 | 传输中的 ApprovalReference 不是授权，ActionGateway 执行前必须以可信存储与时钟重新解析最新决定 | Accepted | 防止伪造载荷、旧批准重放、撤销后复用及调用方回拨时间复活过期授权 |
| ADR-012 | 2026-08-28 | Local Detector/RCA 先落地为无框架确定性核心，A2A/ADK 只作为后续进程边界适配 | Accepted | 让 KPI、分段、证据、规则和仓储先可离线回归，避免同时调试 ADK 主版本差异、A2A 确认流和模型不确定性 |
| ADR-013 | 2026-08-28 | 预览 `incident_id` 是绑定规则/资源/窗口/观测/证据的内容寻址确认令牌；message/workflow/idempotency 使用彼此独立的传输标识 | Accepted | 防止预览后数据漂移与标识混用，同时允许同一业务候选在不同消息中安全重放 |
| ADR-014 | 2026-08-28 | 确定性 RCA 只接受精确规则版本+内容哈希和类型/资源/时间均匹配的证据；任何 provenance 冲突均显式 `INCONCLUSIVE` | Accepted | 禁止用 current 规则解释历史 Incident、跨 EvidenceKind 拼 fact 或使用外部/陈旧证据伪造确定根因 |
| ADR-015 | 2026-08-28 | P2a RCA 是只读 `PROPOSED` Artifact，不保存报告、不生成动作、不推进 Incident | Accepted | 保持 Resolver 的唯一生命周期所有权，并在 A2A/审批闭环接入前消除隐式写操作 |
| ADR-016 | 2026-08-28 | Local LTE eNodeB/Cell 组件使用 28-bit ASCII 十进制白名单，并在导入与 Telemetry 出站边界共用同一规范化函数 | Accepted | 阻止无标签订户号伪装成资源标识，避免前导零产生身份碰撞，并让被篡改的本地数据库也能 fail closed |
| ADR-017 | 2026-08-28 | P2b Assurance 是纯 A2A 0.3.11 独立服务，不依赖 ADK、`agent_library`、模型或云 SDK | Accepted | 保持运行时隔离，并避免把受安全公告影响的 legacy ADK 版本扩散到新服务 |
| ADR-018 | 2026-08-28 | Incident 确认只接受绑定 task/context/workflow/trace/snapshot/candidate 的服务端 challenge DataPart；TextPart 永不触发写入 | Accepted | 防止 LLM 或展示文本伪造审批、跨会话重放和候选替换 |
| ADR-019 | 2026-08-28 | Supervisor 精确锁定 ADK 1.28.1 与 A2A 0.3.11；共享库只声明 ADK 1.x 范围，每个服务继续使用自己的精确锁 | Accepted | 关闭 Supervisor 的已知 ADK 安全风险，同时避免把仍需验证的升级强加给全部 legacy Agent |
| ADR-020 | 2026-08-28 | Assurance 的 Task/challenge 状态使用 DuckDB 持久化，challenge 只存哈希，崩溃重放有界保留 15 分钟，并把 `init` 与 `run` 分离 | Accepted | 在本地单 writer 边界内兼顾重启恢复、容量上限、最小运行权限与过期状态回收 |
| ADR-021 | 2026-08-28 | Supervisor 只展示 TextPart，结构化 DataPart 采用 exact schema；审批工具调用由服务端保存并单播回原 Socket | Accepted | 防止文本命令、未知字段、跨会话事件和伪造 ToolMessage 推进 Incident 写入或模型续跑 |
| ADR-022 | 2026-08-29 | Cloud Incident 新建 `Canonical*V2` 表族，legacy `Incident` 只读保留；Inbox、Incident/Audit/Idempotency/Association 与 Outbox 在同一 Spanner 事务提交 | Accepted | 旧表缺 revision/provenance，原地复用无法提供 CAS、全局来源归属和可靠事件确认 |
| ADR-023 | 2026-08-29 | Fault Pipeline 使用唯一 `legacy|shadow|canonical|paused` 模式，默认 shadow；canonical 与 legacy writer 互斥 | Accepted | 避免双主并保留可验证回滚点；入口只在 durable commit 后 ACK |
| ADR-024 | 2026-08-29 | Cloud MCP 是独立六工具只读服务，使用专属 FGAC reader；Fault、MCP、Outbox、一次性迁移分别使用精确角色 | Accepted | 工具 allowlist 不能替代数据库最小权限，迁移所需 Audit 读取也不应扩散给常驻 Fault 身份 |
| ADR-025 | 2026-08-29 | 一次性迁移只自动接受 Canonical DETECTED/revision-0；bundle 使用校验和而非签名，legacy/归属歧义进入可追踪隔离清单 | Accepted | 保留可证明的快照与 provenance，禁止按字段相似度猜测 legacy 状态或任意选择冲突 owner |
| ADR-026 | 2026-08-30 | P3e 采用 manifest/lock 驱动且许可默认拒绝的 Local Data Lab；原始数据不入 Git，只有脱敏并通过显式适配器的 Canonical 数据可进入评估/回放 | Accepted | 让第三方数据可重复、可审计地复用，同时隔离许可漂移、供应链内容、隐私和跨数据集语义风险 |
| ADR-027 | 2026-08-30 | P3e 回放严格限制为 loopback Local Profile 与禁用/模拟动作；开放数据 Gate 和 Cloud Staging IAM/OIDC/DLQ/WIF Gate 相互独立 | Accepted | 离线数据可以验证业务契约与韧性，但无法证明真实云身份、投递和网络安全边界 |
| ADR-028 | 2026-08-30 | Local 治理只允许策略生成的固定 `LOCAL_SIMULATION`；审批分为预览与决定两次调用，并同时绑定 action hash、report、scope、Incident revision、有效期和可信时钟 | Accepted | 在没有真实网络副作用的条件下验证完整状态机，同时关闭旧预览、参数替换、过期批准和伪造 ApprovalReference 的执行路径 |
| ADR-029 | 2026-08-30 | Local 部署采用显式 marker-owned `.local` workspace、JSON-only 命令、默认禁用动作、固定 loopback 前台服务和确认式精确 reset | Accepted | 为答辩与离线回归提供可重复入口，同时防止隐式云凭据、后台残留进程、外部监听和误删用户/仓库文件 |

## 16. 待确认事项

这些事项不阻塞 P1，但必须在对应阶段开始前决策：

- Local Profile 的最终入口使用现有中文 UI，还是统一 Dashboard 的本地构建。
- Cloud 模式默认模型供应商及每个角色的模型预算。
- legacy Spanner Incident 的人工字段映射、历史状态还原和归档保留期（v2 新表切换方案已由 ADR-022 确定）。
- 允许自动执行的低风险动作白名单是否存在；默认全部要求人工审批。
- 外部文档允许列表、网络访问策略和内容留存要求。
- 目标部署是否继续全部使用 Cloud Run，还是部分 Agent 进入 GKE。

默认方案分别为：保留本地 UI 直至功能等价、Gemini 为 Cloud 默认且 DeepSeek 可选、新建 v2 后迁移、全部人工审批、外部检索关闭、沿用现有 Cloud Run/GKE 分工。

## 17. 进度看板

状态定义：`NOT STARTED`、`IN PROGRESS`、`BLOCKED`、`DONE`。不得用主观百分比替代验收证据。

| 工作项 | 状态 | 最近更新 | 验收证据/备注 |
|---|---|---|---|
| 两项目快速架构与代码基线分析 | DONE | 2026-08-28 | README、入口、依赖、数据与测试已检查 |
| 统一目标架构与实施路线 | DONE | 2026-08-28 | 本文档已完成架构、代码、交付和兼容性复核 |
| 主仓库 Git 可回滚基线 | DONE | 2026-08-28 | 私有仓库 `LeegenSteven/NetworkAgent-dev` 已建立并将 `unified-platform` 设为默认分支；P1 基线为 `e363662`，原始 `dev@44ecbb3`、其余 4 个分支和 4 个发布标签均已推送并校验 |
| P1 领域模型与接口 | DONE | 2026-08-28 | 独立 `telco-domain` 包；双环境各 315 项通过；wheel 构建/独立导入成功；原 10 类安全阻断复审全部关闭；GitHub Actions Python 3.12/3.13 均通过（[run 33151947728](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33151947728)），Gate PASS |
| 首个 Local 纵向切片（P2a） | DONE | 2026-08-28 | 13,440 KPI + 579 安全 Trace → 15 候选；预览零写、确认唯一写、RCA 只读；双依赖矩阵各 468 项通过，wheel/CLI 冒烟成功，安全 Gate PASS |
| A2A/Supervisor 接入（P2b） | DONE | 2026-08-28 | 独立 Assurance A2A 0.3.11、持久 challenge/task、真实 HTTP detect/confirm/analyze/restart、Supervisor 单播与结构化审批桥均通过；本地门禁与[GitHub Actions run 33179490152](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33179490152) 全绿，三个 wheel 与依赖检查通过 |
| Cloud 数据接入 | IN PROGRESS | 2026-08-30 | P3a–P3d 代码与远程 Emulator Gate 已完成：Spanner v2、事务 Inbox/Outbox、严格 Fault Ingress、六工具只读 MCP、四角色 FGAC 和一次性 Canonical 迁移；[GitHub Actions run 33202370157](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33202370157) 的 Python 3.12/3.13 Emulator、五 wheel 与依赖检查全绿，上一轮 RC 的额外 [Cloud CI run 33301104595](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104595) 也为 `success`，但其 `headSha` 不等于当前 Sprint 1 RC。仍需 Cloud Staging IAM/OIDC/DLQ/Workload Identity 验收后才能标 DONE |
| 本地开放数据实验室（P3e） | IN PROGRESS | 2026-08-30 | 已交付 `telco-lab` Python/CLI、稳定 lock ID、artifact/catalog/source URL 指纹、许可证据/归属/复核日期、BubbleRAN 固定 commit 的三 artifact catalog、未知列失败关闭的 CSV/JSON 投影、离线评估、immutable `ReplayPlan`、公开 `ReplayWirePayload`、loopback transport、单调 paced runner 与 caller-owned 持久 checkpoint。Assurance receiver 对 exact BubbleRAN 5G SA UL BLER 签名做每 source 独立 Incident 映射；202 前有界回读 current immutable facts、revision-0 Audit 与 SourceAssociation，缺失 Incident/Audit 则 503 零新写。真实 TCP E2E `1 passed`，覆盖持久 checkpoint 重启零投递、`RESOLVED`/`REOPENED`/`REJECTED`/过期 `FAILED` 和 settled exact replay 零写。RC `7cbff490ccb71befb42c7cd30204f7f88e3b2f38` 的 Assurance、Data Lab、Local 三个 workflows 共 9 个 job 全绿，三个 14 天 `VERIFIED RC` artifacts 已绑定该 SHA；跨事件聚合、真实动作/Cloud Staging、RCAEval、签名/attestation 与完整发布终验未完成，因此不标 DONE |
| Resolver 合并 | IN PROGRESS | 2026-08-30 | Local 无框架编排骨架已完成：确定性 triage/investigate/RCA、持久报告/动作、分步幂等与崩溃后续跑；尚未合并现有 Resolver Strategy/Troubleshoot/Resolution Agent、Cloud checkpoint 或 Outbox 消费 |
| 修复与验证闭环 | IN PROGRESS | 2026-08-30 | Local 模拟切片已完成：两阶段 hash+revision 显式审批、可信执行前复核、唯一 `LOCAL_SIMULATION`、通过 `RESOLVED`/失败 `REOPENED`、审批跨 TTL 的零动作 `FAILED` 收口、exactly-once 重放与响应丢失续跑；本机 stack+governance+full LTE E2E 聚焦验收 39 项通过，结论见 [Local Governance Gate](security/local-governance-gate.md)。真实 Engineer/MCP/GitOps/Operator 与 Cloud Tester 尚未接入 |
| Local 部署、CI 与可观测性 | IN PROGRESS | 2026-08-31 | `local-stack` 安全本地入口及 S2-01..03 已通过；S2-04 因没有兼容、provenance 可追溯且完整 `C/H=0/0` 的基础镜像而为 `BLOCKED`。S4-01、S4-02 窄切片均已由各自 RC 的双 Python Local jobs 和独立下载 artifact 验收并标记 `DONE`：前者交付有界阶段事件/报告内诊断，后者交付双分支各 8 个 revision group / 14 个事件的只读 Canonical 投影。二者都没有 runtime structured logs、OpenTelemetry/Collector、跨组件或分布式 trace、Prometheus、外部告警或 SLO；S2/Workflow B、S4/Workflow E/P7/S7 保持 `IN PROGRESS`，Gate B/G2、Gate A/S3/G4、Gate E/G5、统一 UI 与 Cloud 部署保持开放。 |
| 实施开发计划 2.0 Sprint 1 | DONE | 2026-08-30 | S1-01..07、Workflow C/D、独立 Gate C/D、本地攻击性/E2E 与 RC `7cbff490ccb71befb42c7cd30204f7f88e3b2f38` 的远程 Assurance/Data Lab/Local 矩阵均通过；Sprint 2 已启动。A2A 协作式 timer/后台任务、每 source 独立 Incident、RCAEval、P3e、S3 与 Cloud Gate 保持开放 |
| 实施开发计划 2.0 S2-01 安全容器 | DONE | 2026-08-30 | `none` + `service:assurance` 网络模型、non-root/只读根/cap drop/资源限制、只读输入和内容/层守卫已独立复核；本地 `75 passed, 1 skipped`。RC `d0a020fb7a5d8a33cd136cd18917d21b7e067946` 的远程 run 33311995755 已通过 Compose resolve、build/inspect、应用层/rootfs 扫描、init、health/隔离/shared-loopback smoke+probe/reset/cleanup。S2/Workflow B 仍为 IN PROGRESS；Gate B、Gate A、S3、G4 与供应链证据保持开放 |
| 实施开发计划 2.0 S2-02 容器治理恢复 | DONE | 2026-08-30 | RC `d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4` 的 container run 33314782750 与 Local run 33314782757 共 4 个 job 全绿；成功/失败分支分别为 `RESOLVED`/`REOPENED`，两者均观察到重启、exact replay、零真实网络副作用与项目清理。Local 双 Python 回归与 artifact 9733117877 已绑定同一 RC。S2/Workflow B/P7 仍为 IN PROGRESS；Gate B、Gate A、S3、G4、registry image/digest、容器 SBOM、hash-lock、Trivy 与 signing/attestation/provenance 保持开放 |
| 实施开发计划 2.0 S2-03 容器 Trivy 双报告证据 | DONE | 2026-08-30 | RC `68b16ea528a85b743aa8c05044948bac195ee8ec` 的 container run 33320667296 两个 job 全绿；14 天 artifact 9734817516 分类 `VERIFIED RUNNER-LOCAL EVIDENCE`，archive digest `sha256:e35d8eb12484feeb474477bae0f3d937019f3ab19e9f8ccf1fd491b8a95f0394`。其绑定 runner-local image/config digest `sha256:0e8caa8418d93e1f9654655b84331723e8223d9dc94e66274aed1ca3fa7d00bb`、Trivy 0.74.0 fixable Critical/High gate `0/0`、full diagnostic `5C+29H` 全部 unfixed，以及 CycloneDX 1.7 `145` components；但仍无 registry image/digest、签名/attestation/provenance、Trivy DB OCI digest/signature 或离线独立重验，因此 S2/Workflow B/P7 与 Gate B/G2/A/S3/G4 保持开放 |
| 实施开发计划 2.0 S2-04 基础镜像关闭评估 | BLOCKED | 2026-08-31 | 同一 Trivy 0.74.0/冻结数据库下四个候选均不能同时满足 CPython 3.12、glibc、公开可取得、provenance 可追溯与完整 Critical/High `0/0`；不接受忽略 unfixed、漏洞白名单或丢弃包身份。S2/Workflow B/P7、Gate B/G2/A/S3/G4 保持开放。 |
| 实施开发计划 2.0 S4-01 本地答辩可观测证据 | DONE | 2026-08-31 | RC `cb4a4e7191f67aa71ef980668352d55001e23142` 的 Local run 33330915665 双 Python jobs 全绿；artifact 9737683310 为 106,309 bytes、archive digest `sha256:5b26c3ccaff8e57c10c4bb3375ebbad8156d151e26158b1123639e3423006eca`，独立下载 11 文件闭包精确匹配 manifest。该窄切片只证明进程内事件、诊断时序、报告内指标/告警、隐私与 Runbook；只有 Local workflow 被本 RC 路径规则触发。S4/Workflow E/P7/S7 保持 `IN PROGRESS`，Gate E/G5 与 G2/G4 保持开放。 |
| 实施开发计划 2.0 S4-02 Canonical 生命周期安全投影 | DONE | 2026-08-31 | RC `69643e8a6f79b1264d60e5517eeb9a24035c8e7d` 的 Local run 33336341831 双 Python jobs 全绿；artifact 9739212391 为 115,482 bytes、archive digest `sha256:237bcb207e29e53aba6c907f94efcb1999b77b1640f59dd95b8d3c9c30b27aa7`，独立下载 12 文件闭包精确匹配 manifest。双分支各 8 个 revision group / 14 个事件，`read_only=true`、`side_effects=false`、`distributed_trace=false`；同 SHA Assurance/Container/Cloud 全绿，Lab 未触发。S4/Workflow E/P7/S7 保持 `IN PROGRESS`，Gate E/G5 与 G2/G4 保持开放，S2-04 保持 `BLOCKED`。 |
| UI 与发布 | NOT STARTED | 2026-08-28 | — |

## 18. 文档维护规则

本文档在开发过程中必须持续维护，具体规则如下：

1. 每次开始一个阶段时，将对应状态改为 `IN PROGRESS`，并记录范围和日期。
2. 每次完成代码变更时，同步更新交付物、风险、决策和验收证据。
3. 只有验收标准全部满足并附上测试/演示证据后，阶段才能标记为 `DONE`。
4. 出现阻塞时必须记录：阻塞原因、影响、已尝试方案和解除条件。
5. 任何架构边界变化必须新增 ADR，不覆盖旧决策；被替代的决策标记为 `Superseded`。
6. 工作范围变化必须同步调整阶段表，不允许代码状态领先于计划文档。
7. 每个开发回合结束前检查“进度看板”和“变更记录”。
8. 测试命令、部署结果、关键截图或日志位置应以仓库内相对路径记录，不粘贴密钥或敏感数据。

## 19. 变更记录

| 日期 | 变更 | 作者 |
|---|---|---|
| 2026-08-28 | 建立统一平台实施计划初稿，记录目标架构、阶段、风险、ADR 和维护规则 | Codex |
| 2026-08-28 | 完成并行架构、交付与依赖复核；补充 ADK/A2A 版本隔离、LTE/5G 语义边界、首个 A2A 纵向切片及供应链风险 | Codex |
| 2026-08-28 | 恢复 GitLab 原始历史，建立 `unified-platform` 开发基线并创建 GitHub 私有仓库；P1 领域模型与状态机进入开发 | Codex |
| 2026-08-28 | 建立独立 `telco-domain` 包、双 Python/Pydantic 环境契约测试及 3.12/3.13 CI；安全 Gate 发现并开始修复仓储绕过、审批重放、伪造完成和审计历史篡改路径 | Codex |
| 2026-08-28 | 完成 P1 安全加固：原子状态机仓储、版本化审批引用、可信时钟、并发去重、隐私/载荷预算及不可变审计；双环境各 315 项通过，独立复审 Gate PASS | Codex |
| 2026-08-28 | 完成 P0：认证并推送 GitHub 私有仓库，保留 GitLab 原始分支与标签历史，设置 `unified-platform` 为默认分支；P1 的 Python 3.12/3.13 云端 CI 全部通过，并将官方 Checkout/Setup Python 更新至 v7 | Codex |
| 2026-08-28 | 启动 P2：以 Local Profile 为边界迁移 LTE CSV/DuckDB、确定性异常检测和证据驱动 RCA，先建立无 GCP/无模型 API 的纵向验收链路 | Codex |
| 2026-08-28 | 完成 P2a：建立 `telco-local`、安全数据投影、确定性 Detector/精确规则 RCA、显式确认与本地 CLI；关闭数据漂移、证据串用、数字标识伪装、隐私和容量 Gate，双依赖矩阵各 468 项及 wheel 冒烟通过；P2b A2A 接入保留为下一阶段 | Codex |
| 2026-08-28 | 启动 P2b：冻结纯 A2A Assurance、持久 challenge、结构化确认和 Supervisor 单会话桥接契约；记录 legacy ADK 1.18 安全公告并禁止将其作为新增生产暴露面 | Codex |
| 2026-08-28 | 完成 P2b/P2：交付本机限定的纯 A2A Assurance、持久且有界恢复的 Task/challenge、真实 detect/confirm/analyze/restart 链路、Supervisor exact DataPart/单播/可信工具续跑；Supervisor 升级至 ADK 1.28.1，本地发布门禁与三 wheel 冒烟全部通过 | Codex |
| 2026-08-28 | P2b 提交 `974db42` 的 GitHub Actions 远程验收通过：Assurance 四个 job 及 Local Profile 双 Python 矩阵全部 `success`，回填可追溯运行链接 | Codex |
| 2026-08-28 | 启动 P3：冻结“Spanner v2 扩展表族 → 事务 Inbox/Outbox Fault Ingress → 独立只读 MCP”三切片；旧 Incident/Fault/MCP 路径保留回滚且不双主，云端默认 shadow | Codex |
| 2026-08-29 | 完成 P3 本地实现：新增 Canonical Spanner v2 与四类 Repository、严格 Pub/Sub Fault Ingress、六工具只读 MCP、四角色 FGAC、共享三实现契约及 checksummed 一次性 Canonical 迁移；保持 legacy 只读、shadow 默认和 P4 前 Outbox 不派发，转入远程 Emulator/Cloud Staging Gate | Codex |
| 2026-08-29 | 完成 P3 发布前容量与迁移加固：Repository/迁移入口先限界后迭代，Spanner source owner/active key/association 改为批量事务，补 1,000 来源首导、精确重放和状态推进，以及基于合法 bundle 的 JSON/隐私/depth 攻击回归；本地矩阵全部通过 | Codex |
| 2026-08-30 | 启动 P3e 本地开放数据实验室：纠正编号（P3c 已由 Cloud MCP 占用），冻结首批许可白名单、摘要锁定缓存、锁文件/校验和、脱敏、Canonical 适配、离线评估与 loopback 回放边界；建立独立 Gate，未将尚未实现内容标记为通过 | Codex |
| 2026-08-30 | 完成 P3e BubbleRAN 首个纵向切片：新增 `telco-lab` catalog/fetch/verify/run/evaluate、固定 commit/大小/SHA-256/CC BY-SA 4.0、原子缓存、隐私安全 CSV/JSON 适配和确定性 episode/duration 评估；真实全量与无网络 E2E 通过。P3e 仍为 IN PROGRESS，未把 RCAEval、loopback 回放或 Cloud Staging Gate 误报为完成 | Codex |
| 2026-08-30 | 完成 P3e 首切片终审加固：评估改为 sweep-line 并限制重叠候选，BubbleRAN 使用精确 schema 变体且未知列失败关闭，workspace lock 绑定许可证据/归属/复核日期和 catalog/source 指纹；双 Pydantic、真实全量、wheel 内容白名单与源码树外安装通过，终审在当前 BubbleRAN 范围为 PASS | Codex |
| 2026-08-30 | 完成 Local 治理闭环切片：`LocalGovernanceEngine` 持久推进 triage/RCA/审批/模拟执行/验证，两阶段审批精确绑定 action hash 与 Incident revision，执行前重新解析可信批准；唯一动作 `LOCAL_SIMULATION` 无外部 I/O，验证通过进入 `RESOLVED`、失败进入 `REOPENED`，幂等与响应丢失续跑纳入回归 | Codex |
| 2026-08-30 | 完成 Local 部署入口与受控回放第一批实现：`local-stack` 提供显式 workspace 的 `doctor/init/status/demo/serve/reset` 和固定 loopback/安全 reset；`telco-lab` 提供无标签、lock-bound、有界 `ReplayPlan` 与 opt-in loopback HTTP transport。明确记录 transport 尚无 Canonical Fault 接收器、墙钟节奏、自动重试或持久 checkpoint，也不存在 Cloud 真实动作或 Staging IAM/OIDC/DLQ/WIF 证据 | Codex |
| 2026-08-30 | 建立[实施开发计划 2.0](implementation-development-plan-2.0.md)并启动 Sprint 1：Assurance 新增四个 `/local/v1` Governance 薄路由，保持 A2A 根路由不变；HTTP 同时校验 loopback Host/peer、严格 operation header/JSON/预算，只调用固定 `LOCAL_SIMULATION` 引擎 | Codex |
| 2026-08-30 | 完成 loopback transport 首轮终审收口：禁代理/重定向、DNS/IP pin、逐事件重验、固定 ACK/错误预算；恢复 API 移除裸序号，checkpoint 精确绑定 plan/endpoint/window/event/payload。明确 checkpoint 非认证 ACK，且 paced runner、自动重试、持久化与 Canonical Fault 业务 bridge 仍待开发 | Codex |
| 2026-08-30 | 完成 Sprint 1 第二批本地回放治理：公开 `ReplayWirePayload`、单调 paced runner、有界 transient retry/deadline/cancel 证据、durable-before-202 Assurance Fault receiver 和真实 TCP 业务 E2E 已实现。exact BubbleRAN 5G SA UL BLER 规则仅使用服务端 provenance；成功、验证失败、拒绝、过期和 settled exact replay 零写均通过。仍无 checkpoint 持久化、跨事件聚合、真实动作/Cloud 或 RCAEval，远程 CI 为 PENDING | Codex |
| 2026-08-30 | RC `427fc6832bf6b115d035e5d2cb492a25ffd82395` 的 Assurance、Data Lab、Local 与额外 Cloud workflows 全部成功且 `headSha` 精确绑定；双 Python、双 Pydantic、治理/E2E、wheel 内容/源码树外安装与依赖检查证据已回填。远程未产出 wheel digest/artifact，后续证据文档提交不是受测 RC；仅 S1-06 关闭，P3e/P3/发布工作仍为 IN PROGRESS | Codex |
| 2026-08-30 | 在旧 RC 后新增严格 loopback `healthz/readyz/version`、caller-owned 原子持久 checkpoint 与持久 replay wrapper；真实 TCP E2E `1 passed`，确认 checkpoint 重启零投递。当前本地 Lab+Lab E2E 双 Pydantic 各 `222 passed, 1 skipped`、Assurance full `54 passed`、Domain+Local+shared contracts `520 passed`、status `4 passed`、Local E2E `3 passed`、A2A contracts `33 passed`、A2A E2E `4 passed`。release artifact、CycloneDX SBOM 与 `pip-audit` 证据生成已实现，尚待新远程 RC 验收；S1-04 保持 READY FOR REVIEW，S1-07/Sprint/P3e/S3 保持 IN PROGRESS | Codex |
| 2026-08-30 | RC `6ba631929c312bbff27ef0ad4a9136d2cb390ae1` 的 Assurance、Data Lab、Local 与额外 Cloud workflows 全部成功。三个 Python 3.12 jobs 上传 14 天 `VERIFIED RC` artifacts；下载后 ZIP/逐文件摘要与 manifest 一致，CycloneDX 1.4、runtime inventory、wheel scan、外部安装/`pip check` 和 `pip-audit==2.10.1` 零已知漏洞均通过。S1-07 关闭，但签名/attestation、offline hash-lock、RCAEval、跨事件聚合和 Cloud Staging 保持开放 | Codex |
| 2026-08-30 | RC `7cbff490ccb71befb42c7cd30204f7f88e3b2f38` 的 Assurance、Data Lab、Local 三个 workflows 共 9 个 job 全绿且 `headSha` 精确绑定；三个 Python 3.12 job 发布 14 天 `VERIFIED RC` artifacts。Gate C/D 的本地独立复核与 Assurance `76`、local-stack `22`、Local E2E `3`、A2A contracts `33`、A2A E2E `4` 证据完成回填，S1-01..07 与 Sprint 1 关闭并转入 Sprint 2 准备。Cloud run 33301104595 明确保留为上一 RC 的历史证据；P3e、S3、RCAEval、跨事件聚合和 Cloud Staging 不标 DONE | Codex |
| 2026-08-30 | 启动 S2-01 安全容器候选：冻结 `assurance/init/reset=none`、`probe/smoke=service:assurance` 的无端口/无 bridge 共享 loopback 模型，以及 digest-pinned、non-root、只读根、cap-drop、资源限制、只读输入与 image-layer guard；独立静态审计、`56 passed, 1 skipped`、Black/flake8/YAML/JSON/diff 通过。本机无 Docker/actionlint，远程 `telco-container` 尚未运行，故 S2/Workflow B 为 IN PROGRESS、S2-01 仅 READY FOR REVIEW、Gate B 未通过；真实 Docker、hash lock、Trivy C/H、容器 SBOM 与签名/attestation/provenance 保持开放 | Codex |
| 2026-08-30 | RC `d0a020fb7a5d8a33cd136cd18917d21b7e067946` 的 [telco-container run 33311995755](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33311995755) 精确绑定该 `headSha`，远程 Linux `76 passed, 0 skipped`，compose-policy job 99258612862 与 build-inspect-smoke job 99258640065 均成功；真实 Compose/build/inspect 得到 runner 本地 image ID `sha256:0acef50a2ee7978ea67a8b37582a19698a21b1303451ce37a0a569d48fef6cff`（非 registry digest），完成 5 层 / 2,570 成员应用层和 9,148 成员合并 rootfs 扫描、初始化 `13440/579/0`/无外部访问及 health/隔离/shared-loopback smoke/probe steps。probe 无 stdout；reset 删除 state/artifacts/marker 且 `workspace_removed=true`，cleanup 成功；本地门禁 `75 passed, 1 skipped`，run 上传 0 artifacts，未发布 registry image 或容器 SBOM/签名/attestation/provenance | S2-01=`DONE`；S2/Workflow B/P7 保持 `IN PROGRESS`，Gate B、Gate A、S3 与 G4 保持开放 | Codex |
| 2026-08-30 | RC `d1ffc0e2334d0fda5ab62f47bdc28a1ae7f5ffe4` 的 [telco-container run 33314782750](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782750) 与 [Local run 33314782757](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33314782757) 精确绑定该 `headSha` 且共 4 个 job 全绿。容器 Linux 政策 `128 passed`；成功/失败治理 JSON 分别为 `RESOLVED`/`REOPENED`，两者均 `restart_observed=true`、`exact_replay=true`、`real_network_side_effects=false`；顶层 `projects_removed=true` 证明两个 Compose 项目均已清理。Local 两版各通过 Domain + Local `518 passed`、local-stack `29 passed, 2 skipped`、Local E2E `2 passed`；Python 3.12 `VERIFIED RC` artifact 9733117877 archive digest 为 `3d557a52a80960add94c04b443c14f613892701c5b5b93dcfba4174fd78f3469`。容器 run 上传 0 artifacts | S2-02=`DONE`；S2/Workflow B/P7 保持 `IN PROGRESS`，Gate B、Gate A、S3 与 G4 保持开放；registry image/digest、容器 SBOM、hash-lock、Trivy 与 signing/attestation/provenance 未完成 | Codex |
| 2026-08-30 | RC `68b16ea528a85b743aa8c05044948bac195ee8ec` 的 [telco-container run 33320667296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33320667296) 精确绑定该 `headSha` 且两个 job 全绿。compose-policy job 99281949020 与 build-inspect-smoke job 99281979960 成功；14 天 artifact 9734817516 分类 `VERIFIED RUNNER-LOCAL EVIDENCE`，archive digest 为 `sha256:e35d8eb12484feeb474477bae0f3d937019f3ab19e9f8ccf1fd491b8a95f0394`。该 artifact 绑定 runner-local image/config digest `sha256:0e8caa8418d93e1f9654655b84331723e8223d9dc94e66274aed1ca3fa7d00bb`、Trivy 0.74.0 fixable Critical/High gate `0/0`、full diagnostic `5` Critical + `29` High 且全部 unfixed，以及 CycloneDX 1.7 SBOM `145` components | S2-03=`DONE`；S2/Workflow B/P7 保持 `IN PROGRESS`，Gate B、G2、Gate A、S3 与 G4 保持开放；仍无 registry image/digest、签名/attestation/provenance、Trivy DB OCI digest/signature 与离线独立重验 | Codex |
| 2026-08-31 | 完成 S2-04 基础镜像关闭评估：相同 Trivy 0.74.0/冻结数据库下没有候选同时保持 CPython 3.12、glibc、公开可取得、provenance 可追溯与完整 Critical/High `0/0`；拒绝忽略 unfixed、漏洞白名单或丢弃包身份的扫描假关闭 | S2-04=`BLOCKED`；S2/Workflow B/P7、Gate B/G2/A/S3/G4 保持开放 | Codex |
| 2026-08-31 | RC `cb4a4e7191f67aa71ef980668352d55001e23142` 的 [Local run 33330915665](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33330915665) 双 Python jobs 全绿：每版 Domain + Local `518 passed`、local-stack `66 passed, 2 skipped`、Local E2E `2 passed`，3.12 release boundary `18 tests passed`。VERIFIED RC artifact 9737683310 为 106,309 bytes、archive digest `sha256:5b26c3ccaff8e57c10c4bb3375ebbad8156d151e26158b1123639e3423006eca`；独立下载 11 文件闭包匹配 10 条 manifest 记录加 manifest，两个 supplemental evidence 的 bytes/SHA 均通过。只有 Local workflow 因路径规则触发；579 Trace rows 不是 OTel span，指标/告警仅为报告内诊断且 `propagated_trace=false` | S4-01 窄切片=`DONE`；S4/Workflow E/P7/S7=`IN PROGRESS`，Gate E/G5、G2/G4 保持开放，S2-04=`BLOCKED`；本证据文档提交晚于且不等于受测 RC | Codex |
| 2026-08-31 | RC `69643e8a6f79b1264d60e5517eeb9a24035c8e7d` 的 [Local run 33336341831](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33336341831) 双 Python jobs 全绿：每版 Domain + Local `576 passed`、local-stack `89 passed, 2 skipped`、Local E2E `2 passed`，3.12 release boundary `18 tests passed`。双分支各 8 个 revision group / 14 个 allowlisted 事件，只读、精确绑定、单执行尝试、exact retry、双 cleanup、`side_effects=false` 与隐私边界均通过。VERIFIED RC artifact 9739212391 为 115,482 bytes、archive digest `sha256:237bcb207e29e53aba6c907f94efcb1999b77b1640f59dd95b8d3c9c30b27aa7`；独立下载 12 文件闭包匹配 11 条 manifest 记录加 manifest，lifecycle summary 为 8,431 bytes / SHA-256 `5726b4eec6f0c4621a3d804b0f6973a24d41ae14ea1878b2e57205a299966e45`，重建报告为 8,290 bytes / SHA-256 `21528fd8694da0cb0c51452b14c45af864b24951282475367addbcd1b74fa004`。同 SHA Assurance 33336341877、Container 33336341805、Cloud 33336341859 全绿，Lab 未触发 | S4-02 窄切片=`DONE`；S4/Workflow E/P7/S7=`IN PROGRESS`，Gate E/G5、G2/G4 保持开放，S2-04=`BLOCKED`；`distributed_trace=false`，不声明 runtime structured logs、OTel/Prometheus、SLO、外部告警或 Cloud production 完成；本证据文档提交晚于且不等于受测 RC | Codex |

## 20. 项目完成定义

统一项目只有同时满足以下条件才算完成：

- Local 与 Cloud 两种 Profile 均使用同一 Incident/RCA 领域逻辑。
- 从异常检测到修复验证存在一个可恢复、可审计的状态机。
- Detector、Resolver、Engineer、Tester 和 Supervisor 通过稳定契约协作。
- 未审批写操作、重复修复和敏感数据外泄都有自动化防护测试。
- Local E2E 与 Cloud 预发布 E2E 通过，回滚演练通过。
- P3e 至少两条互补开放数据纵向切片在无网络、无 GCP、无模型 API 环境中可从锁定缓存重复评估/回放，并保留许可、来源、隐私和结果证据。
- 用户能在一个界面查看证据、报告、审批、执行和验证结果。
- 旧的重复 Incident/RCA 路径已停止接收新任务并有明确归档说明。
- 架构、运行、部署、测试、故障处理和安全文档完整且与代码一致。
