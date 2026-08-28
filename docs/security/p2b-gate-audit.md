# P2b Assurance / A2A 发布 Gate 审计

> 日期：2026-08-28
> 范围：Local Assurance A2A 服务、Supervisor/AG-UI 桥、A2A 0.2.16/0.3.11 兼容边界、持久确认与发布工作流
> 结论：**PASS（无未关闭 Gate blocker 或 High）**

## 1. 已验收边界

- `telco-assurance-agent` 是独立 Python 3.12/3.13 服务，精确使用 `a2a-sdk[http-server]==0.3.11`，不依赖 ADK、模型、云 SDK 或凭据。
- P2b 服务仅允许 loopback bind host 与 loopback AgentCard URL；它没有认证能力，不允许作为外网或跨主机生产入口。
- `init` 负责 schema/样例初始化，`run` 只打开已初始化的 DuckDB；运行路径不执行 DDL 或 CSV 导入。
- 扫描、确认与分析只接受一个权威 DataPart；TextPart 只用于展示，不能触发 Incident 写入。
- `CONFIRM` 是唯一 Incident 写入口。服务端 challenge 只保存 SHA-256，并绑定 task、context、workflow、trace、candidate、snapshot 与业务幂等键。
- RCA 使用服务端 Incident 快照，返回只读 `rca_result`；不保存报告、不生成动作、不推进 Incident。
- Supervisor 对 A2A 内容执行大小、深度、隐私、关联标识、allowed/required 字段和嵌套结构校验；未知字段、乱序状态、异常 EOF 和错误 task/context 均 fail closed。
- P2b Assurance 的进度、错误与 continuation 事件只发往 thread 所属 sid。普通 `agui_message` 禁止携带 ToolMessage；审批续跑只接受服务端记录的 exact pending call 与工具名。

## 2. 关闭的问题与回归证据

| Gate | 关闭方式 | 自动化证据 |
|---|---|---|
| SDK 前置校验可能反射被拒请求 | 在 A2A SDK 前增加有界 ASGI JSON-RPC gate；重复键、非有限数、超限、隐私或 Pydantic 失败统一返回固定错误，不携带 `error.data` | `networkagents/assurance/tests/test_app.py` |
| 文本或伪造 ToolMessage 推进写入/模型续跑 | Assurance 只解析严格 DataPart；Socket 普通消息拒绝 `role=tool`；ADK continuation 只走 exact pending call | `test_protocol.py`、`test_socket_tool_gate.py`、`test_adk_continuation.py` |
| 服务端工具名丢失为 `unknown` | 消费前读取 server-observed name，构造同 ID 的 Assistant ToolCall + ToolMessage，并验证 FunctionResponse 名称 | `test_adk_continuation.py` |
| 跨 Socket 广播或 thread 冒用 | 建立 thread→sid ownership，所有进度、错误和 continuation 只单播到原 room | `test_a2a_parts.py`、`test_remote_preflight.py` |
| 确认预览后数据漂移或重放重复写 | 确认前按原 scope 重扫；candidate/snapshot/业务指纹不一致拒绝；成功重放返回原 Incident，revision/history 不增加 | `test_service.py`、`tests/e2e/a2a/test_assurance_http.py` |
| 重启后 challenge/task 丢失或容量永久耗尽 | DuckDB schema 1.1 持久化 Task/Pending；未写入 claim 到期回收，已写入 claim 有 15 分钟恢复保留期，关联孤立 Task 同步清理 | `test_stores.py`、真实 HTTP crash replay |
| A2A 0.2.16/0.3.11 wire 与 Part 顺序差异 | 0.2.16 独立生成 golden fixture，0.3.11 环境验证 camelCase wire、两种 Part 顺序、全部状态及业务 DTO | `tests/contracts/a2a/` |
| Supervisor ADK 1.18.0 已知风险 | Supervisor 精确升级到修复版 `google-adk==1.28.1`；Assurance 完全不引入 ADK；专用 CI 环境运行真实 continuation 门禁 | `telco-assurance.yml` 的 `supervisor-adk-smoke` |
| 审批状态写失败后不可重试 | 先持久化稳定的 trusted decision，再消费 pending；写失败不启动 continuation，消费失败重试复用原 confirmation IDs | `test_host_tool_result_retry.py` |

## 3. 本地发布证据

| 门禁 | 结果 |
|---|---:|
| P1 + P2a 领域、本地适配器与 Local E2E | 476 passed |
| Assurance 协议、存储、服务与 ASGI | 26 passed |
| 真实 A2A HTTP detect/confirm/analyze/reject/cancel/restart | 4 passed |
| A2A 0.3.11 当前边界 + legacy wire contracts | 70 passed |
| Supervisor（精确 ADK 1.28.1/A2A 0.3.11） | 57 passed |
| A2A 0.2.16 golden fixture `--check` | passed |
| 三个 wheel 源码树外安装、CLI/import isolation、`pip check` | passed |

上述门禁已配置为在 Python 3.12/3.13 CI 中分别运行；远程成功链接在首次执行完成后回填实施计划。

## 4. 已知非阻断限制

1. P2b 仅支持本机回环访问。认证、TLS、跨主机部署和速率限制属于 P7；在此之前不得通过反向代理或端口映射对外暴露。
2. DuckDB 仍按单 writer 运行；多进程并发写可能得到文件锁错误。Cloud Profile 将用 Spanner 事务。
3. 已写 Incident 后的 crash replay 只保证到 challenge 过期后 15 分钟；超过窗口需查询 Incident/审计后人工恢复。
4. trusted state、pending 消费与 ADK continuation 不是跨存储事务；进程恰在 pending 删除后崩溃仍可能需要人工恢复。
5. UI thread ownership 与锁是单进程内存状态；断线后已知 thread ID 仍可能重新绑定，因此 P2b 只允许单用户/可信本地会话。多用户或多副本部署需要认证 resume token、sticky session 或共享协调存储。
6. Supervisor 镜像 Assurance 的协议字段，当前由 current/legacy contract tests 防漂移；后续应生成或共享无框架 schema，避免手工双处维护。
7. 除 Supervisor 外，部分 legacy Agent 仍精确依赖存在安全公告的 ADK 1.18.0；它们继续隔离且不得新增外部暴露，P7 分批升级。
8. A2A SDK 0.3.11 在当前 Starlette 中产生上游弃用警告；版本迁移必须先通过现有 wire 与生命周期契约，不能无验证升级到 1.x。
9. 旧的非 P2b `sendPushNotification` 仍保留全局广播语义；Assurance 路径不调用它，P6 合并 UI 时应将所有 legacy push 迁移到显式 room/订阅模型。
10. 当前审批组件的展示文案仍可能只概括候选数量；服务端会忽略 UI 回传的 task 内容并精确绑定原候选，因此完整性不受影响，但 P6 应由服务端渲染包含资源、KPI、时间窗和影响摘要的签名审批卡，确保用户获得充分信息后再确认。
11. 三个 Python 包仍使用 setuptools 已弃用的 TOML license table/classifier 形式；当前 wheel 可正常构建，P7 应在提高 setuptools 构建下界后迁移到 SPDX license expression，避免 2027 年后的构建兼容风险。
