# P3 Cloud Profile 发布 Gate 审计

> 日期：2026-08-29
> 范围：Canonical Spanner v2、Pub/Sub Fault Ingress、Inbox/Outbox、Radio KPI/Evidence 端口与独立只读 MCP
> 当前结论：**IN PROGRESS（本地与远程 Emulator Gate PASS；Cloud Staging 待验收）**

## 1. 发布边界

- 新 Cloud Profile 使用独立 `Canonical*V2` 表族；旧 `Incident` 表保留只读，不原地迁移或长期双写。
- Fault Pipeline 只有 `legacy|shadow|canonical|paused` 一个模式开关，默认 `shadow`；`canonical` 与 legacy writer 不得同时成为主写。
- Pub/Sub 重投以持久 Inbox 和事务幂等处理；进程锁、A2A 调用或日志不得出现在 Spanner 事务回调中。
- 入口仅在 Inbox + Incident/Audit/Idempotency + Outbox 全部提交后确认消息；永久无效载荷进入订阅 DLQ，瞬时依赖失败返回可重试状态。
- 新 MCP 服务只注册读工具，使用独立 reader 身份，不加载 Engineering、任意 SQL 或 legacy 原始日志/Trace 工具。
- `NetworkMetrics` 仍是主机/服务指标，不映射为 LTE/5G Radio KPI；高频明细物理存储留待容量基准决策。

## 2. 必须通过的 Gate

| Gate | 验收要求 | 状态 |
|---|---|---|
| Repository 同构 | Memory、DuckDB、Spanner Emulator 通过同一 CAS/幂等/审计/关联契约 | PASS |
| 并发去重 | 50 路同事件或同 correlation 仅一条活动 Incident | PASS |
| 重开冲突 | `CLOSED → REOPENED` 原子重新获取 active keys，冲突不产生双 active | PASS |
| Inbox/Outbox 原子性 | 任一写入失败整个事务回滚；提交后崩溃重投不重复建事故/派发 | PASS |
| Ingress 边界 | 大小/深度/字段/base64/UTF-8/时间/隐私严格校验，错误不回显原载荷 | LOCAL PASS |
| MCP 只读 | 工具 allowlist 精确，UTC 窗口、资源、数量、256 KiB 出站预算全部 fail closed | LOCAL PASS |
| FGAC artifact | schema、部署 SQL 与文档的四角色/列级权限精确一致 | LOCAL PASS / STAGING PENDING |
| 一次性迁移 | no-DDL export、校验和、隔离清单、provenance 保留、重放完整性和部分失败恢复 | LOCAL + EMULATOR PASS / STAGING PENDING |
| Emulator/CI | Python 3.12/3.13、真实 Spanner Emulator DDL/事务、wheel 源码树外安装与 `pip check` | [PASS（run 33202370157）](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33202370157) |
| Cloud Staging | IAM/OIDC、DLQ、重投、Workload Identity 和非匿名 MCP/Fault 入口 | NOT RUN |

## 3. 已关闭的高风险边界

- Active key 仅保存域分离哈希；source event 全生命周期唯一归属，correlation/source 分裂指向不同活动 Incident 时整笔事务拒绝。
- Fault Ingress 对外层和解码后 JSON 同时执行原始/解码大小、重复 key、非有限数、严格 UTF-8、未配对 surrogate、深度、字段、订阅和时钟预算；日志只记录固定 code/disposition。
- Inbox、Incident、Audit、Idempotency、SourceAssociation 和 created-only Outbox 在一个可重试 Spanner 回调中提交；回调外冻结时间，回调内没有网络/A2A 副作用。
- MCP 注册集精确为六个只读工具；Repository 单项 256 KiB/批次 64 MiB，MCP 逐项累计并把完整响应收紧到 256 KiB，不先无界物化。
- 四个运行角色互不复用：`telco_fault_writer`、`telco_mcp_reader`、`telco_outbox_dispatcher`、短期 `telco_migration_importer`。Dispatcher 只能更新七个投递状态列，不能修改 Outbox payload/身份。
- Canonical 迁移保留 Incident 快照与完整 source association；同 bundle 来源/correlation 冲突全部隔离，不按顺序选 owner；exact replay 交叉检查 Incident、provenance、revision-0 audit 与 active keys。
- Repository 与迁移公开入口都先验证 bounded `Sequence` 和数量，再读取内容；1,000 条 source association 使用批量 owner/index 查询与批量 mutation。Fake Spanner 已覆盖迁移首导/重放和常规 create/transition，真实 Emulator 中保留相同容量用例。
- 迁移 JSON 防护由合法 bundle 单点注入测试验证 duplicate key、`NaN`、溢出浮点与 surrogate 均在 checksum 前拒绝；单条隐私和 canonical depth 24/25 边界同样固定为回归。

## 4. 本地与远程验收入口

2026-08-29 稳定源码快照的本机证据：领域 323 项、Local 166 项、共享 Repository 合同 2 项（合计 491）；Cloud/迁移/FGAC 146 项；Fault Ingress 与 Cloud MCP 101 项；P2b Assurance 回归 26 项及 Local E2E 1 项均通过。真实 Emulator 4 项在未设置 `SPANNER_EMULATOR_HOST` 的 Windows 环境中按设计 skip；纯 helper 合同仍在本机执行并通过。

提交 `fa07096` 的 [GitHub Actions run 33202370157](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33202370157) 已通过全部四个作业：Python 3.12/3.13 的真实 Spanner Emulator 均完成对象 DDL、共享 Repository、50 路并发迁移、1,000 条 source association 首导/重放/状态推进，以及 Inbox/Outbox/CAS/reopen；两个常规作业均完成全回归、五 wheel 构建、源码树外安装、四 CLI 冒烟和 `pip check`。

同一源码快照已重建 `telco-domain`、`telco-local`、`telco-cloud`、`telco-fault-ingress`、`telco-cloud-mcp` 五个 wheel，并在源码树外全新 Python 3.12 环境安装；`pip check` 无冲突，四个 CLI `--help` 通过，核心包从 `site-packages` 导入且 import 阶段未加载 `google.cloud.spanner` 或 `fastmcp`。wheel 内容不含 tests、build、`__pycache__` 或字节码。

- `packages/telco-domain/tests`、`packages/telco-local/tests` 与 `tests/contracts/repository`：领域、DuckDB 与共享 Repository 合同。
- `packages/telco-cloud/tests`：Spanner fake transaction、schema/FGAC、迁移、Outbox、Telemetry 与无凭据导入。
- `services/telco-fault-ingress/tests_fault_ingress`：严格 Pub/Sub 边界与四模式 ACK 语义。
- `services/telco-cloud-mcp/tests_cloud_mcp`：FastMCP 精确工具集、读边界与增量响应预算。
- `tests/e2e/cloud/test_spanner_emulator.py`：真实 GoogleSQL 对象 DDL、共享合同、50 路并发、生命周期/reopen、1,000 条关联的迁移首导/重放/状态推进和 Outbox lease；没有 `SPANNER_EMULATOR_HOST` 时显式 skip。
- `.github/workflows/telco-cloud.yml`：Linux Spanner Emulator `1.5.56`，Python 3.12/3.13 双矩阵，五 wheel 源码树外安装、四 CLI help 和 `pip check`。

## 5. 已知环境限制

- 首个已提交 P3 版本是 `Canonical*V2` 的正式 Schema 基线；任何使用未发布开发中间态 DDL 建立的数据库必须重建，不能作为迁移源。基线发布后的变更才适用 expand-first 向前迁移要求。
- Spanner Emulator 不验证 TLS、IAM 或生产延迟；Emulator 通过不能替代 Cloud Staging 安全验收。
- P3 只接入低频 Canonical Incident、Radio KPI Observation 和安全 EvidenceReference。原始高频 Trace/PM 的 Spanner/BigQuery 选型在容量基准前保持未决。
- P4 前 Outbox 可验证持久与可重放派发，但不将事件交给 legacy Resolver 推进 Incident 状态。
- 本机 Windows 环境没有 Docker，因此真实 Emulator 证据来自已通过的 [GitHub Actions run 33202370157](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33202370157)；本机 skip 不作为通过证据。
- Emulator 不支持 IAM/FGAC。四角色实际正/负权限、Cloud Run OIDC、Pub/Sub DLQ 和 Workload Identity 必须在 Cloud Staging 验收。
- 迁移 bundle 是 checksummed 而非 signed；legacy Spanner Incident 自动映射未实现，必须按[迁移手册](../runbooks/p3-canonical-migration.md)进入人工映射流程。
