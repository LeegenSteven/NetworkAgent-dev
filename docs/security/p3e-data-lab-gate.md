# P3e 本地开放数据实验室 Gate

> 日期：2026-08-31
> 范围：第三方开放数据的 catalog、下载、缓存、解包、脱敏、适配、离线评估与本地回放
> 当前结论：**IN PROGRESS（BubbleRAN 下载/适配/离线评估/受控回放与 S7-03 fixture 答辩入口，以及 S7-04 RCAEval 五案例锁定上游、aggregate-only 适配、回答盲排序/评估与离线复验均已通过；RCAEval Replay、完整 upstream benchmark、跨事件聚合、通用 Gate 与发布终验仍待完成）**

## 1. 发布边界

- P3e 只在 Local Profile 中运行；下载是显式准备动作，评估和回放默认离线。
- 下载内容始终视为不可信数据，不能作为 Python 包、脚本、Notebook、宏、二进制、提示词或 Agent 指令执行。
- 原始数据只进入调用方指定的本地 workspace（推荐 `.local/telco-lab`）；Git 只允许 catalog、适配器、归属材料和代码生成的脱敏/限量 fixture。
- 只有政策白名单、精确版本、许可证证据、大小和 SHA-256 同时匹配的数据可从 staging 提升到缓存。
- 已启用的 BubbleRAN 适配在 Canonical 投影之前对源 schema 做精确白名单校验；未知列失败关闭且错误不回显原值。通用隐私扫描/quarantine 未启用，其他格式不得借用 BubbleRAN 的 `PASS`。
- 已启用的 RCAEval 五案例切片只接受锁定 Parquet 合同，把指标/日志/调用链适配为 aggregate-only、label-free features；case/candidate/evidence/slot/resource 标识、路径、URL 和原始行不得进入公开结果。该 `PASS` 不外推到完整 RCAEval 或其他 Parquet。
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
| `rcaeval` | MIT | ENABLED（仅五案例评估切片） | 已固定 revision、16 个资源、bytes/SHA-256、许可证据、aggregate-only 安全投影与回答盲评估；Replay/完整 benchmark 仍禁用 |
| 其他或许可不清来源 | 未确认 | DENIED | 新 ADR、法务/所有者可追溯许可证据、catalog 变更复核和完整 Gate |

## 4. 必须通过的 Gate

状态仅可使用 `NOT RUN`、`FAIL`、`PASS` 或带解除条件的 `BLOCKED`。`PASS` 只覆盖证据位置明确列出的已启用输入格式和流水线，不外推到其余白名单数据集。

| Gate | 验收要求 | 当前状态 | 证据位置 |
|---|---|---|---|
| Catalog schema | 未知字段、重复 ID、浮动版本、非 HTTPS、越界 host、缺许可或缺预算均拒绝 | PASS | `test_catalog.py`、默认 catalog 固定 commit/size/SHA |
| License default deny | 非白名单/许可证据变化时下载和 run 均拒绝；归属、证据 URL/SHA、复核日期随锁保留，安全摘要随报告保留 | PASS | `test_catalog.py`、`test_workspace.py`、`test_pipeline.py`、`THIRD_PARTY_DATA.md` |
| Fetch integrity | 临时文件下载、大小上限、SHA-256、允许重定向、断流/超时均 fail closed | PASS | `test_downloader.py`、core security review |
| Cache integrity | 锁定 artifact 命中时重新校验大小/SHA；损坏、替换、catalog 漂移均检测 | PASS | `test_downloader.py`、`test_workspace.py` |
| Archive safety | 启用 ZIP/TAR 前必须覆盖 traversal、链接、设备名、碰撞、nested/archive bomb | BLOCKED（当前只启用非归档 CSV/JSON/Parquet 资源，不接受 ZIP/TAR） | P3e 后续归档格式 Gate |
| Parser budgets | BubbleRAN CSV/JSON 行、列、字段、字符串、深度、非有限数和重复 key 预算有效 | PASS | `test_safe_json.py`、`test_adapters.py`、`test_schema.py` |
| RCAEval Parquet/schema budgets | 只接受 16 个锁定资源的精确 schema；row group、row/column/string、数值累计、引用与批次预算先限界后迭代，nullable metric 只跳过该 metric 缺失样本且两侧必须各有有效样本 | PASS（仅五案例切片） | `test_rcaeval_adapter.py`、`test_rcaeval_pipeline.py`、真实锁定上游 dispatch |
| Privacy gate | BubbleRAN CSV 精确列集；任意未知/敏感/自由文本列在行读取前拒绝；UE 标识/标签不进入 Observation、报告或 CLI | PASS（仅 BubbleRAN 精确 schema） | `test_adapters.py`、`test_pipeline.py`、哈希绑定的全量数据测试 |
| RCAEval aggregate privacy | ranker 只接收 label-free aggregate features，不接收 answer/slot/token/path/URL/resource SHA；公开结果省略 private sample、candidate、reference ID、artifact location 与 raw row | PASS（仅五案例切片） | `test_rcaeval_models.py`、`test_rcaeval_ranking.py`、`test_rcaeval_pipeline.py`、远程 privacy scan |
| Semantic adapter | BubbleRAN 源字段、单位、UTC、5G-SA资源、标签和缺失策略显式 | PASS | `adapters.py` 显式映射、`test_adapters.py` |
| RCAEval semantic adapter | 固定五案例 timing、metrics/logs/traces 以显式合同转换为 aggregate-only features；稳定 tie 与 `INCONCLUSIVE` 失败关闭 | PASS（仅五案例切片） | `rcaeval_adapter.py`、`rcaeval_models.py`、`test_rcaeval_adapter.py` |
| Provenance | dataset/lock/artifact/catalog/source-URL/license/adapter/contract/window 可从 lock、manifest 与报告反查；package 版本随 CLI 证据输出 | PASS | 默认 catalog、workspace lock、`LabBundleManifest`、`test_pipeline.py` |
| Deterministic evaluation | 相同输入与配置生成相同摘要、计数和指标；BubbleRAN ground truth/prediction 类型隔离；RCAEval 在答案前 rank/seal/commit、答案后验证同一 commitment 并复用原 seals | PASS（BubbleRAN + RCAEval 五案例评估） | `test_evaluation.py`、`test_pipeline.py`、`test_rcaeval_*`、哈希绑定真实上游运行 |
| Replay confinement | 非 loopback、Cloud Profile、真实动作、GCP 配置或超速/超量均拒绝；transport 禁代理/重定向并逐事件重验 endpoint/env/event；paced runner 只可对 network/timeout 做明确有限重试 | PASS（BubbleRAN ReplayPlan + transport + receiver） | `test_replay.py`、`test_loopback_sink.py`、`test_paced_runner.py`、`test_fault_receiver.py` |
| Replay checkpoint persistence | 显式本地 workspace/checkpoint 目录；checkpoint 严格绑定 plan/window/event/payload，严格 JSON/大小预算、原子保存、单调不回退、非阻塞单 writer；损坏、跨 plan、旧 window、路径/链接/Windows drive 越界失败关闭 | PASS（caller-owned local store） | `test_checkpoint_store.py`、`test_bubbleran_replay_governance.py` |
| Replay idempotency | durable-before-202；持久 checkpoint 重启零投递；精确重放、响应丢失恢复和 settled replay 不重复创建 Incident/Audit/Action/Verification，改变 payload 冲突 | PASS（每 source 独立 Incident） | `test_fault_receiver.py`、`test_bubbleran_replay_governance.py` |
| Repository hygiene | Git/构建产物/wheel 不含第三方大文件、原始数据、quarantine 或本机秘密 | PASS | 当前源码构建的 domain/lab wheels 内容扫描、源码树外强制安装、`pip check` 与受控清理 |
| Offline E2E | 无 GCP、无模型 API 完成数据适配/评估；本地 loopback 上完成 Replay→Incident→RCA→审批→模拟验证 | PASS（BubbleRAN 切片） | `test_local_dataset_pipeline.py`、`test_bubbleran_replay_governance.py` |
| RCAEval offline E2E | 16/16 已验证缓存上无网络重跑，结果与 fetch-and-evaluate 逐结构相等；答案扰动不改变 ranking bytes，只改变 evaluation | PASS（五案例评估；无 Replay） | `test_rcaeval_pipeline.py`、Data Lab dispatch 33385881296 |

## 5. 数据集验收矩阵

| 数据集 | Fetch/Lock | Privacy | Adapter | Offline Eval | Replay | License/Attribution | 总状态 |
|---|---|---|---|---|---|---|---|
| BubbleRAN | PASS | PASS | PASS | PASS | PASS（plan/wire/paced transport/persistent checkpoint/durable receiver/governance E2E） | PASS | PASS（当前单数据集切片） |
| NIST RAN Anomalous State | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| RANalyzer | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| TelecomTS | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| RCAEval | PASS | PASS（aggregate-only 五案例） | PASS（五案例） | PASS（回答盲五案例） | NOT RUN | PASS | NOT RUN（评估切片通过；Replay/整行 Gate 未通过） |

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
13. answer/slot/token/path/URL/resource SHA 注入 ranker、答案揭示前读取真值、揭示后重新排序、seal/batch commitment 扰动、重复 slot/truth、bool-as-int、超预算或不完整五案例批次。

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
- RC `6ba631929c312bbff27ef0ad4a9136d2cb390ae1` 的 [Data Lab CI run 33301104518](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104518) 为 `success`，其 Python 3.12/3.13 当前边界与 Python 3.12 + Pydantic 2.5.3 最低边界均为 `220 passed, 3 skipped`；[Assurance CI run 33301104511](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104511)、[Local CI run 33301104520](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104520) 与额外 [Cloud CI run 33301104595](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104595) 也全部 `success` 且 `headSha` 相同。Cloud run 仍不替代 Staging 验收。
- [Data Lab artifact 9728965310](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104518/artifacts/9728965310) 为 `VERIFIED RC`：`telco_lab-0.1.0` 74,425 bytes / SHA-256 `4c646e7ad618884284bf5f0b484b579c19dbcaccc8ef01571eccfc4ea197d900`；manifest SHA-256 `9c2f4e2c5a9d35cb900a94b64f3ef6b2f604ceb34910a4638f926791f0b9d63d`；CycloneDX 1.4 SBOM SHA-256 `bfe4d7233ee1efeba99d89ebb2fd1140529f5fa7c2b6e62a4721f9c610810105`。
- [Local artifact 9728983176](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104520/artifacts/9728983176) 与 [Assurance artifact 9729018617](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33301104511/artifacts/9729018617) 也为 `VERIFIED RC`。三份 artifact 的运行、GitHub metadata 和 manifest 三层均绑定受测 SHA/run ID；下载后的 ZIP digest、逐文件字节数/SHA、runtime inventory、wheel scan、CycloneDX 1.4 与 `pip-audit==2.10.1` 交叉验证 0 错误、0 已知漏洞。
- 本段证据回填提交晚于且不等于受测 RC；artifact 于 2026-09-13 到期。SHA-256 证明完整性而非发布者身份，签名/attestation、hash-locked 离线安装、SPDX、独立 secret/SAST/license policy 与完整发布终验仍开放。远程矩阵不关闭跨事件聚合或 RCAEval，P3e 继续保持 `IN PROGRESS`。

### 8.1 S7-03 独立 fixture 答辩 Gate

- 唯一入口 `python tools/local-stack/run_bubbleran_defense_demo.py --offline --approve-local-simulation` 使用 4 条 `CODE_GENERATED_SCHEMA_FIXTURE` 记录。它只证明冻结 schema 与垂直治理链路，不包含、不下载也不冒充完整 BubbleRAN 上游 benchmark。
- 真实 loopback TCP 创建 4 个独立 Incident/SourceAssociation；持久 checkpoint 首次为 `4/4/4`，重新打开完成态 store 后为 `0/0/0`。绕过 checkpoint 再投递 4 条 settled event 后，Incident/Audit/SourceAssociation/Idempotency delta 全为 0，且四个 Incident 完整对象深等。
- 四个固定终态逐支为 `RESOLVED/PASSED`、`REOPENED/FAILED`、`REJECTED/NOT_RUN` 与审批过期 `FAILED/NOT_RUN`。ActionRun/VerificationRun 总计 `2/2`；动作固定 `LOCAL_SIMULATION`，`side_effects=false`。
- 受测 RC 为 `46318cbf84b65c3060358dffb49b829479803308`。[Assurance run 33366606140](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140) jobs 99408450337/99408450434/99408450435/99408450555、[Local run 33366606118](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606118) jobs 99408450116/99408450386、[Container run 33366606112](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606112) jobs 99408450317/99408503334 全部成功：8/8 jobs、122 个 success steps，11 个条件 skips 符合设计。
- 唯一证据载体是 Python 3.12 Assurance [VERIFIED RC artifact 9748618894](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33366606140/artifacts/9748618894)，名称 `telco-assurance-release-py3.12-attempt-1`，248,105 bytes，archive SHA-256 `975a60d326eb97ea2557ae237bbff9dd957b327cdc04c2d117ef8cb58f262f14`。独立下载确认 13 文件精确闭包（12 payload + manifest）、manifest `PASS` / `failures=[]`，且无 CSV、DuckDB、JSONL 或 checkpoint。
- `local-bubbleran-defense-summary.json` 为 2,374 bytes / SHA-256 `161354c5715b8a46730debcf7dd37658158d1ec338b469aa24f2bb2f3ddbc855`；移除 stdout-only `report` envelope 后重建持久报告为 2,225 bytes / SHA-256 `4a07a35b7c5ca2e2f256351dc45bfdd7c5eac069b15f78d672f1eafa9c2aff42`。summary/report 不含禁用 ID、路径、原始记录或 source location 字段。
- [独立 fixture 答辩运行手册](../runbooks/local-bubbleran-defense-demo.md) 冻结 6–8 分钟流程、字段 allowlist、稳定失败、身份绑定清理、artifact 复核和十项 `not_claimed`。文档提交晚于且不等于受测 RC。

该 Gate 只把 S7-03 窄切片标为 `DONE`，并确认 P3e-5 的独立 fixture 答辩入口已完成；P3e-5/P3e 仍为 `IN PROGRESS`。RCAEval、第二路径、跨事件/episode 聚合、完整上游 BubbleRAN 流程、容量/生产准确率和发布终验仍开放。S4/Workflow E/P7/S7 保持 `IN PROGRESS`；Gate E/G5/G2/G4 保持开放；S2-04 为 `BLOCKED`；P6 统一 UI 为 `NOT STARTED`。

### 8.2 S7-04 RCAEval 五案例回答盲评估 Gate

- 唯一入口 `telco-lab --workspace .local/networkagent-rcaeval run rcaeval-re2ob-multisource-rca --accept-license MIT` 锁定数据 revision `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`、16 resources / 53,433,532 bytes、closure `c99ced28f1cb56464820a9570ead783de753c31ad36f5d7d29de594115101fb1` 与 MIT evidence `c2990bbe2e040a8d2f55fdd47c4f47f02223d8ea098e5d6e8851585a64956a0f`；缓存不进 Git、wheel 或 release artifact。
- 回答盲顺序固定为：验证并打开完整 closure；仅加载 label-free timing；构造五个 case/aggregate-only features；在私有 slot 不进入 ranker 时完成五个 rank/seal；创建绑定 catalog/lock/closure/case digests/features/seals 的 batch commitment；随后才加载 answers；post-reveal 校验同一 commitment并复用原 seals；最后才创建私有 truth mapping 并评估。`answer_blind_ranking`、pre-reveal commitment、post-reveal validation 和 ranking reuse 均通过，`externally_timestamped=false`。
- 五案例为 5 ranked / 0 inconclusive；AC@1..5、Average@5 与 MRR 均为 1,000,000 ppm。ownership validity 104,838 ppm 只表示 39/372 references 的归属比，不是准确率或标签质量。privacy policy `rcaeval-aggregate-only-v1` 为 `PASS`，所有私有明细/标识/位置/原始行均 `OMITTED`。
- 受测 RC `b8a9e958a0a3354634f87e2fbc8f76aaf60913dd` 的 [Data Lab push 33385845017](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385845017)、[Assurance push 33385845041](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385845041) 与 [Container push 33385844990](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385844990) 均成功；显式 [Data Lab dispatch 33385881296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385881296) 的 jobs 99468272496/99468272632/99468272707 全绿。
- 唯一 [Data Lab VERIFIED RC artifact 9755569487](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385881296/artifacts/9755569487) 为 148,959 bytes，archive SHA-256 `8afc11102a17310c78e1295a15a758396d904c9aea964985801c0e9e30fd88f4`，14 天保留至 `2026-09-14T11:13:40Z`。独立下载确认 10 文件闭包、9/9 payload bytes/SHA、manifest `PASS`；2,408-byte canonical summary SHA-256 为 `999a35e25bfa53aaf3ef7f86f7eaf4b596c17b25366ba85cf7193724a41d0b38`。两个 wheels / 47 members 无 Parquet/Arrow/Feather/IPC/ORC/CSV/DuckDB/JSONL；CycloneDX 1.4 有 8 components（含 PyArrow 25.0.0），`pip-audit` 0，wheel scan `PASS`。
- [RCAEval 运行手册](../runbooks/local-rcaeval-evaluation.md) 冻结许可、唯一命令、缓存/离线复验、判读、固定失败、安全清理、制品复核和全部 17 项精确 `not_claimed`。本次文档提交晚于且不等于受测 RC。

该 Gate 只把 S7-04 五案例回答盲离线评估窄切片标为 `DONE`。RCAEval Replay、完整 benchmark/全数据覆盖、上游实现等价、生产准确率、跨数据集/统计泛化、因果识别、在线/流式评估、live remediation、外部时间戳、Cloud/OTel/dashboard 与所有总体 Gate 均未完成；P3e/S7/P7/S4/Workflow E 保持 `IN PROGRESS`，Gate E/G5/G2/G4 保持开放，S2-04 为 `BLOCKED`，P6 为 `NOT STARTED`。

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

- RCAEval 已完成 S7-04 五案例真实锁定上游、aggregate-only 适配、回答盲排序/评估、离线 exact result 与独立 artifact 复核；尚未覆盖完整 upstream benchmark/全数据、RCAEval Replay、跨事件聚合、跨数据集/统计泛化或生产准确率。
- loopback HTTP transport、paced runner、有限 transient retry、caller-owned 持久 checkpoint、Canonical Fault/Incident 业务接收器与真实 TCP 治理 E2E 已实现。checkpoint 仍只是非签名 continuation claim；store 为非阻塞单 writer，response loss 恢复依赖 receiver 幂等，不能作为共享数据库或认证 ACK。
- checkpoint 路径在 Windows 拒绝 UNC/device 并只接受 `DRIVE_FIXED`；POSIX mount topology 与恶意同用户 ancestor rename/swap TOCTOU 仍属于本机文件系统信任边界。
- 当前 receiver 为每 source event 独立 Incident，尚无 episode/跨事件聚合。受控 UL BLER 阈值只用于 BubbleRAN 本地测试 provenance，不是生产 RCA。
- BubbleRAN 当前使用全量基线和代码生成的极小 CI fixture；远程 RC artifact/SBOM/`pip-audit` 与 S7-03 独立代码生成 fixture 答辩入口已验收，但完整上游答辩、延迟/峰值内存预算、签名/attestation、离线 hash-lock 与完整发布证据仍待完成。
- 本 Gate 不包含真实 Engineer/MCP/GitOps/Operator 动作或任何 Cloud Staging 验收。
- NIST、RANalyzer、TelecomTS 的适配优先级将在前两个适配器通过 Gate 后确定。
- 公司对 CC BY-SA 派生数据演示/再分发的合规要求需要在对外交付前确认。
