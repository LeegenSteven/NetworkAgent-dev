# P2a Local Profile Gate 安全审计

- 审计日期：2026-08-28
- 范围：`telco-domain` 的 Metric/Contract API，以及 `telco-local` 的数据库、Incident Repository、规则、Detector、Telemetry、RCA、文档检索、组合根、CLI 与完整资产 E2E。
- 方法：先把可复现问题固定为失败测试，再实施最小修复；复核不依赖模型、云服务或网络访问。

## Gate 结论

**PASS：未发现仍开放的 Gate blocker 或 High。** RCA、规则重放、确认/幂等、Incident 持久化、数据投影、资源与时间隔离、容量预算、直接端口隐私及 CLI/E2E 的 Gate 项均已修复并有回归测试。

## 状态表

| 领域 | 原始风险 | 修复与证据 | 状态 |
|---|---|---|---|
| 确认边界 | 预览候选可能在数据或规则变化后被提交 | `confirm` 重新扫描并绑定完整规则/证据内容；旧候选使用新 key 会失败；扫描不写 Incident | 已修复 |
| 确认重放 | 同 key 但 actor、reason 或 trace 不同可能被当作成功重放 | 写请求指纹包含 actor、reason、trace 与业务载荷；冲突抛 `IdempotencyConflictError` | 已修复 |
| 规则历史重放 | RCA 可能用 current 规则解释历史 Incident | Detector 仅用 current；RCA 按 `rule_id + version` 精确加载历史版本，校验 comparator、threshold、unit 与 provenance，不回退猜测 | 已修复 |
| 同版本内容漂移 | 规则文件可原地改写而 version 不变 | Detector v3 写入 `rule_content_hashes`；Detector 与 RCA 共享 `rule_content_sha256`；不一致为 `CONFLICT/INCONCLUSIVE` | 已修复 |
| RCA 逻辑身份 | 传输 `message_id` 改变会生成新报告 | 报告/结果逻辑身份绑定 Incident revision、报告版本、引擎、精确规则、规范证据与安全 resolution issue；`request_message_id` 仅用于关联 | 已修复 |
| EvidenceKind 隔离 | TRACE 规则可能消费同名 METRIC fact | 每条规则只合并其 `analysis.evidence_types` 明确允许的证据事实 | 已修复 |
| 证据资源范围 | 外部 eNB/Cell 或缺失/畸形 scope 可进入结论 | 仅信任 canonical `resource_scope`；必须与 Incident 的有序 `stable_identity()` 列表精确相等 | 已修复 |
| 证据时间范围 | 陈旧或无界证据可支撑当前 Incident | 时间必须有时区、顺序合法、包含于 Incident 窗口且与至少一个 violation 窗口相交；`collected_at` 必须落在证据窗口内 | 已修复 |
| 无 Incident 分析范围 | 空资源或空窗口可能触发无范围整库查询/异常退出 | RCA 在查询 Telemetry 前 fail closed，生成安全 issue 并返回 `INCONCLUSIVE` | 已修复 |
| 被拒证据展示 | 无效证据虽不参与事实，仍列入报告支持证据 | 无效 Telemetry 不进入 `evidence_refs` 或正文；只记录代码、稳定索引、类型与内容摘要哈希，不记录 URI/资源原值 | 已修复 |
| RCA 写入/动作 | 分析可能推进或持久化 Incident、产生动作 | Gateway 只读，报告为 `PROPOSED`，recommendations 为空；完整 E2E 验证 Incident revision/status/history/action 集合不变 | 已修复 |
| 数据隐私投影 | `SELECT *` 可能把宽表额外列及订户标识写入 DuckDB | Performance 与 Cell Trace 均使用显式 allowlist；Trace outcome 归一化；数据库字节级测试验证禁止字段/值未落盘 | 已修复 |
| LTE 数字标识伪装 | 无标签的 IMSI/MSISDN/IMEI 可伪装为 eNodeB/Cell 数字并跨越模型边界 | CSV 在持久表创建前校验 1–9 位 ASCII 十进制及 `0..268435455` 值域并去前导零；Telemetry 对数据库行、selector 与 Incident scope 使用同一 helper 再验证；错误不回显原值 | 已修复 |
| 规则/文档隐私 | 合法 JSON/Markdown 文本可携带订户数据并进入模型上下文 | 规则加载后统一隐私断言且错误不回显；文档逐 chunk 检查，查询本身也先检查 | 已修复 |
| 规则/文档容量 | 文件、总字节、密集标题/chunk、候选与查询可造成资源耗尽 | 明确限制文件数、单文件/总字节、每文件 chunk、候选、查询字符与词项；超限 fail closed | 已修复 |
| SQL/资源选择 | eNB 与其 Cell 选择器并用会把 sibling Cell 混入；动态 SQL 可注入 | 子 Cell 选择器优先于父 eNB；KPI 白名单、固定 SQL 片段与参数绑定；selector 数量受限 | 已修复 |
| 重复源行 | 相同业务键可能产生重复 observation/event ID | 身份包含 DuckDB `rowid`；重复行测试验证 observation、source event 与 candidate ID 唯一且重置后稳定 | 已修复 |
| Incident 容量/隐私 | 超过 Contract 预算或写元数据带敏感值仍可持久化 | Repository 对 aggregate、updates、actor/reason/trace/idempotency 执行隐私、深度、字符和 256 KB 序列化预算检查 | 已修复 |
| 关联事件 provenance | 后续 source event 只相关联但不可追踪 | 事务内写入独立 association 表及 idempotency/actor/reason/trace provenance；同一 source event 可稳定找到原 Incident | 已修复 |
| 进程内并发 | 两个 writer 可能同时越过 revision 检查 | 路径级重入锁、事务和 CAS；并发测试只有一个 writer 成功，另一个得到 revision conflict | 已修复 |
| Detector/Telemetry 直接出站隐私 | 直接 Python 调用可能绕过 CLI 的最终 `assert_model_safe` | scan/confirm 的外部 ID 与 reason 在入口检查，Detector 与 Telemetry 的公开结果在返回前再次检查；错误不回显 | 已修复 |

## 冻结的 RCA 安全语义

1. Local v3 Detector 产生的 Incident 必须带精确规则版本与 canonical `rule_content_hashes`。缺失、部分、冲突或内容漂移均不得静默使用 current 规则。
2. Telemetry `resource_scope` 是按 `resource_id` 排序后的 `ResourceReference.stable_identity()` 列表。RCA 不从 URI、摘要或 fact 名称推断资源。
3. 每条 METRIC/TRACE EvidenceReference 都必须有合法的 `window_start/window_end`。窗口须位于 Incident 窗口内，并与至少一个 KPI violation 窗口相交。
4. 任一 Telemetry scope/time 校验失败，整个 RCA 结论降为 `INCONCLUSIVE`。被拒证据不作为报告支持证据展示。
5. 规则 predicate 只能读取该规则声明的 EvidenceType。证据类型存在不等于所需 fact 存在；缺 fact 不得形成根因。
6. RCA 不执行动作、不生成 remediation recommendation、不保存报告、不推进 Incident；输出固定八节中文报告且状态为 `PROPOSED`。

## 红测与修复证据

下列测试由问题复现转为永久回归测试：

- `test_trace_rule_never_consumes_same_named_metric_facts`
- `test_gateway_marks_foreign_resource_evidence_inconclusive`
- `test_gateway_marks_missing_resource_scope_inconclusive`
- `test_gateway_rejects_malformed_scope_and_time_metadata`
- `test_gateway_marks_stale_evidence_window_inconclusive`
- `test_gateway_fails_closed_before_unscoped_telemetry_query`
- `test_markdown_repository_bounds_query_characters`
- `test_markdown_repository_bounds_query_terms`
- `test_redelivery_message_id_does_not_change_logical_rca_identity`
- `test_gateway_refuses_same_version_rule_content_drift`
- `test_confirm_replay_precedes_changed_telemetry_but_new_key_rescans`
- `test_query_kpis_uses_the_most_specific_resource_selector`
- `test_incident_persistence_matches_contract_size_and_depth_budgets`
- `test_performance_import_is_an_explicit_privacy_allowlist`
- `test_scan_rejects_sensitive_envelope_ids_without_echo`
- `test_confirm_rejects_sensitive_boundary_fields_without_echo`
- `test_public_telemetry_ports_fail_closed_on_sensitive_resource_values`
- `test_import_rejects_non_lte_identifiers_without_echo_and_rolls_back_reset`
- `test_import_accepts_28_bit_boundaries_and_normalizes_leading_zeroes`
- `test_query_kpis_rejects_unlabeled_subscriber_id_after_database_tamper`
- `test_query_kpis_rejects_invalid_canonical_resource_selectors_without_echo`
- `test_query_kpis_normalizes_database_rows_and_resource_selectors`
- `test_collect_evidence_validates_and_normalizes_incident_resource_scope`

本轮新增的 EvidenceKind、scope/time 与 query-budget 断言在旧实现上共形成六项预期失败；修复后 RCA/Document 定向集为 33 passed。

LTE 数字标识 High 的首轮攻击性回归在旧实现上形成 16 项预期失败；复核又补入长前导零碰撞用例。共享 helper、双 CSV 导入前校验及 Telemetry 出站再验证完成后，Database/Telemetry 定向集为 33 passed。无效 `reset=True` 导入会回滚到原数据库快照，拒绝值不会写入表或错误信息。

## 验证矩阵

在仓库根目录、正确设置 `packages/telco-domain/src` 与 `packages/telco-local/src` 的 `PYTHONPATH` 后执行：

```text
pytest packages/telco-local/tests -q -p no:cacheprovider
pytest packages/telco-domain/tests -q -p no:cacheprovider
pytest tests/e2e/local/test_local_assurance.py -q -p no:cacheprovider
```

最终结果在 Pydantic 2.5.3 与 2.13.4 上完全一致：

- `packages/telco-local/tests`：149 passed
- `packages/telco-domain/tests`：318 passed
- `tests/e2e/local/test_local_assurance.py`：1 passed

双矩阵合计均为 468 passed。

完整资产 E2E 的固定断言包括：13,440 条 performance、579 条安全 Trace、15 个唯一候选、扫描零 Incident 写、确认一次写且精确重放、RCA 为 conclusive、八节报告、零 recommendation、零 Incident/RCA/action 持久化副作用，以及结构化输出隐私检查。

## 残余与运营限制

以下不构成本轮确认/隐私 Gate 绕过，但应进入后续风险台账：

1. **DuckDB 跨进程写竞争（Medium/运营）**：当前路径锁只覆盖同一 Python 进程。两个独立进程同时写同一数据库时，DuckDB 可能让其中一个以文件锁异常失败；它是 fail closed，不会双写，但调用方需要单 writer 部署、外部互斥或有界重试。
2. **pre-v3 历史 provenance（Medium/兼容）**：没有内容哈希的旧 versioned Incident 只能证明版本字符串，不能证明同版本文件内容未漂移。新写全部使用 v3 mapping；旧数据迁移或审计时应补快照/哈希并标注 legacy。
3. **多规则/多资源归因（Medium/后续）**：当前 Local Detector 每个候选为单规则、单资源 episode；未来若允许一个 Incident 聚合多个独立规则或多个 eNB，应把 evidence scope 细化到 rule/violation，而不是只做 Incident 级聚合。
4. **CLI 组合初始化副作用（Medium/运营）**：CLI 的 detect/analyze 会先运行幂等数据库初始化并刷新本地 view；这不会写 Incident，但严格只读部署应把 init 与运行命令分离，并以只读数据库权限运行分析进程。
