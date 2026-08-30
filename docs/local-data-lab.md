# P3e 本地开放数据实验室设计

> 状态：**IN PROGRESS（BubbleRAN 下载/适配/评估、ReplayPlan、公开 wire、paced loopback transport、Canonical Fault 持久接收与本地治理 E2E 已实现；checkpoint 持久化、跨事件聚合与 RCAEval 待开发）**
> 日期：2026-08-30
> 适用范围：Local Profile 的外部开放数据获取、脱敏、适配、离线评估和受限回放
> 安全 Gate：[P3e Data Lab Gate](security/p3e-data-lab-gate.md)

## 1. 决策摘要

为项目保留一个稳定的本地数据接口是可行且必要的。首层先把不同开放数据集转换成标签隔离、版本化且有预算的 Lab 契约；只有经过后续显式 bridge 和既有 Domain Gate 的字段才能进入 Telemetry、Evidence、Resource 或 Fault Event 业务契约。这样既能先完成无 GCP 凭据的可重复评估，也不会把第三方字段未经证明就冒充生产语义。

P3e 采用“manifest 驱动、许可默认拒绝、原始数据不入 Git、适配器隔离、安全投影后才进入业务链路”的本地数据实验室。下载器不是通用 URL 抓取器；只有同时满足白名单、版本锁定、许可证证据和校验和要求的数据才可进入缓存。已启用的 BubbleRAN 适配器对源 schema 做精确白名单校验，未知列在读取数据行前拒绝；通用隐私扫描与 quarantine 尚未实现，因此其他数据格式保持禁用。

P3e 是 P3 的新增子阶段。现有 **P3c 已用于只读 Cloud MCP**，因此本工作不得命名为 P3c；正式编号为 **P3e**。

## 2. 目标与非目标

### 2.1 目标

1. 提供统一 `DatasetProvider`/`DatasetAdapter` 本地端口以及对应 CLI，使数据源差异不进入 Detector、Resolver 或 MCP。
2. 以锁文件、SHA-256 和来源证明复现某次答辩或回归所使用的精确数据版本。
3. 下载、缓存、解包、隐私扫描、脱敏、适配、评估和回放具有明确的失败关闭边界。
4. 允许在完全不加载 GCP 凭据和模型 API 的环境中执行确定性离线评估。
5. 对异常检测、事件关联、Incident 幂等、证据预算和 RCA 结果形成机器可读的评估报告。
6. 为公司答辩提供小体积、可重复、可解释的演示配置，同时保留使用较大数据集进行本地容量测试的能力。

### 2.2 非目标

- 不把第三方完整数据集、压缩包、数据库或派生大文件提交到 Git 仓库。
- 不提供任意 URL、任意代码、Notebook、二进制或 Shell 脚本的下载/执行能力。
- 不把外部数据的字段强行伪装成不存在的 LTE/5G KPI，也不把 UE/订户标识当作网元资源标识。
- 不让历史数据回放触发真实 Engineer、GitOps、Network Operator 或其他有副作用动作。
- 不默认把本地数据上传到 Spanner、BigQuery、模型供应商或任何外部服务。
- 不用开放数据验收替代 Cloud Staging 的 IAM、OIDC、Pub/Sub DLQ 或 Workload Identity 验收。
- 不承诺第三方数据长期可用；可重复性以已验证的本地摘要锁定缓存和 lock 文件为准。

## 3. 首批许可白名单

下表是“可进入实现和验证”的政策白名单，不表示下载、适配或 Gate 已经通过。每个数据集在实际启用前仍必须固定版本、保存许可证证据、记录归属要求并生成内容校验和。

| 数据集 | 来源 | 许可基线 | 计划用途 | 当前状态 |
|---|---|---|---|---|
| BubbleRAN Open Telco Datasets | [GitHub](https://github.com/bubbleran/open-telco-datasets) | CC BY-SA 4.0 | 带标签的 RAN KPI 异常检测、时序窗口和 Detector 评估 | ENABLED / PINNED |
| NIST RAN Anomalous State Dataset | [NIST/Data.gov](https://catalog.data.gov/dataset/radio-access-network-anomalous-state-detection-dataset) | NIST Open Data | 正常/异常状态对照、协议层证据安全投影 | ELIGIBLE / NOT IMPLEMENTED |
| RANalyzer Dataset | [GitHub](https://github.com/wineslab/RANalyzer-Dataset) | CC BY 4.0 | 5G 性能指标、gNB 日志、安全 Evidence 与 Fault Ingress 回放 | ELIGIBLE / NOT IMPLEMENTED |
| TelecomTS | [Hugging Face](https://huggingface.co/datasets/AliMaatouk/TelecomTS) | MIT | 多通道电信时序、异常分类和批量容量评估 | ELIGIBLE / NOT IMPLEMENTED |
| RCAEval | [GitHub](https://github.com/phamquiluan/RCAEval) | MIT | 指标/日志/调用链联合 RCA、根因排序与重放幂等 | ELIGIBLE / NOT IMPLEMENTED |

许可策略：

- `license_id`、原始许可页 URL、许可文本或其不可变摘要、归属文本和数据版本必须进入锁文件；只有名称相似不构成许可证明。
- CC BY 数据的归属信息必须随派生报告保留；CC BY-SA 数据若被分发，必须由发布前合规复核确认相同许可和归属要求。
- 许可不清、许可页消失、数据与代码许可混淆、需要账号但条款不可保存或禁止所需用途时，状态一律为 `DENIED`。
- SpotLight、商业网络 PM 等当前未能在项目内固定明确数据许可的来源默认禁用；它们只能在新增 ADR、许可证据和安全 Gate 后加入白名单。
- 本表是工程准入控制，不替代公司法务对再分发、商用或演示用途的最终判断。

## 4. 稳定的本地接口

已提供两层接口：业务层使用 `TelcoLab` 与类型化 Pipeline API，开发者和答辩脚本使用 `telco-lab` CLI。首版不增加远程 HTTP 下载接口，也不启动常驻服务。

```text
CatalogProvider.load() -> DatasetCatalog
TelcoLab.catalog() -> DatasetCatalog
TelcoLab.fetch(resource_id, accepted_license) -> ArtifactRecord
TelcoLab.verify(resource_id?) -> VerificationReport
TelcoLab.artifact_path(resource_id) -> verified local Path
TelcoLab.verified_manifest() -> verified WorkspaceLock

fetch_and_evaluate_bubbleran(lab, accepted_license, overlap_threshold)
evaluate_cached_bubbleran(lab, overlap_threshold)
```

当前 CLI：

```text
telco-lab --workspace .local/telco-lab catalog
telco-lab --workspace .local/telco-lab fetch <resource-id> --accept-license <license-id>
telco-lab --workspace .local/telco-lab verify [resource-id]
telco-lab --workspace .local/telco-lab run bubbleran-persistent-interference --accept-license CC-BY-SA-4.0 --overlap-threshold 0.1
telco-lab --workspace .local/telco-lab evaluate bubbleran-persistent-interference --overlap-threshold 0.1
```

工作区由调用方显式指定，推荐 `.local/telco-lab`。只有 `fetch` 与 `run` 允许触发下载；`catalog`、`verify` 和 `evaluate` 不隐式联网。命令输出是单行 JSON，只包含数据集/资源 ID、内容摘要、计数、指标和固定错误码，不输出本机路径、来源 URL/query、被拒绝原始行或敏感值。Python 已提供受限 `ReplayPlan`、公开 `ReplayWirePayload`、立即 loopback transport 与单调 paced runner；默认零重试，只有显式的有限策略可重试 network/timeout 瞬时失败。Assurance 已提供 durable-before-202 的 Canonical Fault 业务接收器。replay CLI 与 checkpoint 持久化尚未发布，该路径不能触发真实动作。

## 5. 仓库与缓存布局

当前目录边界：

```text
packages/telco-lab/
├── src/telco_lab/catalogs/default.json  # 固定来源、许可、大小、SHA-256
├── src/telco_lab/adapters.py            # 明确字段映射与隐私/容量边界
├── src/telco_lab/pipeline.py            # fetch/evaluate 类型化入口
├── src/telco_lab/replay.py              # lock-bound ReplayPlan 与事件安全投影
├── src/telco_lab/loopback_sink.py       # opt-in loopback HTTP transport
├── src/telco_lab/paced_runner.py        # 单调节奏、deadline/cancel 与有限 transient retry
└── tests/                               # 仅代码生成的极小 fixture，不含上游字节

networkagents/assurance/
└── src/telco_assurance_agent/fault_receiver.py  # durable Canonical receiver

.local/telco-lab/                        # 整体不提交 Git
├── artifacts/                           # 通过大小与SHA-256验证的缓存
└── telco-lab.lock.json                  # catalog/版本/许可/adapter/摘要锁
```

锁文件至少包含：

- `lock_id`、catalog ID/版本、dataset ID 与固定的上游提交；
- artifact 文件名、字节数、SHA-256、媒体类型与 adapter ID；
- 完整 catalog resource 指纹、源 URL 的不可逆指纹与允许主机（不持久化 URL query）；
- `license_id`、许可名称/URL、许可证据 URL/SHA-256、归属文本和复核日期；
- 获取时间、锁生成时间和基于上述不变字段计算的稳定锁标识。

适配后的时间窗、资源、指标集、adapter 版本、输入/内容摘要和记录计数保存在 `LabBundleManifest`中，不与下载锁混为同一层契约。归档条目、解压字节和压缩比预算只在未来启用归档格式时加入，当前 catalog 不接受归档。

禁止使用浮动的 `main`、`latest`、未固定的 Hugging Face revision 或仅靠文件名识别数据版本。首次下载得到的哈希不能自动改写受审锁文件；不匹配必须失败并由人工复核新版来源。

## 6. 数据处理流水线

```text
Catalog/License Gate
        ↓
显式 Fetch（HTTPS + 主机白名单 + 容量预算）
        ↓
SHA-256/大小/媒体类型验证
        ↓
严格格式与精确 schema 校验（仍视为不可信）
        ↓
字段白名单投影 → 未知字段失败关闭
        ↓
DatasetAdapter 语义映射
        ↓
Canonical Dataset + Provenance Manifest
        ↓
确定性离线评估 / 受限本地回放
```

### 6.1 下载与缓存

- 只有 catalog 中明确列出的 HTTPS 来源和重定向主机可访问；不读取浏览器 Cookie、云凭据或仓库凭据。
- 下载到同一工作区的随机 staging 文件，按字节流计算 SHA-256；验证固定大小和摘要后才能原子替换对应的锁定 artifact。
- 设单文件、总下载、超时和并发上限。断点续传必须验证完整内容，不能信任远端 ETag 代替 SHA-256。
- 任何校验失败、来源漂移、重定向越界或空间不足都失败关闭；不会以“最新版本”继续运行。

### 6.2 解包与格式边界

- 只允许 manifest 声明的 CSV、JSON/JSONL、Parquet 和受控归档类型；不执行上游代码、宏、安装脚本或 Notebook。
- 解包拒绝绝对路径、`..`、保留设备名、符号/硬链接、重复冲突路径、大小写碰撞、嵌套归档超限、压缩炸弹和条目数超限。
- 解析器设置行、列、字段、字符串、嵌套深度和非有限数预算；错误只记录固定 code。

### 6.3 脱敏与数据最小化

- 原始 IMSI、SUPI、SUCI、MSISDN、IMEI/IMEISV、GUTI/TMSI、MAC、个人 IP、原始 payload 和可识别自由文本不得进入 Canonical Dataset、日志、评估报告或 Git fixture。
- 不需要关联的标识直接删除。确需保持局部关联时，使用数据集专用、不可逆且由本机秘密保护的映射；低熵标识的普通 SHA-256 不视为匿名化。
- Cell/gNB/eNodeB 等资源 ID 必须通过对应技术的显式语法、范围和来源语义校验；未知数字列不得自动升级为 `ResourceReference`。
- BubbleRAN CSV 只接受已审核的精确列集；未知列（包括订户标识或自由文本）在读取数据行前固定错误拒绝。`ran_ue_id` 和标签列的排除策略、计数与输出模型校验结果进入评估摘要。通用自由文本扫描、带本地密钥的映射和 quarantine 工作流仍是后续门禁，不得对尚未启用的格式声称已完成。

### 6.4 语义适配

首层适配器只能输出 P3e 的版本化、不可变 Lab 契约：

- `LabObservation`：只含 detector-safe 指标，不含标签；
- `LabEpisode`：独立保存 ground truth；
- `PredictedEpisode`：独立保存显式 detector 输出；
- `LabBundleManifest` / `LabBundle`：保存版本、摘要、窗口、资源和适配器 provenance。

每个字段映射必须注明源字段、单位、聚合、时间语义、缺失值策略和是否使用标签。无法证明语义等价时保留为数据集命名空间内的 KPI，不能映射成 ERAB、RRC、PDU Session 等业务 KPI。当前 BubbleRAN bridge 只接受精确锁定的数据集/版本/场景、`5G_SA` GNB 资源与公开 wire allowlist；每 source 事件独立建立 Incident，不做跨事件聚合。`ran.mac.ul_bler > 0.15 ratio` 仅使用服务端固定规则版本/内容摘要构造 provenance，是受控本地测试签名，不是生产结论。原始日志不能直接注入提示、A2A TextPart 或 MCP 响应。

## 7. 离线评估

所有正式基准默认满足：`RUNTIME_PROFILE=local`、`MODEL_PROVIDER=fake`、`ACTION_MODE=disabled`、无 GCP 凭据、无外网。评估输入由锁文件和适配器版本确定。CLI 证据 JSON 包含 UTC 时间、Python/Pydantic/Domain schema、package 版本、lock ID、artifact/catalog/输出摘要、许可归属、隐私投影计数与评估指标。公司发布流水线可在包版本之外绑定签名的 Git 提交；未提供该外部证据时不伪造工作树提交号。

建议的首批套件：

| 套件 | 主要数据 | 指标 |
|---|---|---|
| Detector | BubbleRAN、NIST、TelecomTS | precision、recall、F1、误报率、检测延迟、拒绝/缺失计数 |
| Evidence | RANalyzer、NIST | 安全字段覆盖、窗口/资源匹配、预算拒绝、敏感命中为零 |
| Incident | 全部可适配事件 | 活动 Incident 数、source/correlation 去重、乱序/重放幂等、审计完整性 |
| RCA | RCAEval、RANalyzer | Top-k 根因命中、MRR、证据引用有效率、正确 abstain/`INCONCLUSIVE` |
| Capacity | TelecomTS、RANalyzer | 有界吞吐、峰值内存、P95 延迟、超预算失败关闭 |

有标签数据必须固定 train/validation/test 或 scenario 边界，防止用测试标签生成规则或提示。无标签数据只能用于稳定性、容量、人工场景或弱监督验证，不能报告监督学习准确率。不同数据集指标单独报告，不把不兼容标签混成一个“总准确率”。

## 8. 受限回放

回放只向 loopback 上的 Local Fault/Assurance 测试入口发送经过 Canonical 校验的事件：

- `run_paced_replay()` 按计划偏移与最大速率使用单调时钟串行调度，并以总 deadline 同时约束 sleep 和 in-flight emit；答辩配置可使用固定加速倍率。
- 将历史时间整体平移到指定 UTC 基准，不改变事件相对顺序；原始时间只保存在本地 provenance。
- `source_event_id` 由数据集 ID、锁 ID、scenario、源记录稳定键和规范化内容生成，重复回放必须得到相同业务幂等结果。
- 默认零自动重试；只有显式 `TRANSIENT_ONCE|TRANSIENT_TWICE` 可对 network/timeout 失败使用固定有界退避。契约、隐私、payload、环境、HTTP 状态或 poison 错误不重试。
- deadline/取消只保留最后 durable ACK 的 checkpoint 与可能不确定的 in-flight 序号；checkpoint 由调用方持有，当前不持久化。
- Assurance receiver 要求 loopback Host+peer、`replay-v1`、匹配幂等键与严格公开 wire；只在有界回读 current Incident immutable facts、初始 revision-0 Audit 与 SourceAssociation 后返回 202。任一持久事实缺失都返回 503 且不新增写。
- 进程启动时若发现 Cloud Profile、真实 Engineer 地址、GCP 项目配置或 `ACTION_MODE` 不是 `disabled|simulate`，回放必须拒绝启动。
- 回放完成后只生成摘要和 Canonical 审计引用；不复制原始日志正文到报告。

## 9. 答辩演示基线

建议保留两个固定演示场景：

1. **BubbleRAN 本地治理场景**：小型固定窗口经下载锁验证、脱敏和适配后，以真实 loopback TCP 进入每 source 独立 Canonical Incident；展示 exact 5G SA 规则 provenance、独立审批、`RESOLVED`/`REOPENED`/`REJECTED`/过期 `FAILED` 及 settled replay 零写。
2. **多源 RCA 场景**：RCAEval 或 RANalyzer 的一个许可允许的小型场景，重放 KPI 与安全 Evidence；展示重复事件不重复创建、MCP 只读查询和 `INCONCLUSIVE` 失败关闭。

每个演示都应支持 `--offline`：若本机已有匹配摘要的缓存，不进行网络访问；若没有缓存则明确提示先执行 `fetch`，不能临场下载浮动数据。演示脚本不得依赖个人账号、浏览器登录状态或真实 GCP 项目。

## 10. 实施切片

| 切片 | 内容 | 状态 |
|---|---|---|
| P3e-1 | catalog/lock schema、许可白名单、缓存布局和安全下载器 | DONE |
| P3e-2 | 安全解析、精确 schema 投影、通用隐私扫描与隔离流程 | IN PROGRESS（BubbleRAN CSV/JSON 精确 schema 已完成；通用扫描/quarantine、归档与其他格式未启用） |
| P3e-3 | BubbleRAN 与 RCAEval 两个最小适配器及 Canonical fixture | IN PROGRESS（BubbleRAN 已完成；RCAEval 待开发） |
| P3e-4 | Detector/RCA 离线评估器、机器可读报告和基线阈值 | IN PROGRESS（BubbleRAN episode/duration 评估已完成；RCA 待开发） |
| P3e-5 | loopback 限定回放、重复/乱序/中断场景和答辩演示 | IN PROGRESS（BubbleRAN 公开 wire、paced transport、durable receiver 与真实 TCP 治理 E2E `READY FOR REVIEW`，RC 远程矩阵已通过；checkpoint 持久化、独立答辩脚本与远程制品摘要/上传待完成） |
| P3e-6 | 其余三套白名单适配器、容量基准和发布归属材料 | NOT STARTED |

第一版退出标准以 BubbleRAN + RCAEval 两条互补路径为最低范围；其余白名单数据集可在接口和 Gate 稳定后逐步接入。任何切片只有在 [P3e Data Lab Gate](security/p3e-data-lab-gate.md) 对应证据完成后才能标为 `DONE`。

### 10.1 BubbleRAN 首切片证据（2026-08-30）

- catalog 固定上游 commit `fa4e3333855d64474e710bc5bebf11a9ec075e0b`，三份 artifact 的精确字节数与 SHA-256；第三方数据不进入 Git 或 wheel；
- 全量异常 CSV 适配为 1,597 条观测和 5 个连续 ground-truth episode，清洁 CSV 为 3,601 条观测和 0 episode，上游 JSON 为 55 个独立 prediction；
- temporal IoU `0.1` 的可重复基线为 TP=5、FP=50、FN=0，event precision=`0.090909...`、recall=`1.0`；该结果用于复现，不包装为生产精度结论；
- 同一数据重复适配得到相同 bundle、observation 与 evaluation 内容 ID；标签不进入 `LabObservation`，上游 UE 标识不进入模型或 JSON 报告；
- `packages/telco-lab/tests` 与 `tests/e2e/lab` 使用代码生成的小 fixture，在无网络、无 GCP、无模型 API 下覆盖一键 fetch/evaluate 与离线 replay-of-evaluation。

### 10.2 BubbleRAN 回放治理切片证据（2026-08-30）

- `ReplayWirePayload` 为 sender/receiver 共享严格契约；paced runner 具有单调节奏、有限 transient retry、deadline/cancel 与不确定序号证据；
- `POST /local/v1/faults/replay` 在有界回读 current Incident 不可变事实、revision-0 Audit 与 SourceAssociation 后才返回 202，删除 Incident/Audit 则 503 且零新写；精确重放只读，改变 payload 冲突；
- 真实 loopback TCP E2E 1 项通过，覆盖 `RESOLVED`、验证失败 `REOPENED`、审批 `REJECTED`、审批过期 `FAILED`、标签不泄漏与 settled exact replay 零写；
- 远程受测 RC 为 `427fc6832bf6b115d035e5d2cb492a25ffd82395`；[Data Lab CI run 33296728022](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296728022) 的 `headSha` 精确绑定该 RC，Python 3.12/3.13 + Pydantic 2.13.4 各 `198 passed, 1 skipped`，Python 3.12 + Pydantic 2.5.3 也为 `198 passed, 1 skipped`，wheel 内容 allowlist、源码树外 smoke 与 `pip check` 全绿；
- 同一 RC 的 [Assurance CI run 33296728012](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296728012) 在两个 Python job 中各通过 Domain 323、Lab 197、Local 195、Lab E2E 1（另 1 skipped）、Local E2E 3、Assurance 50、A2A contracts 33、A2A E2E 4，并完成四 wheel/外部 smoke/`pip check`；[Local CI run 33296728032](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296728032) 在两个 Python job 中各通过 Domain+Local 518、local-stack 19（另 2 skipped）、Local-only E2E 2，真实 CLI 到 `RESOLVED` 后 reset，并完成两 wheel/`pip check`；
- 同一 RC 的额外 [Cloud CI run 33296727982](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33296727982) 也为 `success`，但不构成 Cloud Staging 证据；
- 本地 `telco-lab 0.1.0` wheel 为 67,653 bytes，SHA-256=`96B5D696CB769E29256C5319FF391DA5CC30F2B25D108F5730FF9F8BD467C40B`。远程 workflows 未输出 wheel 字节数/SHA-256，也未上传 artifact，因此该值仍是本地摘要，不是 RC artifact digest；
- 上述四个成功 run 的 `headSha` 均为受测 RC；本次证据回填产生的后续文档提交不是该 RC。P3e 仍因 checkpoint 持久化、跨事件聚合和 RCAEval 未完成而保持 `IN PROGRESS`。

## 11. 与 Cloud Staging 的边界

P3e 可以证明本地数据适配、检测/RCA 逻辑、Canonical 契约、重复回放和隐私边界，但不能证明：

- Cloud Run 或 GKE 身份是否获得了正确且最小的 Spanner FGAC/IAM 权限；
- OIDC audience、issuer、调用方身份和非匿名入口是否正确；
- Pub/Sub subscription 的 DLQ、最大投递次数和 poison message 行为是否正确；
- Workload Identity/Kubernetes ServiceAccount 与 Google Service Account 绑定是否正确；
- 真实云网络、TLS、配额、延迟和故障恢复是否满足发布要求。

因此，P3e 通过不会改变 P3 Cloud Staging 的 `NOT RUN/PENDING` 状态，也不能使 P3 总阶段提前标记为 `DONE`。
