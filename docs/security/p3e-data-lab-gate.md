# P3e 本地开放数据实验室 Gate

> 日期：2026-08-30
> 范围：第三方开放数据的 catalog、下载、缓存、解包、脱敏、适配、离线评估与本地回放
> 当前结论：**IN PROGRESS（BubbleRAN 下载、适配、离线评估、公开 Replay wire、paced loopback transport、caller-owned 持久 checkpoint、durable Canonical Fault 业务回放与真实 TCP 持久重启治理 E2E Gate 已通过；跨事件聚合、RCAEval 及发布终验待完成）**

## 1. 发布边界

- P3e 只在 Local Profile 中运行；下载是显式准备动作，评估和回放默认离线。
- 下载内容始终视为不可信数据，不能作为 Python 包、脚本、Notebook、宏、二进制、提示词或 Agent 指令执行。
- 原始数据只进入调用方指定的本地 workspace（推荐 `.local/telco-lab`）；Git 只允许 catalog、适配器、归属材料和代码生成的脱敏/限量 fixture。
- 只有政策白名单、精确版本、许可证证据、大小和 SHA-256 同时匹配的数据可从 staging 提升到缓存。
- 已启用的 BubbleRAN 适配在 Canonical 投影之前对源 schema 做精确白名单校验；未知列失败关闭且错误不回显原值。通用隐私扫描/quarantine 未启用，其他格式不得借用 BubbleRAN 的 `PASS`。
- 回放只能访问 loopback 测试入口，且 `ACTION_MODE=disabled|simulate`；不得加载 Cloud Profile 或真实 Engineer/Operator 能力。
- 本 Gate 不能替代 P3 Cloud Staging IAM/OIDC/DLQ/Workload Identity Gate。

## 2. 威胁模型

需要防御的输入和失败包括：

- 上游内容被替换、浮动分支变化、镜像站或重定向接管；
- 不明确/不兼容许可、数据许可与仓库代码许可混淆、归属或 ShareAlike 丢失；
- 路径穿越、符号/硬链接、设备名、大小写碰撞、压缩炸弹、嵌套归档和磁盘耗尽；
- CSV/JSON/Parquet 解析器资源耗尽、重复 key、非有限数、超长字段和恶意 schema；
- IMSI/SUPI/MSISDN/IMEI、UE/IP/MAC、payload 或自由文本中的敏感信息泄漏；
- KPI 单位、时区、资源和标签误映射，导致看似正确但业务含义错误的评估；
- 测试标签泄漏到规则/提示、只挑成功场景、非确定抽样和基准结果不可复现；
- 历史回放误发到 Cloud、复用生产凭据、触发真实动作、制造无界事件或绕过 Fault Ingress；
- checkpoint 损坏、跨 plan/旧 window 复用、单调回退、并发多 writer、路径逃逸或远程/设备文件系统触发；
- 原始行、URL 查询参数、令牌或敏感字段进入日志、报告、MCP 或 A2A TextPart。

## 3. 首批准入表

`ELIGIBLE` 只表示许可类型已进入 P3e 政策白名单，不能替代锁定和技术验收。

| Dataset ID | 许可 | 准入状态 | 启用前必须补齐 |
|---|---|---|---|
| `bubbleran-open-telco` | CC BY-SA 4.0 | ENABLED | 已固定 commit、三份 artifact 字节数/SHA-256、许可证据与归属；对外再分发仍需公司合规复核 |
| `nist-ran-anomalous-state` | NIST Open Data | ELIGIBLE | 固定 artifact、许可页快照/摘要、SHA-256、payload 安全投影 |
| `ranalyzer` | CC BY 4.0 | ELIGIBLE | 固定 release/commit、SHA-256、归属、gNB 日志隐私扫描 |
| `telecomts` | MIT | ELIGIBLE | 固定 Hugging Face revision、各 artifact SHA-256、字段/标识扫描和容量预算 |
| `rcaeval` | MIT | ELIGIBLE | 固定 release/commit 与案例清单、SHA-256、日志/trace 安全投影 |
| 其他或许可不清来源 | 未确认 | DENIED | 新 ADR、法务/所有者可追溯许可证据、catalog 变更复核和完整 Gate |

## 4. 必须通过的 Gate

状态仅可使用 `NOT RUN`、`FAIL`、`PASS` 或带解除条件的 `BLOCKED`。`PASS` 只覆盖证据位置明确列出的已启用输入格式和流水线，不外推到其余白名单数据集。

| Gate | 验收要求 | 当前状态 | 证据位置 |
|---|---|---|---|
| Catalog schema | 未知字段、重复 ID、浮动版本、非 HTTPS、越界 host、缺许可或缺预算均拒绝 | PASS | `test_catalog.py`、默认 catalog 固定 commit/size/SHA |
| License default deny | 非白名单/许可证据变化时下载和 run 均拒绝；归属、证据 URL/SHA、复核日期随锁保留，安全摘要随报告保留 | PASS | `test_catalog.py`、`test_workspace.py`、`test_pipeline.py`、`THIRD_PARTY_DATA.md` |
| Fetch integrity | 临时文件下载、大小上限、SHA-256、允许重定向、断流/超时均 fail closed | PASS | `test_downloader.py`、core security review |
| Cache integrity | 锁定 artifact 命中时重新校验大小/SHA；损坏、替换、catalog 漂移均检测 | PASS | `test_downloader.py`、`test_workspace.py` |
| Archive safety | 启用 ZIP/TAR 前必须覆盖 traversal、链接、设备名、碰撞、nested/archive bomb | BLOCKED（当前 catalog 仅启用原始 CSV/JSON，不接受归档） | P3e 后续格式 Gate |
| Parser budgets | BubbleRAN CSV/JSON 行、列、字段、字符串、深度、非有限数和重复 key 预算有效 | PASS | `test_safe_json.py`、`test_adapters.py`、`test_schema.py` |
| Privacy gate | BubbleRAN CSV 精确列集；任意未知/敏感/自由文本列在行读取前拒绝；UE 标识/标签不进入 Observation、报告或 CLI | PASS（仅 BubbleRAN 精确 schema） | `test_adapters.py`、`test_pipeline.py`、哈希绑定的全量数据测试 |
| Semantic adapter | BubbleRAN 源字段、单位、UTC、5G-SA资源、标签和缺失策略显式 | PASS | `adapters.py` 显式映射、`test_adapters.py` |
| Provenance | dataset/lock/artifact/catalog/source-URL/license/adapter/contract/window 可从 lock、manifest 与报告反查；package 版本随 CLI 证据输出 | PASS | 默认 catalog、workspace lock、`LabBundleManifest`、`test_pipeline.py` |
| Deterministic evaluation | 相同输入与配置生成相同摘要、计数和指标；ground truth/prediction 类型隔离；重叠候选计算有硬预算 | PASS | `test_evaluation.py`、`test_pipeline.py`、哈希绑定的真实全量重放 |
| Replay confinement | 非 loopback、Cloud Profile、真实动作、GCP 配置或超速/超量均拒绝；transport 禁代理/重定向并逐事件重验 endpoint/env/event；paced runner 只可对 network/timeout 做明确有限重试 | PASS（BubbleRAN ReplayPlan + transport + receiver） | `test_replay.py`、`test_loopback_sink.py`、`test_paced_runner.py`、`test_fault_receiver.py` |
| Replay checkpoint persistence | 显式本地 workspace/checkpoint 目录；checkpoint 严格绑定 plan/window/event/payload，严格 JSON/大小预算、原子保存、单调不回退、非阻塞单 writer；损坏、跨 plan、旧 window、路径/链接/Windows drive 越界失败关闭 | PASS（caller-owned local store） | `test_checkpoint_store.py`、`test_bubbleran_replay_governance.py` |
| Replay idempotency | durable-before-202；持久 checkpoint 重启零投递；精确重放、响应丢失恢复和 settled replay 不重复创建 Incident/Audit/Action/Verification，改变 payload 冲突 | PASS（每 source 独立 Incident） | `test_fault_receiver.py`、`test_bubbleran_replay_governance.py` |
| Repository hygiene | Git/构建产物/wheel 不含第三方大文件、原始数据、quarantine 或本机秘密 | PASS | 当前源码构建的 domain/lab wheels 内容扫描、源码树外强制安装、`pip check` 与受控清理 |
| Offline E2E | 无 GCP、无模型 API 完成数据适配/评估；本地 loopback 上完成 Replay→Incident→RCA→审批→模拟验证 | PASS（BubbleRAN 切片） | `test_local_dataset_pipeline.py`、`test_bubbleran_replay_governance.py` |

## 5. 数据集验收矩阵

| 数据集 | Fetch/Lock | Privacy | Adapter | Offline Eval | Replay | License/Attribution | 总状态 |
|---|---|---|---|---|---|---|---|
| BubbleRAN | PASS | PASS | PASS | PASS | PASS（plan/wire/paced transport/persistent checkpoint/durable receiver/governance E2E） | PASS | PASS（当前单数据集切片） |
| NIST RAN Anomalous State | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| RANalyzer | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| TelecomTS | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| RCAEval | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

第一版发布至少要求 BubbleRAN 与 RCAEval 两行全部 `PASS`，并且通用 Gate 全部 `PASS`。其余三行可保持 `NOT RUN`，但对应数据集必须继续保持运行时禁用，文档和演示不得宣称已支持。

## 6. 攻击性验收用例

实现时至少固定以下红测：

1. 锁文件 SHA-256 与远端字节不符、长度不符、同 lock 内容漂移。
2. HTTPS 重定向到未允许域、包含凭据的 URL、`file:`/UNC/本地回环或云元数据地址。
3. ZIP/TAR 中 `../`、绝对路径、符号/硬链接、Windows 设备名、大小写碰撞和超预算膨胀。
4. CSV 超长单元格/列爆炸、JSON duplicate key/`NaN`/深度炸弹、Parquet 恶意行组或元数据预算。
5. 标题正常但自由文本包含 IMSI、SUPI、MSISDN、IMEI、MAC、IP 或原始 payload。
6. 数字订户号伪装成 cell/gNB/eNodeB 资源；不同技术资源类型混用。
7. KPI 单位或时区缺失、标签列映射成观测特征、测试集标签泄漏到规则生成。
8. adapter 输出未知字段、过大 Evidence、原始日志进入 TextPart/MCP、错误回显敏感行。
9. 回放目标为非 loopback、环境含 Cloud Profile、动作模式为真实执行、速率/总数越界。
10. 同场景精确重放、乱序、重复 source event、回放中断后恢复和 poison event；checkpoint 损坏/跨 plan/回退、并发 writer、路径逃逸、UNC/device、非固定 Windows drive 与 API 失败。
11. Git status、构建上下文和 wheel 扫描发现 raw/cache/quarantine、凭据或大文件。
12. 白名单之外的 dataset ID、许可证据哈希变化或 catalog/lock 签核不一致。

## 7. 评估证据要求

每次可作为答辩或发布证据的运行必须保存 CLI 生成的不含原始数据的 JSON 证据；组织的发布流程可再将它与签名构建证明组成报告目录。证据至少包括：

- evaluation/run 内容 ID、UTC 时间、package 版本、Python/Pydantic/Domain schema；对外发布时另绑定签名代码提交；
- dataset ID、lock ID、artifact SHA-256、license ID 和 attribution；
- adapter/contract/policy 版本、固定种子、场景与时间窗口；
- 输入、显式排除、适配与拒绝计数；尚未实现的 quarantine/回放计数不伪造；
- Detector/RCA/Incident/容量指标及阈值判断；
- 已启用 schema 的未知列拒绝、敏感字段排除计数、输出模型验证与容量预算结果；
- 失败时的固定错误码和阶段，不包含原始行、敏感值或带 token 的 URL。

报告中的“准确率”只能对应有明确 ground truth 的固定 split。无标签或跨领域数据只能陈述容量、稳定性、检测候选或人工检查结果。

## 8. 当前验收证据

- 当前本地 Data Lab + Lab E2E 在 Pydantic 2.5.3 与 2.13.4 下各 `222 passed, 1 skipped`；Assurance full `54 passed`，Domain + Local + shared contracts `520 passed`，status 定向 `4 passed`，Local E2E `3 passed`，A2A contracts `33 passed`，A2A E2E `4 passed`。
- 单个真实 loopback TCP BubbleRAN → Governance E2E `1 passed`：持久 checkpoint 首次完成 4 个事件，重新打开同一 store 后零选择/零尝试/零投递；绕过 checkpoint 的 settled exact replay 零新增 Incident/Audit/Action/Verification 写。
- receiver 在 ACK 前有界回读 current Incident 不可变事实、初始 revision-0 Audit 与 SourceAssociation；删除 Incident 或初始 Audit 的故障注入均返回 503 且不新增业务写。
- `healthz/readyz/version` 只在直接 loopback 暴露；readiness 以 1 秒预算读取一次 Repository，依赖失败、超时或已有卡住 worker 时固定返回 503，不并发创建第二个 worker；所有标准非 GET 方法使用固定有界 JSON 405 契约，HEAD 按 HTTP 语义省略 body，也不构成 Cloud readiness。
- RC `427fc6832bf6b115d035e5d2cb492a25ffd82395` 的 [Data Lab CI run 33296728022](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296728022) 为 `success`，其 `headSha` 精确等于该 RC。Python 3.12/3.13 + Pydantic 2.13.4 各为 `198 passed, 1 skipped`，Python 3.12 + 声明下限 Pydantic 2.5.3 也为 `198 passed, 1 skipped`；wheel 内容 allowlist、源码树外安装 smoke 与 `pip check` 全绿。
- 同一 RC 上的 [Assurance CI run 33296728012](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296728012) 与 [Local CI run 33296728032](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296728032) 均为 `success`：Assurance 的两个 Python job 各通过 Domain 323、Lab 197、Local 195、Lab E2E 1（另 1 skipped）、Local E2E 3、Assurance 50、A2A contracts 33、A2A E2E 4，并完成四 wheel/源码树外 smoke/`pip check`；Local 的两个 Python job 各通过 Domain+Local 518、local-stack 19（另 2 skipped）、Local-only E2E 2，真实 CLI 到达 `RESOLVED` 后安全 reset，并完成两 wheel/`pip check`。
- 同一 RC 上的额外 [Cloud CI run 33296727982](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296727982) 也为 `success`，但不替代 Cloud Staging 验收。
- 最新本地 `telco-lab 0.1.0` wheel 为 67,653 bytes，SHA-256=`96B5D696CB769E29256C5319FF391DA5CC30F2B25D108F5730FF9F8BD467C40B`。远程 jobs 验证了构建、内容 allowlist、源码树外安装与依赖一致性，但没有输出远程 wheel 字节数/SHA-256，也没有上传 artifact；该摘要仍只是本地证据。
- 本段证据回填形成的后续提交不是受测 RC，不得替换上述 runs 的 `headSha`。release artifact、CycloneDX SBOM 与 `pip-audit` 证据生成已经实现，待新远程 RC 验收；当前不宣称新 artifact URL、wheel/SBOM digest 或扫描结论。远程历史矩阵与当前本地实现都不关闭跨事件聚合、RCAEval 或发布终验，P3e 继续保持 `IN PROGRESS`。

## 9. 退出标准

P3e 第一版只有同时满足下列条件才可标记 `DONE`：

- BubbleRAN 与 RCAEval 精确版本已锁定，下载、校验、脱敏、适配、离线评估和受限回放均通过；
- 通用 Gate 全部 `PASS`，攻击性用例成为自动化回归；
- 无网络/无 GCP/无模型 API 的固定答辩流程可以从缓存重复执行；
- 精确重放不会重复创建活动 Incident，非本地目标和真实动作确定性拒绝；
- 仓库、构建上下文与发布 wheel 不含第三方原始数据或本机秘密；
- 许可归属文件、数据锁、评估报告和操作手册可被独立复核；
- P3 Cloud Staging Gate 仍按其真实状态单独报告，不因 P3e 通过而改变。

## 10. 当前未关闭事项

- RCAEval 的精确 artifact、最小案例、适配器和多源 RCA 评估尚未实现。
- loopback HTTP transport、paced runner、有限 transient retry、caller-owned 持久 checkpoint、Canonical Fault/Incident 业务接收器与真实 TCP 治理 E2E 已实现。checkpoint 仍只是非签名 continuation claim；store 为非阻塞单 writer，response loss 恢复依赖 receiver 幂等，不能作为共享数据库或认证 ACK。
- checkpoint 路径在 Windows 拒绝 UNC/device 并只接受 `DRIVE_FIXED`；POSIX mount topology 与恶意同用户 ancestor rename/swap TOCTOU 仍属于本机文件系统信任边界。
- 当前 receiver 为每 source event 独立 Incident，尚无 episode/跨事件聚合。受控 UL BLER 阈值只用于 BubbleRAN 本地测试 provenance，不是生产 RCA。
- BubbleRAN 当前使用全量基线和代码生成的极小 CI fixture；独立答辩脚本、延迟/峰值内存预算，以及新远程 RC 的 artifact/SBOM/`pip-audit` 与完整发布证据仍待验收。
- 本 Gate 不包含真实 Engineer/MCP/GitOps/Operator 动作或任何 Cloud Staging 验收。
- NIST、RANalyzer、TelecomTS 的适配优先级将在前两个适配器通过 Gate 后确定。
- 公司对 CC BY-SA 派生数据演示/再分发的合规要求需要在对外交付前确认。
