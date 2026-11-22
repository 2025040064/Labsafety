# 数据来源与使用范围

## UCI Steel Industry Energy Consumption

- 官方入口：https://archive.ics.uci.edu/dataset/851/steel+industry+energy+consumption
- 官方 ZIP：https://archive.ics.uci.edu/static/public/851/steel+industry+energy+consumption.zip
- DOI：https://doi.org/10.24432/C52G8C
- 许可：CC BY 4.0，https://creativecommons.org/licenses/by/4.0/legalcode 。使用、改编或再分发须保留归属说明。
- 官方引用：V E, S., Shin, C., & Cho, Y. (2021). Steel Industry Energy Consumption [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C52G8C.
- 核验日期：2026-10-30。页面经 Firecrawl 读取；原始 CSV 经 Python urllib 的 TLS 下载，没有禁用证书检查。
- 原始来源：韩国光阳 DAEWOO Steel Co. Ltd 的工业能耗记录；官方标注 35,040 条、无缺失值。
- 原文件：`raw/steel_energy/Steel_industry_data.csv`，2.6 MB 左右；SHA-256：`9b1cee6f9cb9cd9df2b95814ca90a9a2ff15b7f5f1fba0fae3c643e82072eacc`。

原文件 11 列：`date`、`Usage_kWh`、`Lagging_Current_Reactive.Power_kVarh`、`Leading_Current_Reactive_Power_kVarh`、`CO2(tCO2)`、`Lagging_Current_Power_Factor`、`Leading_Current_Power_Factor`、`NSM`、`WeekStatus`、`Day_of_week`、`Load_Type`。官方页面将 Usage_kWh 标为特征，并将 Load Type 列为分类标签；本项目明确选择 `Usage_kWh` 为回归目标，单位为 kWh，不声称复现官方分类任务。官方对 CO2 单位的说明为 ppm，而列名含 tCO2；这里不自行解释或使用此列。

本阶段只使用预测前已知的日历特征：`hour`、`minute`、`day_of_week`、`month`、`is_weekend`，固定上述顺序。不输入同期无功电量、功率因数、CO2 或 `Load_Type`，因为尚未证实它们在预测时可获得且与目标独立。`NSM` 和文本日历字段由解析后的日期重新生成。日历基线体现可复现流程，不代表可用于生产预测的充分工艺信息。

只有与上述官方 SHA-256 完全一致的文件才标记为已核验 UCI 来源并采用 CC BY 4.0 归属。其他符合字段契约的本地 CSV 标记为 `user_supplied` / `unverified`，许可为 `unknown`；适配器参考来源保留在独立的 `adapter_reference` 元数据中，不推断导入文件的来源或再分发权利。

工况预先固定为 `weekday` / `weekend`，只由日期决定，不使用目标、模型误差或 `Load_Type` 定义。`date_group` 是时间相关的日历块，不是独立加工运行、批次、刀具或机床。该公开文件没有这些独立单元的标识，不能把 35,040 行或 365 天报告为独立加工批次数。

CSV 每日的 `00:00` 行放在当日末尾。导入按官方日期文字解析后稳定排序，保留源行映射，不猜测该行应移至次日。按完整日期分配约 60% / 20% / 20% 保留天数，在 train/validation 和 validation/test 两个边界各排除一天。固定索引、日期清单、工况计数、特征顺序、SHA-256 保存在 `manifests/steel_calendar_v1_<sha-prefix>.json`。所有预处理均为固定日历运算，不从全量数据拟合统计量。

搜索仅使用 train 与 validation。测试标签只在显式最终评估时使用。当前日期组不能支撑独立加工批次的可靠性推断，后续阶段仍须寻找含真实加工运行和工艺条件的数据。

## 获取与重建清单

在项目环境安装完成后运行：

```powershell
.venv\Scripts\python.exe scripts/download_steel.py
```

下载函数只接受已核验 SHA-256 的官方 CSV。已有文件校验不符会明确失败，避免覆盖原数据；若官方更新，必须先核验并记录新版本。

## CNC Milling Process Dataset（2025，已接入）

- 作者数据：[Figshare DOI 10.6084/m9.figshare.28589216.v1](https://doi.org/10.6084/m9.figshare.28589216.v1)；[公开元数据 API](https://api.figshare.com/v2/articles/28589216)。
- 原始研究：[Piecuch, G. & Żabiński, T., Scientific Data 12, 650 (2025)](https://www.nature.com/articles/s41597-025-04923-y)。
- 数据元数据明确许可 CC BY 4.0（不以论文许可替代数据许可）；保留作者及 DOI 归属。
- 实际文件：[FeatureAndMetadata_Milling.csv](https://ndownloader.figshare.com/files/52968974)，1,797,280 字节；发布者 MD5 `cbcad3f9631e0be6c4f71ff27749108c` 已匹配；本地 SHA-256 `e125471f983e98ae326e2c47e15f633bb30738e285b9ad6e54b5e364d4d66dbc`。
- 文件不是普通逗号 CSV：分号分隔，第一行 Column1…Column131，第二行才是真实列名，部分工艺元数据使用小数逗号。实查 968 个加工周期、131 列、0 缺失值、14 个 TollIndex。
- 任务：观测当前加工周期的传感器统计后，估计剩余周期 CycleToFailure，单位 cycles。使用作者发布的 120 个信号聚合特征，加 MillingToolType、ADOC、RDOC、HardnessMean、ToolHolderLength，共 125 个输入。未下载约 25.35 GB 原始信号压缩包，没有声称独立复算这些统计特征。
- FileName、NumberOfCycle、SampleIndex、TollIndex、CycleToFailure、CycleToFailureNormalized 全部不作为输入。剩余寿命可用未来失效时间生成训练标签，但未来失效信息不得作为特征。
- 工况固定为 ADOC=5 mm / 10 mm；每种切深有 7 把工具。TollIndex 是源字段的实际拼写，代表物理刀具；文件名 F 标签不能擅自当作全局刀具编号。
- 固定 seed=42、仅按刀具和切深分配：train 8 把 / 442 行，validation 4 把 / 400 行，test 2 把 / 126 行。同一把刀具的全部周期在同一集合。测试不参与搜索。
- 训练刀具 10、7、103、11、2、6、105、4；验证刀具 5、101、9、8；测试刀具 3、102。保留仅一个周期便失效的刀具 11，不因误差或稀有性删除。
- 一个切深工况只有 2 把验证刀具；默认最少 5 个独立单元的阈值不满足，置信区间与可信薄弱工况权重不会伪造。RAC 可运行全局固定 J / 成本候选排序，但本数据不能证明可信薄弱工况加权分支的实证收益。
- 单机床、14 把刀具限制外推；物理刀具是本任务的保守分组单位，不等于不同工厂、机床或材料批次的独立复制实验。

## NASA Milling（已核验并下载，暂不接入训练）

- [NASA 数据页](https://data.nasa.gov/dataset/milling-wear) 的 legacy/mill.zip 在本机连接超时；[NASA PCoE 官方仓库页面](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/) 提供的 [S3 下载](https://phm-datasets.s3.amazonaws.com/NASA/3.+Milling.zip) 已成功下载。
- 本地 datasets/raw/nasa_milling/milling.zip，14,731,306 字节，SHA-256 `bdba8d52ec1a1baab24c2be58480e6ac62508c8cc1f8219f47ebde8fc9ebc474`；外层包含 mill.zip，内层含 Readme.pdf 和 mill.mat。
- 许可目录为 other-license-specified，指向美国政府作品说明；数据由 UC Berkeley BEST Lab 提供。保留 Agogino / Goebel / BEST Lab 归属，不直接标记为 CC BY 或认定所有第三方材料均为公共领域。
- MAT 实查 167 条记录、146 个 VB 标签、16 个 case。case/run 标识仍需核对实验说明才能等同于独立工具，不能凭切片数计算独立批次。实际结构清单见 [参考快照](../docs/references/nasa-milling-file-inventory.json)。

## 其他官方候选（已在线核验，未用于实验）

| 候选 | 已核验信息 | 当前限制 |
|---|---|---|
| [多机床磨损 zpxs87bjt8 v3](https://data.mendeley.com/datasets/zpxs87bjt8/3) | CC BY 4.0；3 台机床、9 把刀具、6,418 个文件，VB 测量标签 | 独立刀具少；机床域与加工工况不同；本轮未批量下载 |
| [Digital Twin CNC Power](https://data.mendeley.com/datasets/4m9n7zzvwk/1) | CC BY 4.0；5 种切深、变转速进给，包含实验与仿真 | 本机整包下载返回 HTTP 403；需要分清 ADT/实测；未以它生成训练结果 |
| [JUST_CAS_milling](https://data.mendeley.com/datasets/8jk3kyxmbn/1) | CC BY 4.0；115 组、多材料/切深/切宽/转速/进给；约 424 MB | 不把高频行数当独立实验；当前未下载或接入 |

核验日期：2026-10-30。直接 Figshare API / 普通前台页在本机返回 HTTP 403，公开元数据由 Firecrawl 读取成功；发布者 ndownloader 原始文件可直接下载并经 MD5 匹配。所有数据源在各自适配器内独立训练，不拼接异构设备数据。
