# S7-04 本地 RCAEval 回答盲评估运行手册

> 工作包：S7-04（仅本手册定义的五案例窄切片）
> 受测 RC：`b8a9e958a0a3354634f87e2fbc8f76aaf60913dd`
> 分类：`PINNED_UPSTREAM_RCAEVAL_RE2OB_SLICE`
> 数据许可证：MIT
> 边界：Local Profile、真实锁定上游字节、无 GCP、无模型 API、无真实网络动作

## 1. 目标与唯一入口

本流程验证 RCAEval `re2ob` 的五案例固定切片：显式接受 MIT 许可后下载并校验 16 个锁定资源，把指标、日志和调用链转换为只含聚合量的 label-free 特征，在答案加载前完成五个排序及 seal/batch commitment，再揭示私有真值并计算有界整数 ppm 指标。唯一 fetch-and-evaluate 入口是：

```text
telco-lab --workspace .local/networkagent-rcaeval run rcaeval-re2ob-multisource-rca --accept-license MIT
```

命令只输出一行有界 JSON。它不启动服务、不访问 GCP、不调用模型、不进入 Assurance/治理动作链，也不回放 RCAEval 事件。首次运行需要网络下载；已有精确缓存时仍会逐件校验锁与内容。

## 2. 下载、版本与许可前提

运行前确认：

1. 当前源码提交精确为 `b8a9e958a0a3354634f87e2fbc8f76aaf60913dd`，并使用该提交构建或安装的 `telco-lab`。
2. 工作区精确为仓库下 `.local/networkagent-rcaeval`，不使用根目录、用户目录、共享目录、链接、junction/reparse point、UNC 或设备路径。
3. 数据 revision 为 `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`；catalog 预期 16 个资源、53,433,532 bytes，artifact closure SHA-256 为 `c99ced28f1cb56464820a9570ead783de753c31ad36f5d7d29de594115101fb1`。上游代码语义参考提交为 `526cdd5818ea9d8c2a34e869ebd637bc6b4fa4b8`，不是 catalog 锁，也不支撑上游实现等价声明。
4. MIT 许可证据 SHA-256 为 `c2990bbe2e040a8d2f55fdd47c4f47f02223d8ea098e5d6e8851585a64956a0f`，归属为 `RCAEval dataset contributors`。必须显式传入 `--accept-license MIT`；名称相似、上次同意或代码许可证均不能代替本次数据许可确认。
5. 缓存和 lock 只保存在上述 `.local` 工作区，不提交 Git，也不进入 wheel 或 release artifact。

任一 URL/host、revision、资源数、字节数、SHA-256、许可或 catalog/lock 绑定不匹配时必须失败关闭；不得改用浮动 revision、跳过校验或接受“最新”内容。

## 3. 回答盲顺序

必须按实现中的固定顺序判读，不可把答案加载提前：

1. 校验 catalog、manifest 与 closure，并打开全部 16 个已验证 artifact。
2. 只从 index 加载 label-free timing。
3. 构造五个 telemetry case，并适配为 aggregate-only features。
4. 对五个 case 全部完成 rank 与 seal；私有 slot 始终在 ranker 外部。
5. 创建 ranking batch commitment，绑定 catalog、lock、closure、case digests、features 与 seals。
6. commitment 创建后才揭示并加载 answers。
7. 揭示后重新校验同一 commitment，并复用原 seals；不得重新排序。
8. 最后创建私有 truth mapping 并执行 evaluation。

成功 JSON 必须同时满足：

- `answer_blind_ranking=true`
- `commitment_created_before_answer_reveal=true`
- `post_reveal_commitment_validation=PASS`
- `ranking_reused_after_reveal=true`
- `sealed_ranking_count=5`
- `ranking_algorithm=networkagent-multisource-shift-v1`
- `batch_commitment_sha256` 为 64 位小写十六进制；本次受测值前缀为 `e7756d…`
- `externally_timestamped=false`

commitment 只证明本次规范字节的一致绑定；它没有外部可信时间戳，也不隔离同进程恶意 ranker。

## 4. 结果判读

受测 RC 的固定结果如下：

| 字段 | 受测值 | 准确解释 |
|---|---:|---|
| `sample_count` / `ranked_count` | 5 / 5 | 仅固定五案例切片 |
| `inconclusive_count` | 0 | 本五案例无 `INCONCLUSIVE`，不代表任意输入都可判定 |
| `ac_at_1_ppm` … `ac_at_5_ppm` | 各 1,000,000 | 仅五案例上的 Accuracy@k |
| `average_at_5_ppm` | 1,000,000 | 仅上述五个 Accuracy@k 的固定平均 |
| `mean_reciprocal_rank_ppm` | 1,000,000 | 仅五案例的 MRR |
| `ranked_reference_count` | 372 | 聚合排名引用总数，不是原始行数或独立标签数 |
| `truth_owned_reference_count` | 39 | 私有真值拥有的引用数 |
| `candidate_ownership_validity_ppm` | 104,838 | `39 / 372` 的整数 ppm；不是准确率、召回率或证据标注质量 |

privacy 必须为 `policy=rcaeval-aggregate-only-v1`、`status=PASS`，并精确报告 `private_sample_details/candidate_details/reference_identifiers/artifact_locations/raw_rows=OMITTED`。输出中不得出现 case/candidate/evidence/slot/lock/resource 私有标识、路径、URL 或原始值。

## 5. 缓存与离线复验

首次唯一入口成功后，以下命令只用于独立复核，不是第二个演示入口：

```text
telco-lab --workspace .local/networkagent-rcaeval verify
telco-lab --workspace .local/networkagent-rcaeval evaluate rcaeval-re2ob-multisource-rca
```

`verify` 与 `evaluate` 不隐式联网。`verify` 必须确认 16/16 artifact 的 lock、bytes 与 SHA-256；`evaluate` 只有在完整 closure 已验证时才运行。离线 `evaluate` 的公开 `result` 必须与首次 `run` 的公开 `result` 逐结构相等，包括同一 batch commitment、指标、privacy 与 17 项 `not_claimed`；该 commitment 绑定同一 ranking bytes/seals，但公开 `result` 不暴露单个 seal。删除或替换任一资源、修改 lock/catalog、缺少受支持的 PyArrow（`>=21,<26`）或出现任何摘要漂移都必须失败，不得退化到部分案例结果；本次受测 RC 的 release artifact 锁定为 PyArrow 25.0.0。

## 6. 固定失败与处置

CLI 失败只返回稳定 code 和通用消息，不回显路径、URL、case、candidate、slot、resource 或原始行。常见处理如下：

| code | 含义 | 处置 |
|---|---|---|
| `license_not_accepted` | 未精确接受 MIT 数据许可 | 复核许可证据后重跑唯一入口；不得持久化隐式全局同意 |
| `invalid_catalog` / `lock_invalid` | catalog、资源合同或 workspace lock 漂移 | 停止评估，核对受测 RC 与 lock；不得手工放宽字段 |
| `artifact_unverified` / `size_mismatch` / `digest_mismatch` | 缓存缺失、长度或摘要不匹配 | 隔离该工作区后由唯一入口重新获取；不得就地改摘要 |
| `adapter_invalid_input` / `adapter_unsafe_field` | Parquet/schema/字段不满足冻结合同 | 作为数据或合同漂移处理，不输出被拒原值 |
| `adapter_limit_exceeded` | 行、列、字符串、累计数值或引用预算超限 | 失败关闭并复核锁定数据与实现预算；不得截断后继续计分 |
| `workspace_busy` / `workspace_unsafe` | 并发写或工作区身份/路径不安全 | 停止并发进程，重新确认精确本地目录，不绕过锁 |
| `internal_error` | 未分类内部失败 | 保存固定 code 与受测 RC 元数据，禁止输出或上传原始数据 |

任何一个 case 在 seal、commitment、post-reveal validation 或 evaluation 阶段失败，整批结果均不可作为 PASS；不得用剩余案例补出指标。

## 7. 安全清理

优先保留已验证缓存用于无网络复验。确需清理时：先停止所有 `telco-lab` 进程，解析并人工核对目标恰为当前仓库内 `.local/networkagent-rcaeval`；确认该目录及其祖先没有 symlink、junction 或 reparse point，且没有 workspace lock 持有者；随后只删除这个精确目录。禁止使用通配符、未解析环境变量、仓库根、`.local` 根、用户目录或跨 shell 拼接删除。身份、路径或并发状态有任何不确定时保留现场人工复核。

清理缓存不会撤销远程 release evidence；也不会让缺失缓存的离线 `verify/evaluate` 继续成功。

## 8. RC、远程矩阵与独立制品复核

受测 RC `b8a9e958a0a3354634f87e2fbc8f76aaf60913dd` 的 push 矩阵全部成功：

- [Data Lab run 33385845017](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385845017)
- [Assurance run 33385845041](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385845041)
- [Container run 33385844990](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385844990)

显式 [Data Lab dispatch 33385881296](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385881296) 也为 `success`：Python 3.12 [job 99468272496](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385881296/job/99468272496)、Pydantic 最低边界 [job 99468272632](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385881296/job/99468272632) 与 Python 3.13 [job 99468272707](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385881296/job/99468272707) 全绿。只有 Python 3.12 job 发布一次制品。

唯一制品是 [artifact 9755569487](https://github.com/LeegenSteven/NetworkAgent-dev/actions/runs/33385881296/artifacts/9755569487)，名称 `telco-lab-release-py3.12-attempt-1`，148,959 bytes，archive SHA-256 `8afc11102a17310c78e1295a15a758396d904c9aea964985801c0e9e30fd88f4`；创建于 `2026-08-31T11:13:40Z`，到期于 `2026-09-14T11:13:40Z`，保留 14 天。

`VERIFIED RC` 是本项目 workflow 根据精确 RC 绑定、manifest `PASS` 和独立闭包复核给出的验收标签，不是 GitHub artifact API 的原生字段。

独立下载/解包必须核对：

- 恰有 10 个非链接普通文件，即 9 条 manifest 记录加 manifest 自身；9/9 payload 的 bytes/SHA-256 匹配，manifest 为 `PASS`。
- `release-evidence/rcaeval-upstream-summary.json` 恰为 2,408 bytes，SHA-256 `999a35e25bfa53aaf3ef7f86f7eaf4b596c17b25366ba85cf7193724a41d0b38`；按 UTF-8、key 排序、紧凑分隔符和末尾单换行重建后逐字节相同。
- 两个 wheel 共 47 个成员，不含 Parquet、Arrow、Feather、IPC、ORC、CSV、DuckDB 或 JSONL；上游 53,433,532 bytes 数据未进入制品。
- runtime inventory 为 2 个 first-party wheel + 6 个 runtime package；CycloneDX 1.4 SBOM 有 8 components 且包含 PyArrow 25.0.0；`pip-audit` 为 0，wheel scan 为 `PASS`。

artifact SHA-256 与 manifest 证明内容完整性，不是发布者签名、attestation、provenance 或外部时间戳。本次文档提交晚于且不等于上述受测 RC。

## 9. 精确 `not_claimed`

输出必须按以下顺序包含全部 17 项，不能删减、改名或重排：

1. `COMPLETE_UPSTREAM_RCAEVAL_BENCHMARK`
2. `FULL_RCAEVAL_DATASET_COVERAGE`
3. `UPSTREAM_RCAEVAL_IMPLEMENTATION_PARITY`
4. `INDEPENDENT_EVIDENCE_LABEL_ANNOTATIONS`
5. `MALICIOUS_IN_PROCESS_RANKER_ISOLATION`
6. `PRODUCTION_RCA_ACCURACY`
7. `CROSS_DATASET_GENERALIZATION`
8. `STATISTICAL_SIGNIFICANCE_OR_GENERALIZATION`
9. `CAUSAL_IDENTIFICATION`
10. `LIVE_NETWORK_REMEDIATION`
11. `ONLINE_OR_STREAMING_EVALUATION`
12. `EXTERNALLY_TIMESTAMPED_COMMITMENT`
13. `CLOUD_OR_GCP_DEPLOYMENT`
14. `OPEN_TELEMETRY_OR_DISTRIBUTED_TRACE`
15. `UNIFIED_DASHBOARD`
16. `GATE_E_OR_G5_CLOSURE`
17. `P3E_OR_S7_OVERALL_CLOSURE`

因此只将 S7-04 五案例 RCAEval 回答盲离线评估窄切片标为 `DONE`。S7、P7、P3e、S4 与 Workflow E 仍为 `IN PROGRESS`；Gate E/G5/G2/G4 仍开放；S2-04 仍为 `BLOCKED`；P6 仍为 `NOT STARTED`。
