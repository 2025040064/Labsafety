# 工业数据来源与接入审计

核验日期：2026-10-31。不同来源始终使用独立适配器、数据版本、标签语义与固定划分，不合并标签训练。真实获取状态以各数据目录的 `source.json` / `download_failure.json` 和实验 manifest 为准；本文不以构造测试输入作为训练证据。

| 数据集 | 官方事实、许可 | 本轮预测任务与接入方式 | 边界 |
| --- | --- | --- | --- |
| AI4I 2020 | [UCI 601](https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset)，10,000 行，**合成数据**，CC BY 4.0，DOI 10.24432/C5HS5C | 优先 `ucimlrepo.fetch_ucirepo(id=601)`；机器失效二分类，Type 与五项传感器；固定原始顺序 60/20/20 | 排除 UDI/UID、Product ID、TWF/HDF/PWF/OSF/RNF 及目标。Type 是推理可知质量类别。没有真实设备 ID；合成行不能称为独立设备或真实工厂生产。 |
| SECOM | [UCI 179](https://archive.ics.uci.edu/dataset/179/secom)，1,567 个生产实体，匿名半导体工艺测量，存在缺失值，CC BY 4.0，DOI 10.24432/C54305 | 优先 `ucimlrepo.fetch_ucirepo(id=179)`；-1 pass / +1 fail 映射成 0/1；按实际测量时间稳定排序后 60/20/20 | 时间戳只作划分，不进特征；匿名传感器无可信工况语义，因此工况为 overall，不把日期段解释为不同设备/批次。缺失填补和常量处理只许在训练折拟合。 |
| Paderborn | [官方说明与许可](https://mb.uni-paderborn.de/en/kat/research/bearing-datacenter)，[原始 RAR 目录](https://groups.uni-paderborn.de/kat/BearingDataCenter/)，CC BY-NC 4.0；真实实验台测量，6 个健康状态、26 个损伤状态，含人工损伤和加速寿命损伤 | 健康/外圈/内圈三分类；按官方 MAT 名读取 N/M/F 预设工况与物理轴承编码；聚合振动、两相电流的统计/频带特征，固定每条记录 8 个不重叠窗口 | 整个物理轴承的所有工况、记录和窗口只属于一个划分。窗口不是独立轴承。小规模子集每类仅 3 轴承，每折每类 1 轴承，不作校准可靠性结论；商业使用需提供方许可。 |
| XJTU-SY | [作者官方说明](https://biaowang.tech/xjtu-sy-bearing-datasets/)，15 把轴承完整加速寿命、3 工况×5 轴承，采样频率 25.6kHz，每分钟记录 32,768 点；作者允许验证寿命预测算法并要求引用，未核验标准许可证 | 支持本地 ZIP/TAR 解包或已解压目录。自然任务是当前振动观测后的剩余寿命回归，单位 minute；按物理轴承分组，记录按序号读取。须在 `source.json.complete_runs` 明确每轴承最后记录号及已观察最终失效 | 不把工况编号当故障标签，不把当前运行序号、完整寿命或未来数据放入特征。缺失/截断 run 拒绝。此次分类训练服务拒绝把其回归分数混入分类对照；未获取原始归档，不宣称完成真实训练。 |
| 联想智能工厂 | [国家基础学科公共科学数据中心条目](https://nbsdc.cn/general/dataDetail?id=67d510b0195d260905af9dcc&type=1) | 只提供已授权本地规范化表格导入契约；`authorized_local_use=true`、实际许可/版本/字段、物理分组、目标语义和哈希必须明确 | 无申请、登录或权限绕过。未获得原始文件与授权，来源字段/任务未核验，不宣称原生解析或真实训练完成。 |
| Pneumatic Pressure | [Mendeley V3](https://data.mendeley.com/datasets/ypzswhhzh9/3)，2024-05-21，CC BY 4.0，DOI 10.17632/ypzswhhzh9.3；真实离散制造现场约 7 日、150 件产品，压力/电流按状态机动作预分段 | 语义是操作动作分类；提供显式规范化表格的来源/字段/工件分组契约，`prediction_task=operation_action_classification` | 未获得原始文件并验证内部结构；此导入契约不是已完成官方原始格式解析。动作标签不得描述为故障标签。 |

## Paderborn 小规模协议

官方每个完整 RAR 约 152–178 MB。为先验证解析与训练，不下载整库；读取官方公开 HTTP 服务器的 RAR4 头部，通过 HTTP Range 跳过未选择记录，仅取以下 9 个物理轴承在两个预先指定工况的首个遇到记录：

- 健康：K001、K002、K003。
- 外圈损伤：KA04、KA15、KA16。
- 内圈损伤：KI04、KI14、KI16。
- 工况：[官方定义](https://mb.uni-paderborn.de/en/kat/research/bearing-datacenter/operating-conditions) N15_M07_F10（1,500 rpm / 0.7 Nm / 1,000 N）、N09_M07_F10（900 rpm / 0.7 Nm / 1,000 N）。

压缩条目按原始字节复制后交给系统 RAR4 解压器，无合成/重采样/补造信号；每条记录文件保存 SHA-256、原 RAR URL 和字节起止位置。选取依据仅轴承与预设工况，不看信号或性能。物理轴承按类别内编码稳定分配 train / validation / test，最小子集每折每类 1 轴承。最终 test 保留在 manifest，训练与 HPO 只读取 train / validation。

本环境第一次 HTTPS 请求因证书链校验失败，未关闭 TLS 校验；官方同域公开 HTTP 服务支持 206 范围请求。源数据由公开 HTTP 获取，哈希只用于本次文件完整性/复现，不冒称与发布方独立公布哈希核对。本地 ZIP/TAR 与非 solid、未加密的 RAR4 可在系统可信 `tar` 支持 RAR 时导入；RAR5、solid、加密、多卷等变体需先用可信工具解压，再导入 MAT 目录。导入拒绝路径穿越、链接、已存在文件覆盖和超过大小上限的展开。

Paderborn 与 XJTU 的本地归档按源归档 SHA-256 解压到工程内 `datasets/processed/industrial`，记录归档及解压文件哈希；重复审计、训练复用前核验全部文件，篡改或出现链接时拒绝读取。不会在工程外的原归档旁创建解压文件。

## 本地规范化表格契约

联想及 Pneumatic 的本地 `data.csv` 必须同目录包含 `source.json`，至少记录：`dataset_key`、`dataset_version`、`source_url`、`license`、`acquired_at`、CSV 的 `sha256`、`target_column`、`group_column`、`regime_column`、明确的数值 `feature_columns` 与整数 `classes` 语义。标签、物理身份与工况标识不能被暗中拼成预测特征；工况对应实际推理前已知元数据。联想另需 `authorized_local_use=true`；Pneumatic 另需已核验动作目标声明。任何缺项、哈希不符、未知许可或未经授权均明确失败。

XJTU-SY 的 `source.json` 需包含 `dataset_key=xjtu_sy`、官方 `source_url`、`acquired_at`、实际许可说明和 `complete_runs`，例如 `{"Bearing1_1":{"last_record":123,"failure_observed":true}}`；数字必须来自完整原始 run 的核验，不使用示例数字作为真实寿命。需每个接入工况至少三把完整物理轴承，才提供不重叠的 train / validation / test。

获取失败保存 `download_failure.json` 的时间、来源、错误类型与原因，并显式记 `substitution_used=false`；训练过程不会隐式联网下载，也不会用测试夹具替代工业数据。

## 实际文件核验

- AI4I 的 `ucimlrepo` 返回表被保存为 CSV 快照，使用 `UID` 和不带单位的传感器列名；适配器将官方别名固定映射到含单位名称，保留快照不变，并在 manifest 记录映射与单位依据。保留 Type 和五个传感器，不保留 ID 或任何故障原因标签。哈希针对保存的快照，不宣称它与 UCI 原始文件逐字节一致。
- SECOM 的 `ucimlrepo` 返回表保存为 CSV 快照，使用 `class,timestamp,Attribute 1,...,Attribute 590`，因此实际预测输入为 590 项数值传感器；页面列出的 591 项不能被误解为额外造出一列传感器。真实文件有 1,567 行、104 fail、1,463 pass。时间从 2008-07-19 11:55:00 到 2008-10-17 06:07:00；文件存在可选秒与单数字日期混排，按 day-first 逐行解析后稳定排序。固定划分为 train 940 / validation 313 / test 314。
- 构造输入测试只验证字段剔除、时间排序、未拟合缺失处理、整轴承隔离、档案安全与失败日志；所有性能必须来自真实获取文件的独立实验产物。
