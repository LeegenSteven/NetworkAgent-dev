# P3 Canonical Incident 一次性迁移手册

> 适用范围：Local Profile DuckDB schema `1.1` 中的 Canonical Incident，迁移到正式 P3 `Canonical*V2` Spanner 表族。
>
> 该流程不是双写器，也不会自动修改旧 `Incident` 表。

## 支持边界

- 自动导入仅接受 `DETECTED`、`revision=0` 的 Canonical Incident。
- Incident 快照保持原样；完整 `SourceEventAssociation` 的时间、actor、reason、幂等键和 trace 会逐条保留。
- 已推进生命周期、来源归属冲突、同一活动 correlation 冲突、缺失来源证明或带 `legacy_source` / `legacy_id` 的记录会进入隔离清单，不会猜测转换。
- 旧版 Spanner `Incident(id, issue, strategy, root_cause, ...)` 不是 Canonical 模型。本阶段保持只读；必须先经过人工批准的字段映射，生成合规 Canonical bundle 后才能使用本工具导入。
- Bundle 使用 SHA-256 校验和检测传输或编辑错误，**不是数字签名**。文件仍须放在受访问控制的存储中，并通过可信渠道交接。

## 前置条件

1. 备份 DuckDB 源文件，并确认应用已停止写入。
2. 运行显式初始化/迁移，使 Local Profile schema 为 `1.1`；运行时和导出命令不会自动修复 schema。
3. 目标 Spanner 已由 `telco-cloud-schema apply` 建立正式 P3 基线。
4. 实际导入使用短期角色 `telco_migration_importer`；不要使用 Fault、MCP、Outbox 或 schema-admin 身份。
5. 任何由未提交 P3 草稿 DDL 创建的 Canonical 数据库必须重建，不能作为正式迁移目标。

## 操作步骤

先从只读 DuckDB 快照导出：

```powershell
telco-cloud-migrate export-duckdb `
  --database D:\backup\telco-local.duckdb `
  --output D:\backup\canonical-migration.json `
  --source-profile local-lte-demo
```

导出使用只读连接；缺表、版本错误或缺少全局来源唯一索引时失败关闭，不会执行 DDL。

离线校验并查看隔离项：

```powershell
telco-cloud-migrate validate `
  --input D:\backup\canonical-migration.json

telco-cloud-migrate import-spanner `
  --input D:\backup\canonical-migration.json `
  --offline-plan
```

`--offline-plan` 不读取云凭据、不连接 Spanner、也不写目标库。它只验证并分类 bundle 自身的 schema、隐私、深度、单条/总大小、来源归属和活动 correlation；它不是目标库预检，目标库已有数据造成的冲突只能由真实导入事务原子判定。命令刻意不使用 `dry-run` 名称，避免把离线计划误解成与在线提交完全同构。

人工复核输出中的 `quarantine_items`。每项只包含稳定的 `entry_index`、安全 `incident_id` 和固定 `code`。退出码 `3` 表示存在隔离项，不能当作全量成功。

实际导入前设置精确的数据库角色：

```powershell
$env:TELCO_SPANNER_DATABASE_ROLE = 'telco_migration_importer'
telco-cloud-migrate import-spanner `
  --input D:\backup\canonical-migration.json
```

导入按 Incident 独立提交并使用稳定幂等键。进程中断后可安全重跑同一个未经修改的 bundle：已提交前缀会作为 replay 返回，未提交条目继续执行。重放会交叉验证 Incident、来源关联、revision-0 审计和活动索引，不能用一条残缺的幂等记录掩盖数据丢失。

## 隔离代码

| code | 含义 | 操作 |
|---|---|---|
| `UNSUPPORTED_LIFECYCLE` | 已不是 DETECTED/revision-0 | 另行设计历史导入，不自动降级 |
| `LEGACY_REQUIRES_MAPPING` | 存在 legacy 标记 | 完成人工字段映射和证据审查 |
| `AMBIGUOUS_SOURCE_OWNERSHIP` | 同一 source event 指向多个 Incident | 对照原始事件记录确定唯一 owner |
| `AMBIGUOUS_CORRELATION_OWNERSHIP` | 多个待导入活动 Incident 共用 correlation | 合并/关闭错误候选后重新导出 |
| `MISSING_SOURCE_PROVENANCE` | aggregate 引用的 source 缺少关联记录 | 从可信审计源补齐，禁止伪造时间或 actor |
| `TARGET_CONFLICT` | 目标库已有 ID、source 或活动 correlation 冲突 | 查询目标 Canonical 数据后人工裁决 |

## 回滚与收尾

- 工具不删除目标数据，也不提供反向“自动回滚”。切流前保持 Fault Pipeline 为 `shadow`，以便回退到 `legacy` writer。
- 只在新的、尚未承载生产写入的目标数据库中演练；若演练需重做，删除并重建该受控测试数据库，不清空共享/生产数据库。
- 导入完成后撤销迁移主体对 `telco_migration_importer` 的使用权限；常驻 Fault/MCP/Outbox 身份不得继承该角色。
- 在 Cloud Staging 验证 FGAC 负权限、OIDC、DLQ、Pub/Sub 重投和审计查询后，才能切换到 `canonical` 模式。
