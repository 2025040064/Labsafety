# RAC-HPO 微信小程序移植交接

这份清单供接手的 AI 或开发者使用。目标是把现有 RAC-HPO 桌面版的可用功能完整移植到微信小程序，保留单机运行方式。以原项目当前的 GUI、应用服务和实验产物为准。布局可以适配手机，功能、运行参数和指标含义需要一致。

## 项目和工作目录

| 用途 | 具体目录 |
|---|---|
| 原桌面项目根目录 | `F:/A_LIU_Astrspire/A_competition/工业制造场景下的模型训练与超参数优化系统/` |
| 桌面版七个页面 | `F:/A_LIU_Astrspire/A_competition/工业制造场景下的模型训练与超参数优化系统/src/rac_hpo/interfaces/gui/pages/` |
| 桌面版主窗口、任务调度、公共图表和交互 | `F:/A_LIU_Astrspire/A_competition/工业制造场景下的模型训练与超参数优化系统/src/rac_hpo/interfaces/gui/` |
| 原 Python 应用服务 | `F:/A_LIU_Astrspire/A_competition/工业制造场景下的模型训练与超参数优化系统/src/rac_hpo/application/` |
| 移植目标及微信开发者工具导入目录 | `F:/A_LIU_Astrspire/A_competition/Labsafety/` |
| 微信小程序源代码 | `F:/A_LIU_Astrspire/A_competition/Labsafety/miniprogram/` |
| 微信小程序工作区页面 | `F:/A_LIU_Astrspire/A_competition/Labsafety/miniprogram/pages/workspace/` |
| 小程序 Python 后端 | `F:/A_LIU_Astrspire/A_competition/Labsafety/backend/` |
| 移植后的 RAC-HPO Python 核心 | `F:/A_LIU_Astrspire/A_competition/Labsafety/backend/engine/src/rac_hpo/` |
| 单机数据及历史实验工作区 | `F:/A_LIU_Astrspire/A_competition/Labsafety/rac-data/` |
| 原 Labsafety 小程序备份 | `F:/A_LIU_Astrspire/A_competition/工业制造场景下的模型训练与超参数优化系统/workspace/labsafety-backup-20261109/` |

### 从哪些文件开始

- 原项目：先读根目录的 `README.md`、`ARCHITECTURE.md` 和 `configs/default.yaml`。桌面 UI 中，`main_window.py` 和 `workers.py` 是页面到服务的连接点，`charts.py`、`parallel_coordinates.py`、`workspace_state.py` 是图表和交互参考。
- 目标项目：`miniprogram/pages/workspace/index.js` 管理七个入口和任务状态，`index.wxml` 与 `index.wxss` 管理布局，`miniprogram/api.js` 负责访问本机接口。微信开发者工具用根目录的 `project.config.json`。
- Python 接口：`backend/server.py` 管理本机 HTTP、任务、历史查询和导出，`backend/actions.py` 将页面参数传给 RAC-HPO。实际运行使用 `rac-data/configs/default.yaml`。原核心的配置副本位于 `backend/engine/configs/default.yaml`，排查默认值时注意区分。
- 实验产物：`rac-data/datasets/` 保存数据和清单，`rac-data/workspace/database/` 保存 SQLite 数据库，`rac-data/workspace/experiments/`、`industrial_runs/` 和 `loop/` 分别保存对应记录。启动说明见 `README_RAC_HPO.md`，前次实现和验收记录见 `PLAN_RAC_HPO.md`。

## 当前可用范围

小程序在微信开发者工具模拟器中自动连接同一台电脑上的 `http://127.0.0.1:8765`，应用内无需登录。已有真实数据审计、训练、搜索、分类、取消、历史查询、ZIP 导出和离线会话接口。保持单机模式，不引入云托管、账号或生产部署。2026-11-10 本轮移植后，结果页已改为结构化分组展示，Python 端负责计算摘要、图表序列与分页视图，详情接口不再一次返回全量 trials。

## 要补齐的功能

| 页面 | 对照的桌面文件 | 小程序待实现 |
|---|---|---|
| 数据管理 | `data_page.py` | 将校验结果拆成数据概览、固定划分、预定义工况、字段及单位、来源与许可、SHA-256、质量审计和完整清单。补齐大文件及工业多文件数据集的本机导入。当前小程序入口限制为单个文件且不超过 8 MB。导入后将已验证的来源带到训练和优化页，切换数据集时明确切换来源。 |
| 模型训练 | `train_page.py` | 对齐桌面默认 300 棵树、随机种子和有效参数范围。显示验证 MAE、固定 J、总耗时、分工况样本及 MAE、实际模型参数。训练结束直接打开本次实验，可查看或导出。 |
| 超参数优化 | `optimize_page.py`、`charts.py`、`parallel_coordinates.py`、`workspace_state.py` | 对齐七种搜索算法的默认值、预算范围与提示。移植累计最优、目标分布、参数与目标散点、状态与进度、参数平行坐标。试验列表增加搜索、状态与参数范围筛选、排序、分页、详情及勾选 2 至 8 条比较，替换当前只显示前 100 条的做法。显示默认基线、最佳参数和真实搜索诊断。提供「返回当前任务」，查看历史不改变活动任务或新任务配置。 |
| 实验比较 | `compare_page.py`、`workbench_widgets.py` | 增加历史搜索、筛选、单条概览、完整详情、并排对比表和跳转到对应优化工作区。显示数据、版本与划分证据、算法、预算、指标与单位。证据不足时只并排展示，不生成排名。 |
| 任务日志 | `logs_page.py` | 保留已实现的复制和清空显示。补齐搜索、匹配计数、实时更新、自动滚动开关和日志导出。清空只作用于显示，分页读取持久事件。 |
| 自主优化 | `loop_page.py`、`application/loop_service.py` | 补齐 CNC 文件选择、试验次数、搜索时间、训练总预算和「演示重调参」。续跑要把 `manual_demo` 传给后端。会话详情拆成审计时间轴、诊断与门禁、模型与参数、数据划分。按实际状态提供继续、批准、保留冠军、回滚及导出，沿用会话修订号、门禁约束和人工确认。 |
| 工业训练 | `industrial_page.py`、`application/industrial_service.py` | 补齐文件和目录导入、桌面参数默认值与范围。结构化显示数据审计、分类指标、各工况、搜索记录、搜索曲线、训练曲线、模型产物和日志。补上「与当前工业实验并排查看」，复用服务已有的公平比较判定。 |

表中的页面文件均在上表「桌面版七个页面」目录下。图表和公共组件在「桌面版主窗口、任务调度、公共图表和交互」目录下。应用服务在「原 Python 应用服务」目录下。

> 上表保留原始待办口径。本轮已把导入入口从「单文件 8 MB」改为「最多 9 个文件、单文件 32 MB」，多个文件追加到同一 `UP-*` 目录；后端单次请求体上限 64 MB。逐项完成状态见文末「本轮移植完成状态」。

## 推荐实施顺序

1. **先统一实际调用参数。** 对照桌面版 `main_window.py`、`workers.py` 和应用服务，逐项核对数据来源、模型、种子、树数、早停、线程和预算。目前小程序各页共用一套表单状态，`backend/actions.py` 固定使用 2 个线程，并将试验上限、时间上限设为 128 次、1800 秒，和桌面范围不同。各页独立管理配置，核对实际落盘参数。对于工业训练，注意桌面默认搜索时间为 300 秒，当前小程序共用 120 秒。
2. **统一新结果与历史结果。** 任务结束时按返回的实验 ID 或会话 ID 读取详情。当前任务和正在查看的历史记录独立管理；快速切页、任务完成、切回前台或取消时，结果按 ID 归属，避免覆盖用户正在看的记录。分页返回试验和日志，避免把整个大型 JSON 一次传给页面。
3. **先完成优化工作台，再完成其他页面。** 优先实现图表与试验表的联动。累计最优图保留完整任务序列，筛选仅高亮；分布图和参数散点按筛选集合绘制。默认基线单独展示。训练、工业、会话和日志复用已保存的结果字段。
4. **保持数据与算法边界。** 测试集只在显式最终评估中使用。保留数据指纹、固定划分、验证口径及原离线门禁约束。MAE 保留目标单位，固定 J 无量纲，分类指标与回归指标分别呈现。缺少真实数据的残差、参数重要性等内容显示空态。

## 验收清单

- [ ] 用同一数据版本、种子和实际配置，从桌面版与小程序分别执行钢铁能耗基线、同预算搜索和工业分类。核对落盘配置、划分指纹、试验状态与关键指标，记录任何差异及原因。
- [ ] 验证自定义数据导入后能用于训练，切换数据集不误用旧文件。大文件和多文件导入明确支持的格式及失败提示。
- [ ] 用历史实验核对图表、分页、筛选、单条详情、并排比较和 ZIP 导出。检查超过 100 条试验时仍可查看。
- [ ] 用微信开发者工具实际走一遍自动连接、运行、取消、看历史和导出。任务期间切页后回到当前任务，仍能看到正确进度。
- [ ] 验证自主优化预算、演示重调参、未经授权不运行、门禁未通过不能切换，以及通过后需人工确认。用隔离工作区做会改变会话状态的验收。
- [x] 运行适当的后端测试和小程序编译检查。可参考 `backend/tests/smoke.cjs` 与 `backend/tests/test_workspace.py`。分别记录已通过、失败和未验证项。→ 2026-11-10 已完成：16 项 `unittest`、6 个 JS 文件 `node --check`、WXML/WXSS 静态检查、HTTP 冒烟测试与真实数据接口验收全部通过；结果明细见 `PLAN_RAC_HPO_PORT.md`「验收记录」。微信开发者工具手机端模拟器编译已于 22:27 通过（改用 `miniprogram/` 目录导入），七页 UI 功能流程与真机仍列为未验证项。

## 本轮移植完成状态（2026-11-10）

本轮按「推荐实施顺序」1–4 推进，代码层面的补齐已完成，人工与跨端验收仍待接手者执行。上面的待办表保持原样，便于逐条复核。

| 页面 | 本轮实现 | 主要文件 |
|---|---|---|
| 数据管理 | 校验结果拆成数据概览、固定划分、预定义工况、字段及单位、来源与许可、SHA-256、质量审计、清单与限制；支持最多 9 个文件、单文件 32 MB，多文件追加同一目录；导入文件与数据集绑定，切换数据集或页面自动清除 | `view.js` 的 `dataSections()`、`index.js` 的 `uploadFiles()`、`server.py` 的 `/api/uploads` |
| 模型训练 | 独立 seed / 树数配置，默认 42 / 300，后端 `n_jobs=4`；显示验证 MAE、固定 J、总耗时、分工况样本及 MAE、实际模型参数；结束可打开本次结果 | `config.js` 的 `pageConfig.train`、`view.js` 的 `trainSections()` |
| 超参数优化 | 累计最优、目标分布、参数与目标散点、状态分布、平行坐标五类图表（canvas 2d）；试验搜索、状态与参数范围筛选、排序、分页、完整详情、勾选 2–8 条比较；默认基线、最佳参数、搜索诊断、状态计数、配置证据；「返回当前任务」与按 ID 归属的当前/历史结果 | `charts.js`、`views.py` 的 `charts()` / `trial_rows()`、`/trials` 与 `/charts` 接口 |
| 实验比较 | 历史搜索、勾选 2–8 条、单条概览、并排对比表、跳转到优化工作区；暴露数据 SHA-256、划分、组定义、版本与预算证据；证据不足时 `ranking=False` 只并排展示 | `view.js` 的 `compareView()`、`views.py` 的 `compare_rows()` |
| 任务日志 | 搜索、匹配计数、自动滚动开关、分页读取持久事件、JSONL 导出、复制、清空显示（只清显示） | `/api/jobs/{id}/events`、`/api/jobs/{id}/export`、`views.py` 的 `event_rows()` |
| 自主优化 | 场景、试验次数、搜索时间、训练总预算、CNC 文件、授权、演示重调参；`loop_create` / `loop_run` 均传 `manual_demo`，策略经 `LoopPolicy` 范围校验；会话详情拆成审计时间轴、诊断门禁、模型参数、数据划分 | `actions.py` 的 `loop_create` / `loop_run`、`view.js` 的 `loopSections()` |
| 工业训练 | 数据集/方法/模型/阈值策略与独立参数（24 次 / 300 秒）、多文件导入、审计/获取/运行、结构化显示审计与分类指标、各工况、搜索记录、曲线采样、产物与日志、工业图表、并排比较复用 `industrial.compare()` 判定 | `view.js` 的 `industrialSections()`、`server.py` 的 `/api/compare` |

仍需人工按「验收清单」逐条走完：跨端同配置对照实验、自定义数据与大文件导入的端到端流程、超过 100 条试验在小程序 UI 中的分页/筛选/详情/比较/ZIP 导出、开发者工具自动连接与运行/取消/切页回看、自主优化授权与门禁流程（建议隔离工作区）。本轮完成了接口层、静态检查与手机端模拟器编译渲染；交付物是手机端微信小程序，模拟器内 UI 功能流程与真机验收仍待执行，接口层测试不能替代 UI 验收。2026-11-13 起顶部入口合并为「运行 / 结果 / 记录」三页，上表七类功能全部保留：运行页用任务类型切换，结果页看图表、比较与详情，记录页看历史、日志与会话。

接手时先核对当前工作区及未提交修改。这份文件只记录待办和验收要求；本节的完成状态仅作进度参考，不替代人工验收。
