# 从手机上网到网络故障：读懂本项目中的 LTE 电信字段

> 本文面向第一次接触移动通信数据的读者。目标不是背诵缩写，而是建立一条完整、可复用的理解链路：一部手机怎样接入 LTE 网络、怎样建立业务承载、怎样传输数据，以及网络为什么会把某些现象判断为异常。

## 1. 先确定本文讨论的网络范围

这个项目使用的是 LTE（通常也称 4G）无线接入网和 EPC 核心网概念，核心分析对象是：

- LTE 基站及其小区的周期性性能数据；
- 用户设备在接入、建立承载、传输数据和释放连接过程中的信令跟踪数据；
- 由这些数据计算出的异常 KPI；
- 围绕异常事件开展的根因分析。

因此，本文主要解释 LTE 术语。5G 中虽然有许多相似概念，但节点名称和部分协议已经变化，不能把本文中的 `eNodeB`、`MME`、`S1` 等名称直接当作 5G 字段使用。

可以先把本项目中的网络想象成下面这条路径：

```text
用户设备 UE
    │ 无线连接
    ▼
小区 Cell ── 属于 ── eNodeB 基站
    │
    ├── S1 信令 ──► MME：负责接入控制和移动性管理
    │
    └── 用户数据 ──► SGW：负责转发用户数据
```

当用户打开手机上的应用时，手机并不是立即就能把数据送到互联网。它通常需要先与基站建立无线控制连接，再让基站与核心网建立信令关系，然后建立能够承载用户数据的 E-RAB。项目中的大部分字段，都是在描述这条链路上的“谁、在什么时候、尝试做什么、结果如何”。

## 2. 两类数据回答两类问题

仓库中的电信数据主要分为 `performance` 和 `cell_traces` 两类。理解二者的区别，是读懂后续字段的前提。

### 2.1 Performance：一段时间内的汇总成绩

`data/performance.csv` 中每一行表示某个 `eNodeB + Cell` 在一个测量周期结束时的汇总计数。样例数据以 15 分钟为间隔，包含：

- RRC 连接尝试了多少次、成功了多少次；
- S1 信令连接尝试了多少次、成功了多少次；
- 各个 QCI 的 E-RAB 建立了多少次、成功了多少次；
- 活跃 E-RAB 释放了多少次；
- UE 累计活跃了多长时间；
- 上行 RSSI 等无线指标。

它适合回答：

> “这个小区在这 15 分钟里的整体表现怎么样？”

它不能直接告诉我们某一部具体手机的完整失败过程。

### 2.2 Cell traces：一次通信过程的明细

`data/cell-traces.csv` 中每一行描述一次具体过程，例如：

- `INITIAL_ATTACH`：初始附着；
- `RRC_SETUP`：RRC 连接建立；
- `INITIAL_CTXT`：初始上下文建立；
- `INITIAL_CTXT_SETUP_FAILURE`：初始上下文建立失败；
- `DEFAULT_BEARER_ACTIVATION`：默认承载激活；
- `DEDICATED_BEARER_ACTIVATION`：专用承载激活；
- `ERAB_SETUP`：E-RAB 建立；
- `ERAB_MODIFY`：E-RAB 修改；
- `ERAB_RELEASE`：E-RAB 释放；
- `SERVICE_REQUEST`：业务请求；
- `TRACKING_AREA_UPDATE`：跟踪区更新；
- `INTERENB_HO`：跨 eNodeB 切换；
- `DETACH`：从网络去附着。

这类记录还可能携带 IMSI、终端标识、开始和结束小区、执行结果、失败原因、耗时以及无线测量值。

此外，Cell trace schema 还预留了 RRC 重建和 eNodeB 释放 UE 上下文等字段组。也就是说，`procedure_type` 描述这一行的主过程，而同一行中的其他字段可能记录该过程关联的子步骤；不能简单地把每个字段前缀都理解成一个必然存在的 `procedure_type` 取值。

它适合回答：

> “这次具体的接入或承载建立为什么失败？”

所以，本项目的分析思路可以概括为：

```text
Performance 发现“某个小区整体不正常”
                  │
                  ▼
Cell traces 解释“具体失败发生在哪个步骤、是什么原因”
```

### 2.3 时间字段：先分清统计窗口和过程时间

项目中有三类时间：

| 字段形式 | 含义 |
|---|---|
| `measurement_end` | Performance 汇总周期的结束时间 |
| `starttime` / `endtime` | 一条 Cell trace 主过程的开始和结束时间 |
| `*_timestamp_start` / `*_duration` | 主过程内部某个子步骤的开始时间和耗时 |
| `*_sample_meastime` | 无线或流量样本的测量时间 |

其中 `meastime` 是 measurement time 的缩写。schema 中的 `uemeasment` 少了一个字母 `r`，这是源数据的实际拼写；查询时必须沿用真实字段名，不能因为英文拼写不规范就自行改名。

## 3. 先学会拆解字段名

电信字段看起来很长，是因为名称通常同时编码了“协议、过程、动作、结果和分类维度”。例如：

```text
ERAB_EstabInitSuccNbr_QCI1
│     │    │   │    └─ QCI 1
│     │    │   └────── Number，数量
│     │    └────────── Success，成功
│     └─────────────── Initial Establishment，初始建立
└───────────────────── E-RAB
```

常见缩写如下：

| 缩写 | 英文 | 含义 |
|---|---|---|
| `Att` | Attempt | 尝试 |
| `Succ` | Success | 成功 |
| `Estab` | Establishment | 建立 |
| `ReEstab` | Re-establishment | 重建 |
| `Rel` | Release | 释放 |
| `Nbr` | Number | 数量 |
| `Conn` | Connection | 连接 |
| `Sig` | Signalling | 信令 |
| `Ctxt` | Context | 上下文 |
| `Init` | Initial | 初始 |
| `Add` | Additional | 附加 |
| `Recvd` / `Rec` | Received | 已接收 |
| `Trans` | Transmitted | 已发送 |
| `Ack` | Acknowledged | 已确认 |
| `DL` | Downlink | 下行，网络发往手机 |
| `UL` | Uplink | 上行，手机发往网络 |

另一个例子是：

```text
rrc_conn_setup_rrc_result
│   │    │     └─ RRC 建立的结果
│   │    └────── setup，建立
│   └─────────── connection，连接
└─────────────── RRC 协议
```

读长字段时，不要从左到右硬背，而要先找协议前缀，再找 `setup`、`release`、`result`、`duration` 等过程词。

## 4. 网络中“谁是谁”

### 4.1 UE：用户设备

`UE` 是 User Equipment，即用户设备。它可以是手机、无线网卡、车载终端或物联网设备。

项目中没有一列直接叫 `ue_id`，而是通过下面几类标识从不同角度识别 UE、用户或号码：

| 字段 | 表示什么 | 更接近谁的身份 |
|---|---|---|
| `imsi` | International Mobile Subscriber Identity | SIM 卡/移动用户 |
| `msisdn` | 移动用户号码 | 用户使用的电话号码 |
| `imeisv` | IMEI 加软件版本 | 终端设备及软件版本 |

这三个概念不能互换：

- 用户换手机但保留 SIM 卡时，`IMSI` 通常不变，`IMEISV` 会变化；
- 用户更换 SIM 卡后，`IMSI` 会变化；
- `MSISDN` 是可拨打的号码，不是无线网络内部所有流程都必须使用的主身份。

这些字段属于敏感标识。真实生产系统中需要脱敏、限制访问并遵守所在地的数据保护要求。本项目使用的是合成数据，但数据治理原则仍然值得保留。

### 4.2 eNodeB：LTE 基站

`eNodeB`，也写作 `eNB`，是 LTE 无线接入网中的基站。项目中的相关字段包括：

- `enodeb_id`
- `start_enodeb_id`
- `end_enodeb_id`

`start_` 和 `end_` 表示一次过程开始和结束时所在的基站。如果二者不同，可能意味着过程跨越了不同基站；但是否属于切换，还需要结合 `procedure_type`、时间和其他信令字段判断，不能只凭两个 ID 不同就下结论。

### 4.3 Cell：无线小区

一个 eNodeB 可以管理多个 Cell。Cell 是无线覆盖和资源调度的基本对象之一。相关字段包括：

- `cell_id`
- `start_cell_id`
- `end_cell_id`

性能表用 `enodeb_id + cell_id + measurement_end` 标识“某  基站下某小区在某个测量周期”的统计结果。分析时不能只看 `cell_id`，因为不同基站环境中可能存在相同或局部编码的小区标识。

### 4.4 MME：控制面的核心节点

`MME` 是 Mobility Management Entity，主要处理 LTE 的控制面工作，例如：

- 用户附着和鉴权流程协调；
- 移动性和连接状态管理；
- 为用户选择相关核心网节点；
- 控制承载的建立和释放。

项目中的 `start_mme` 和 `end_mme` 表示过程开始与结束时关联的 MME。

`gummei` 是 Globally Unique MME Identifier，即全球唯一 MME 标识。它用来在更大范围内唯一定位某个 MME，而不只是保存一个局部节点编号。

### 4.5 SGW：用户面的转发节点

`SGW` 是 Serving Gateway。与以信令控制为主的 MME 不同，SGW 主要参与用户数据转发。

项目中的字段包括：

- `sgw_bytes_sent_dl`：SGW 下行发送的字节数；
- `sgw_pkts_sent_dl`：SGW 下行发送的数据包数；
- `sgw_bytes_recvd_ul`：SGW 上行接收的字节数；
- `sgw_pkts_recvd_ul`：SGW 上行接收的数据包数。

这些字段能说明“传了多少”，但不能单独说明业务体验好坏。高流量可能意味着业务繁忙，也可能伴随拥塞；低流量可能意味着用户少，也可能意味着连接失败。

## 5. 一部手机怎样建立 LTE 连接

下面按照典型顺序，把项目中的信令字段串成一段完整故事。

### 5.1 第一步：建立 RRC 无线控制连接

`RRC` 是 Radio Resource Control，即无线资源控制协议。手机需要通过 RRC 与 eNodeB 建立控制关系，才能继续完成后续接入。

Cell trace 中的相关字段包括：

- `rrc_conn_setup_timestamp_start`
- `rrc_conn_setup_rrc_result`
- `rrc_conn_setup_rrc_establ_cause`
- `rrc_conn_setup_duration`

它们分别描述开始时间、成功或失败结果、建立原因和耗时。

Performance 中则使用汇总计数器：

- `RRC_ConnEstabAtt`：RRC 建立尝试总数；
- `RRC_ConnEstabSucc`：RRC 建立成功总数；
- `RRC_ConnReEstabAtt`：连接中断后的重建尝试数；
- `RRC_ConnMax`：测量周期内处于 `RRC_CONNECTED` 状态的 UE 峰值。

最直接的 RRC 建立成功率可以写成：

```text
RRC 建立成功率 = RRC_ConnEstabSucc / RRC_ConnEstabAtt × 100%
```

项目还按建立原因拆分了尝试数和成功数：

| 后缀 | 英文 | 典型含义 |
|---|---|---|
| `_Em` | Emergency | 紧急业务接入 |
| `_Hpa` | High Priority Access | 高优先级接入 |
| `_Mod` | Mobile Originating Data | 手机主动发起数据业务 |
| `_Mos` | Mobile Originating Signalling | 手机主动发起信令 |
| `_Mta` | Mobile Terminating Access | 网络寻呼或下行触发的接入 |
| `_Dta` | Delay Tolerant Access | 可容忍延迟的接入 |

例如：

- `RRC_ConnEstabAtt_Mod`：手机主动发起数据业务时的 RRC 尝试数；
- `RRC_ConnEstabSucc_Mod`：其中成功建立的数量；
- `RRC_ConnReEstabAtt_Em`：紧急业务场景下的 RRC 重建尝试数。

拆分原因很有价值：总成功率下降时，可以进一步判断问题是否只集中在某一类业务。

### 5.2 第二步：建立 S1 信令连接

RRC 解决的是 UE 与 eNodeB 之间的无线控制关系。接下来，eNodeB 还需要通过 S1 接口与核心网控制节点 MME 交互。

Cell trace 字段包括：

- `s1_sig_conn_setup_timestamp_start`
- `s1_sig_conn_setup_sig_conn_result`
- `s1_sig_conn_setup_rrc_establ_cause`
- `s1_sig_conn_setup_duration`

Performance 中对应：

- `S1SIG_ConnEstabAtt`
- `S1SIG_ConnEstabSucc`
- 以及按 `_Em`、`_Hpa`、`_Mod`、`_Mos`、`_Mta` 拆分的计数器。

如果 RRC 成功率正常，而 S1 信令连接成功率明显下降，问题更可能位于基站到核心网的信令链路、MME 处理或相关配置，而不一定是空口覆盖问题。

### 5.3 第三步：建立初始上下文

`INITIAL_CTXT_SETUP` 是 Initial Context Setup。它可以理解为：核心网通知 eNodeB 为这个 UE 准备接入上下文，并携带承载、安全和服务质量相关信息。

相关字段包括：

- `initial_ctxt_setup_timestamp_start`
- `initial_ctxt_setup_initial_ctxt_result`
- `initial_ctxt_setup_duration`
- `initial_ctxt_setup_erab_result_array`
- `initial_ctxt_setup_erab_qci_array`
- `initial_ctxt_setup_3gpp_cause_group`
- `initial_ctxt_setup_3gpp_cause`

一次上下文建立可能同时处理多个 E-RAB，所以结果和 QCI 使用 `array` 保存。两个数组应按相同位置理解，例如第一个结果对应第一个 QCI。

### 5.4 第四步：上报 UE 能力

UE 能力字段包括：

- `ue_capability_timestamp`
- `ue_capability_cap_category`

能力类别可用于描述终端支持的速率、无线特性和能力等级。它会影响网络能够为终端使用哪些功能，但一个类别编号的精确含义需要结合采用的 3GPP 版本和设备实现解释。

### 5.5 已连接之后还会发生什么

手机成功接入并不代表后续只剩数据传输。样例中的 `procedure_type` 还包含以下流程：

| 过程 | 直观解释 |
|---|---|
| `DEFAULT_BEARER_ACTIVATION` | 激活默认承载，使 UE 获得基础数据连接 |
| `DEDICATED_BEARER_ACTIVATION` | 为具有特定 QoS 要求的业务增加专用承载 |
| `SERVICE_REQUEST` | 空闲 UE 有业务需要传输时，请求恢复用户面资源 |
| `TRACKING_AREA_UPDATE` | UE 进入新的跟踪区或按要求更新位置状态 |
| `INTERENB_HO` | UE 在两个 eNodeB 之间完成切换 |
| `ERAB_MODIFY` | 修改已有 E-RAB 的参数或 QoS |
| `ERAB_RELEASE` | 释放不再需要的 E-RAB |
| `DETACH` | UE 主动或由网络触发，从 EPC 去附着 |

`INTERENB_HO` 特别适合与 `start_enodeb_id`、`start_cell_id`、`end_enodeb_id`、`end_cell_id` 一起阅读。跨基站切换时，开始和结束 eNodeB 通常不同；普通接入流程中，即使存在 start/end 字段，也不应在没有切换过程证据时把 ID 变化直接解释为切换。

## 6. Bearer、DRB、E-RAB 与 QCI

这些概念很容易混淆，可以先用“物流通道”类比：

- 信令负责申请并配置通道；
- Bearer 是具有特定服务质量的数据运输通道；
- QCI 是通道的服务等级；
- PDCP 是无线数据通道中的一个协议处理层。

### 6.1 Bearer：承载

`bearer_id` 是承载标识。承载不是一条物理电线，而是一组逻辑连接和 QoS 处理规则。

不同业务可能需要不同承载。例如，语音通话对时延敏感，网页下载更关注吞吐量，二者不一定使用相同的服务质量策略。

### 6.2 DRB：无线数据承载

`DRB` 是 Data Radio Bearer，表示 UE 与 eNodeB 之间承载用户数据的无线侧逻辑通道。

项目中的相关字段包括：

- `uerbtrafficrep_sample_drb_pdcp_ackvol_dl`
- `uerbtrafficrep_sample_drb_pdcp_transvol_dl`
- `uerbtrafficrep_sample_drb_pdcp_recvol_ul`

它们分别描述某个 DRB 上 PDCP 下行确认量、下行发送量和上行接收量。

### 6.3 E-RAB：贯通无线侧与核心网的承载

`E-RAB` 是 E-UTRAN Radio Access Bearer。可以把它理解为从 UE 经过无线接入网、通向 SGW 的一条端到端接入承载。它包含无线侧 DRB 以及基站到核心网用户面的相关承载。

Cell trace 中有：

- `erab_setup_timestamp_start`
- `erab_setup_erab_result_array`
- `erab_setup_erab_qci_array`
- `erab_setup_duration`

Performance 中有三组重要计数：

1. `ERAB_EstabInitAttNbr_QCI1...QCI9`：初始 E-RAB 建立尝试；
2. `ERAB_EstabInitSuccNbr_QCI1...QCI9`：初始 E-RAB 建立成功；
3. `ERAB_EstabAddAttNbr_QCI` 和 `ERAB_EstabAddSuccNbr_QCI`：附加 E-RAB 的尝试和成功。

“初始 E-RAB”通常随用户上下文建立；“附加 E-RAB”则是在已有连接上增加新的业务承载。

### 6.4 QCI：承载的服务质量等级

`QCI` 是 QoS Class Identifier。经典 LTE QCI 1—9 用来表达不同的优先级、时延和丢包要求。理解时不必先背完整参数，可以先记住常见业务方向：

| QCI | 常见业务方向 |
|---|---|
| 1 | 实时语音 |
| 2 | 实时视频 |
| 3 | 实时交互或游戏 |
| 4 | 缓冲视频 |
| 5 | IMS 信令 |
| 6 | 视频、TCP 类业务 |
| 7 | 语音、视频或交互业务 |
| 8 | 普通数据业务 |
| 9 | 普通数据业务，常见默认承载 |

这张表只用于建立直觉。实际网络中的优先级和资源策略应以运营商配置及对应 3GPP 规范为准。

Performance 表将 QCI 1—9 分成九列；部分字段则把九个子计数器编码在一个字符串中，例如：

```text
ERAB_EstabAddAttNbr_QCI = "0|0|0|0|0|0|0|0|0"
```

这里每个位置对应一个 QCI。使用前应先解析字符串，不能把整段文本直接当成一个数值。

`ERAB_SessionTimeQCI_QCI` 也采用相似方式保存各 QCI 的累计会话时间，而 `ERAB_SessionTimeUE` 保存 Cell 内所有 UE 的累计活跃会话时间。前者适合观察不同 QoS 业务，后者被本项目用于计算整体释放事件率。

## 7. PDCP 和用户流量

`PDCP` 是 Packet Data Convergence Protocol。它位于 LTE 无线协议栈中，承担用户数据处理、头压缩、安全保护和序列管理等职责。

项目中的字段包括：

- `pdcp_bitrate_dl`、`pdcp_bitrate_ul`：PDCP 下行、上行速率；
- `pdcp_volume_dl`、`pdcp_volume_ul`：PDCP 下行、上行数据量；
- `uetrafficrep_sample_meastime`：UE 流量样本的测量时间；
- `uetrafficrep_sample_thp_pdcpvol_trunk_dl`：吞吐统计窗口内的下行 PDCP 数据量；
- `uetrafficrep_sample_thp_pdcpvol_trunk_ul`：吞吐统计窗口内的上行 PDCP 数据量；
- `uetrafficrep_sample_thp_time_dl`、`uetrafficrep_sample_thp_time_ul`：对应方向的统计时间。

schema 还定义了一组按无线承载记录的流量字段：

- `uerbtrafficrep_sample_meastime`：承载流量样本时间；
- `uerbtrafficrep_sample_bearer_id`：Bearer 标识；
- `uerbtrafficrep_sample_erab_id`：E-RAB 标识；
- `uerbtrafficrep_sample_bearer_type`：承载类型；
- `uerbtrafficrep_sample_drb_pdcp_ackvol_dl`：下行已确认数据量；
- `uerbtrafficrep_sample_drb_pdcp_transvol_dl`：下行已发送数据量；
- `uerbtrafficrep_sample_drb_pdcp_recvol_ul`：上行已接收数据量。

当前 `cell-traces.csv` 没有包含这组字段，因此本地 DuckDB 导入表中也不会生成这些空字段。

如果数据量和统计时间的单位已知，就可以计算近似吞吐率：

```text
吞吐率 ≈ 数据量 / 有效传输时间
```

但本项目的 schema 没有明确给出这些字段的单位。因此分析时可以比较相对变化，不应在没有数据字典的情况下擅自把数值解释成 bit/s、Byte/s 或毫秒。

## 8. 无线质量：RSSI、SINR、PUSCH 与 PRB

### 8.1 UL 与 DL

- `UL` 是 Uplink，上行，即 UE 发往网络；
- `DL` 是 Downlink，下行，即网络发往 UE。

看到 `sent_dl`、`recvd_ul` 时，应站在网络节点的视角解释。例如 SGW “发送下行数据”和“接收上行数据”是方向一致的。

### 8.2 RSSI：接收到的总功率

`UL_RSSI` 表示基站在上行侧观测到的接收信号强度指标。RSSI 通常包含期望信号、干扰和噪声，因此它不是“越高一定越好”的单一质量分数：

- 数值过低可能意味着上行信号弱；
- 数值异常偏高也可能意味着上行干扰较强；
- 判断阈值与带宽、设备实现和测量口径有关。

本项目的 RCA 规则将 `-120` 到 `-114` 作为一个示例正常范围。这是演示规则，不应直接复制为所有真实网络的统一阈值。

### 8.3 SINR：有用信号相对于干扰和噪声的质量

`uemeasment_sample_last_pusch_sinr` 表示最近一次 PUSCH 测量的 SINR。

`SINR` 是 Signal to Interference plus Noise Ratio。与 RSSI 相比，它更直接表达有用信号和干扰噪声之间的关系：

- SINR 较高通常表示信号更容易被正确解调；
- SINR 较低表示干扰或噪声相对更强；
- 精确的好坏阈值取决于调制编码方式、业务目标和设备实现。

### 8.4 PUSCH：上行用户数据通道

`PUSCH` 是 Physical Uplink Shared Channel，即物理上行共享信道。UE 的大部分上行用户数据通过 PUSCH 发送。

相关字段：

- `uemeasment_sample_last_pusch_num_prb`
- `uemeasment_sample_last_pusch_sinr`
- `uemeasment_sample_last_pusch_power_per_prb`

它们描述最近一次 PUSCH 传输使用了多少 PRB、信号质量如何、每个 PRB 使用了多少功率。

### 8.5 PRB：无线资源的基本调度单位

`PRB` 是 Physical Resource Block。基站调度器把一定数量的 PRB 分配给 UE。通常：

- 分配更多 PRB，意味着给该 UE 更多频域资源；
- 实际吞吐量还取决于 SINR、调制编码、调度时间和重传；
- 不能仅凭 PRB 数量判断用户最终速率。

### 8.6 Power Headroom：上行功率余量

`uemeasment_sample_power_headroom` 表示 UE 在当前上行发射功率之外还剩多少功率余量。

直观地说：

- 余量较大：UE 还有能力提高发射功率；
- 余量较小或为负：UE 可能接近发射能力上限；
- 结合 PRB、PUSCH SINR 和功率控制配置，才能判断是覆盖、干扰还是配置问题。

相关字段还包括：

- `uemeasment_sample_meastime`
- `uemeasment_sample_power_headroom_prb_used`
- `uemeasment_sample_last_pusch_power_per_prb`

## 9. 重建与释放：连接为什么结束

### 9.1 RRC 重建

当已有 RRC 连接发生无线链路问题时，UE 可能尝试重建连接。

字段包括：

- `rrc_conn_reestablish_timestamp_start`
- `rrc_conn_reestablish_reestab_result`
- `rrc_conn_reestablish_duration`
- `rrc_conn_reestablish_reestab_cause`

重建次数上升通常是值得调查的信号，但不能直接等同于掉线。还需要查看重建是否成功、失败原因、无线质量和最终是否释放上下文。

### 9.2 E-RAB 释放

字段包括：

- `erabrelease_starttime`
- `erabrelease_release_result`
- `erabrelease_duration`
- `erabrelease_s1_release_cause`
- `erabrelease_internal_release_cause`
- `erabrelease_erab_req_qci_array`
- `erabrelease_erab_req_3gpp_cause_group_array`
- `erabrelease_erab_req_3gpp_cause_array`

E-RAB 释放既可能是正常业务结束，也可能是异常中断。只有结合 cause、业务时长和上下文，才能判断它是否属于“掉话”或“业务保持失败”。

### 9.3 eNodeB 释放 UE 上下文

字段包括：

- `enb_uectxtrelease_starttime`
- `enb_uectxtrelease_duration`
- `enb_uectxtrelease_triggering_node`
- `enb_uectxtrelease_ue_release_cause`
- `enb_uectxtrelease_internal_release_cause`
- `enb_uectxtrelease_3gpp_cause_group`
- `enb_uectxtrelease_3gpp_cause`
- `enb_uectxtrelease_erab_release_req_qci_array`

`triggering_node` 用来判断释放由哪一侧触发；`cause_group` 表示原因类别；`cause` 表示该类别下的具体原因值。

原因数字本身没有脱离上下文的自然语言含义。要准确解释，必须知道它属于哪个过程、哪个 cause group，并查阅对应版本的 3GPP 定义或设备厂商数据字典。

## 10. 本项目真正使用的两个 KPI

项目通过 DuckDB 本地视图计算两个 KPI：`erab_success_rate` 和 `retainability`。

### 10.1 E-RAB 建立成功率

项目先把 QCI 1—9 的初始建立尝试数和成功数分别求和：

```text
总尝试数 =
    ERAB_EstabInitAttNbr_QCI1
  + ERAB_EstabInitAttNbr_QCI2
  + ...
  + ERAB_EstabInitAttNbr_QCI9

总成功数 =
    ERAB_EstabInitSuccNbr_QCI1
  + ERAB_EstabInitSuccNbr_QCI2
  + ...
  + ERAB_EstabInitSuccNbr_QCI9

erab_success_rate = 总成功数 / 总尝试数 × 100
```

异常检测器将低于 `97%` 的数据视为潜在事件。

以仓库中的一条真实样例为例：

```text
eNodeB              = 1
Cell                = 12314
measurement_end     = 2025-11-24 16:00:00
E-RAB 总尝试数       = 529
E-RAB 总成功数       = 505

erab_success_rate
  = 505 / 529 × 100
  ≈ 95.46%
```

因为 `95.46% < 97%`，异常检测器会把它识别为潜在事件。

RCA 规则又规定：低于 `95%` 才属于高严重度，否则属于低严重度。因此这条 `95.46%` 的记录会触发事件，但按照当前规则不会被评为高严重度。

这说明“是否创建事件”和“事件有多严重”是两个不同判断，阈值不必相同。

### 10.2 项目中的 retainability

在日常语言中，retainability 是“业务建立后能够保持、不异常中断的能力”。但一定要看清具体项目的计算口径。

本项目使用下面的公式：

```text
活跃 E-RAB 释放总数 =
    ERAB_RelActNbr_QCI1
  + ERAB_RelActNbr_QCI2
  + ...
  + ERAB_RelActNbr_QCI9

retainability =
  活跃 E-RAB 释放总数 / ERAB_SessionTimeUE × 3600
```

如果 `ERAB_SessionTimeUE` 的单位是秒，那么该值表达的是：

> 每小时累计 UE 活跃会话时间对应的活跃 E-RAB 释放次数。

因此，本项目的 `retainability`：

- 不是百分比；
- 数值越高表示单位活跃时长内释放越频繁，通常越差；
- 更接近一种“释放率”或“中断事件率”。

仓库第一条性能数据为：

```text
活跃 E-RAB 释放总数 = 9
ERAB_SessionTimeUE   = 20907

retainability
  = 9 / 20907 × 3600
  ≈ 1.55
```

异常检测器将 `retainability > 3` 视为潜在事件。

在其他运营商、设备厂商或论文中，名为 retainability 的 KPI 可能使用完全不同的公式，甚至以成功保持率或掉话率百分比表达。讨论 KPI 时，公式永远比名称更可靠。

## 11. 从 KPI 异常走向根因分析

现在可以把项目的数据链完整串起来。

### 11.1 检测 E-RAB 成功率异常

当 `erab_success_rate < 97%` 时，项目：

1. 确定异常所属的 `enodeb_id` 和 `cell_id`；
2. 记录异常开始、结束时间；
3. 创建包含异常 KPI 和数值的 incident；
4. 在 RCA 阶段查询同一基站、小区和时间范围内的 Cell trace；
5. 统计 S1 信令连接结果；
6. 依据检索到的规则判断是否存在安全过程或信令建立失败；
7. 生成分析报告。

这条分析路径体现了：

```text
汇总 KPI 告诉我们“哪里表现不好”
明细 trace 告诉我们“哪些过程失败了”
规则告诉智能体“应该检查什么”
```

### 11.2 检测业务保持异常

当 `retainability > 3` 时，项目的示例规则要求检查：

- `UL_RSSI` 是否偏离规则指定范围；
- 上行功率控制配置是否接近目标值；
- 是否需要建议调整上行配置。

当前代码中的上行 RSSI 和配置工具返回模拟数据，因此这部分展示的是“RCA 工具如何被规则动态选择”的架构，不代表已经接入真实网管系统。

## 12. Incident 表中的字段

`incidents` 表不完全属于电信协议字段，而是项目在电信数据之上构建的故障管理模型：

| 字段 | 含义 |
|---|---|
| `incident_id` | 事件唯一标识 |
| `start_ts` / `end_ts` | 异常开始和结束时间 |
| `status` | 事件状态，如 `NEW`、`ANALYZED` |
| `description` | 初始事件描述 |
| `kpi_missed` | 越界 KPI 及其数值 |
| `enodeb_id` / `cell_id` | 受影响的基站和小区 |
| `severity` | 严重度 |
| `preliminary_analysis` | 初步 RCA 报告 |
| `final_analysis` | 最终分析 |
| `events` | 分析过程中积累的事件事实 |
| `events_embeddings` | 用于相似事件检索的向量 |
| `cause` | 最终原因 |
| `resolution` | 解决方案 |
| `created_ts` | 记录创建时间 |

本地版本直接使用 `events` 文本计算相似度，从历史 incident 中寻找具有相似事件描述的案例；相似度特征不是电信测量值。

## 13. 阅读任意一行数据的固定方法

以后再遇到陌生电信字段，可以按下面顺序阅读。

### 第一步：确定粒度

先问：

- 这是一个 15 分钟汇总计数，还是一次信令过程？
- 一行对应一个 Cell、一个 UE、一个 Bearer，还是一个 procedure？

粒度不清，后续计算很容易重复计数。

### 第二步：确定网络对象

观察：

- `enodeb_id`
- `cell_id`
- `imsi`
- `bearer_id`
- `start_*` 和 `end_*`

先弄清楚“谁发生了什么”。

### 第三步：确定过程

查看 `procedure_type` 或字段前缀：

- `rrc_conn_setup`
- `s1_sig_conn_setup`
- `initial_ctxt_setup`
- `erab_setup`
- `rrc_conn_reestablish`
- `erabrelease`
- `enb_uectxtrelease`

### 第四步：确定结果与原因

按顺序看：

1. `result`
2. `duration`
3. `cause_group`
4. `cause`
5. `internal_cause`

不要只看到 `FAILURE` 就立即猜根因。

### 第五步：结合无线和流量指标

最后再检查：

- RSSI 与 SINR；
- PUSCH、PRB 和 Power Headroom；
- PDCP 数据量和吞吐；
- 同小区同时间段的汇总成功率。

## 14. 容易踩的几个坑

### 14.1 把所有“成功率下降”都归因于无线覆盖

RRC、S1 和 E-RAB 属于不同阶段。RRC 正常而 S1 异常时，优先检查核心网信令链路；RRC 本身失败较多时，才更应该关注空口、覆盖、容量和接入配置。

### 14.2 把 RSSI 当成唯一的信号质量

RSSI 包含干扰和噪声。应结合 SINR、PUSCH、PRB、功率余量和失败原因分析。

### 14.3 把所有 E-RAB 释放都当成掉话

正常业务结束也会释放 E-RAB。需要通过 cause、过程顺序和会话持续时间识别异常释放。

### 14.4 忽略字段单位

本项目部分 trace 字段没有在 schema 中注明单位。没有数据字典时，不能自行假定 `duration` 是秒还是毫秒、`volume` 是 Byte 还是 bit。

### 14.5 只看 KPI 名称，不看公式

本项目的 `retainability` 实际是乘以 3600 的释放事件率，而不是保持成功率百分比。跨系统比较时必须先统一公式和单位。

### 14.6 把 schema 中存在的字段误认为样例数据一定存在

当前样例 CSV 与 schema 并不完全相同：

- Performance schema 中有 `RRC_ConnReEstabAtt_Dta`，样例 CSV 没有这一列；
- Cell trace schema 的最后八个吞吐与 Bearer 明细字段没有出现在当前样例 CSV 中。

因此，代码和查询需要正确处理缺失列或空值，学习时也应区分“系统设计支持的字段”和“本次样例实际提供的字段”。

## 15. 总结：用一条因果链记住这些字段

不需要一次背下所有缩写。只要记住下面的因果链，字段就会逐渐各就各位：

```text
UE 通过 Cell 接入 eNodeB
        │
        ├─ RRC 建立无线控制连接
        ├─ S1 连接到 MME
        ├─ Initial Context 准备用户上下文
        ├─ E-RAB 按 QCI 建立业务承载
        ├─ PDCP、SGW 记录用户数据传输
        ├─ RSSI、SINR、PUSCH、PRB 描述无线状态
        └─ 重建、释放和 Cause 描述异常或正常结束

大量单次过程
        │ 汇总
        ▼
Performance 计数器
        │ 计算
        ▼
E-RAB 成功率、retainability
        │ 越过阈值
        ▼
Incident → RCA 规则 → Cell trace → 根因报告
```

对这个项目而言，最重要的不是记住 80 多个字段，而是能够回答四个问题：

1. 这条数据描述的是哪个网络对象？
2. 它处于接入、承载、传输还是释放的哪个阶段？
3. 这是单次过程，还是一段时间内的汇总？
4. 这个数值如何参与 KPI，并怎样支持根因判断？

能稳定回答这四个问题，就已经具备了继续阅读本项目电信数据和 RCA 代码的基础。
