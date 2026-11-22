# RAC-HPO 小程序移植：实施计划与执行记录

本轮目标：按《RAC_HPO_微信小程序移植交接.md》把桌面版七个页面的可用功能移植到 `miniprogram/`，保持单机运行方式。工作过程中读写本文件，避免偏离方向。

## 事实依据

- 桌面版原工程：`F:/A_LIU_Astrspire/A_competition/工业制造场景下的模型训练与超参数优化系统/`，提交 `9badeed`。
- 桌面默认值来自 `configs/default.yaml`：`seed=42`、`n_estimators=300`、`early_stopping_rounds=30`、`n_jobs=4`、`max_trials=12`、`timeout_seconds=120`。
- 桌面控件范围（`pages/*.py`）：训练树数 10–10000 默认 300；搜索试验 1–10000 默认 12、时间 1–86400 默认 120；工业试验 1–10000 默认 24、时间 0.1–86400 默认 300；闭环策略 `LoopPolicy` 为 `max_trials ≤48`、`timeout_seconds ≤120`、`training_budget_seconds ≤300`。
- 移植前现状：小程序只有一个页面（`index.js` 172 行、`index.wxml` 87 行），七页共用一套表单参数；`backend/actions.py` 固定 `n_jobs=2`、上限 128 次 / 1800 秒；`/api/runs/{id}` 直接返回含全部 trials 的详情，单个真实 `result.json` 约 540 KB；试验列表只取前 100 条；日志只取最近 100 条事件。
- 数据结构：回归实验 `EXP-*` 的 `trials` 含 `number`（基线为 -1）、`status`、`objective`、`metrics.overall_mae`、`params`、`selection`、`duration_seconds`；工业实验 `IND-*` 另有 `run.json`；闭环会话 `LOOP-*` 有 `state`、`revision`、`policy`、`gate`、`champion`、`candidate`、`active_model`。

## 架构决定

1. **后端算视图、小程序只渲染。** 大 JSON 不进页面：`/api/runs/{id}` 去掉 `trials`，只返回摘要与轻量图表元信息；试验、日志和完整图表序列分别走分页接口与独立接口。累计最优图保留完整任务序列，筛选只影响高亮与分布/散点/平行坐标集合。
2. **各页独立配置。** 小程序每个页面有自己的参数对象，切换数据集会清空绑定的导入文件；后端只做范围校验，不替页面决定默认值。
3. **结果按 ID 归属。** `active`（当前任务）与 `viewing`（正在看的记录）分开。任务完成后按返回 ID 可一键打开本次结果；查看历史不覆盖正在看的记录，也不改写新任务配置。
4. **不引入云托管、账号或生产部署。** Python 服务仍只监听 `127.0.0.1:8765`，小程序自动连接。
5. **保持数据与算法边界。** 测试集只在显式最终评估读取；MAE 带目标单位、固定 J 无量纲、分类与回归指标分开显示；缺失证据处显示空态，不生成排名。

## 接口契约（本轮新增）

| 接口 | 返回 |
|---|---|
| `GET /api/runs/{id}` | 原详情去掉 `trials`，增加 `trial_count`、`baseline` 与轻量 `charts` 元信息（目标定义、状态计数、参数名），**不返回图表序列** |
| `GET /api/runs/{id}/charts?q&status&param&low&high&sort&order` | `history`（累计最优）、`distribution`、`scatter`、`status`、`parallel` 序列，按同一套筛选条件计算 |
| `GET /api/runs/{id}/trials?page&size&q&status&sort&order&param&low&high` | `{total, page, size, rows}`；`rows` 为精简试验记录；支持搜索、状态、参数范围和排序 |
| `GET /api/jobs/{id}/events?page&size&q` | `{total, page, size, rows}`；按持久事件分页，搜索返回匹配计数 |
| `GET /api/jobs/{id}/export` | 任务事件 JSONL 文本，供日志导出 |
| `GET /api/compare?id=...&id=...` | 保留原语义，补齐数据/划分/版本证据字段；工业比较仍返回公平比较判定 |
| `POST /api/uploads?ext&name&upload` | 单文件返回文件 id；带 `upload` 时向同一目录追加文件，供工业多文件/目录导入 |

图表序列按需从 `/charts` 加载，避免详情接口重复传输。累计最优保留完整任务序列，筛选只影响分布、散点和平行坐标。

## 参数对照（前端默认值 → 后端落盘）

| 页面 | seed | n_estimators | max_trials | timeout_seconds | n_jobs |
|---|---|---|---|---|---|
| 模型训练 | 42 | 300 | — | — | 4 |
| 参数优化 | 42 | 300 | 12 | 120 | 4 |
| 工业训练 | 42 | 120（工业内核自带默认，可覆盖） | 24 | 300 | 4 |
| 自主优化 | 42（策略固定） | — | 48 | 120，训练总预算 300 | 由闭环服务决定 |

其余早停轮数、搜索空间、鲁棒目标参数仍由 `rac-data/configs/default.yaml` 提供，不在页面重复定义。

## 文件清单

- 新增 `backend/views.py`：试验精简、筛选排序分页、图表序列、摘要与比较行。
- 改 `backend/actions.py`：范围校验、各页参数、工业与闭环实际调用、`manual_demo` 续跑。
- 改 `backend/server.py`：新路由、日志导出、去除详情中的全量 trials。
- 新增 `miniprogram/pages/workspace/config.js`、`charts.js`、`view.js`。
- 改 `miniprogram/pages/workspace/index.js`、`index.wxml`、`index.wxss`。
- 新增 `backend/tests/test_views.py`、`backend/tests/test_actions.py`；`backend/tests/test_workspace.py` 保持原样并纳入同一轮回归。

## 实施顺序与进度

- [x] 1. 核对两端代码、默认参数与实验数据结构，写本计划。
- [x] 2. 后端参数统一 + 分页/视图接口。
- [x] 3. 小程序七页独立配置、结果归属、优化工作台与图表。
- [x] 4. 其余六页结构化展示与交互。
- [x] 5. 测试、静态检查、验收记录与文档更新。

## 验收记录（2026-11-10）

### 已通过

| 项目 | 命令 / 接口 | 结果 |
|---|---|---|
| 后端单元测试 | `python -m unittest discover -s backend/tests -v` | 16 项全部通过，`OK`（4.59s） |
| 小程序 JS 语法 | `node --check` 覆盖 `api.js`、`app.js`、`config.js`、`charts.js`、`view.js`、`index.js` | 6/6 通过 |
| WXML 结构 | 标签配对与事件 handler 静态检查 | 247 个开标签、224 个闭标签、23 个自闭合，无未闭合或错配；28 个 handler 在 `index.js` 全部有定义；5 个字符串型 `data-source` 选择数组均存在 |
| WXSS 结构 | 花括号配对与样式类覆盖 | 32/32 配对；29 个 class 标识符中，除 `index`、`chartKind`、`key` 三个 mustache 表达式变量外均有 `.class` 定义 |
| HTTP 冒烟 | `node backend/tests/smoke.cjs` | 通过：403 鉴权、ZIP 导出（PK 头）、prepare、train、optimize、industrial_run、取消、并排比较、未确认 promote 被拒，六条真实任务链路均返回预期状态 |
| 详情瘦身 | `GET /api/runs/EXP-010bc65ebc894a8a` | 5879 bytes，无 `trials`，`trial_count=181`，`charts` 仅 `objective_label` / `parameters` / `status` |
| 图表按需 | `GET /api/runs/{id}/charts` | 约 146 KB；`history=181`、12 个分布桶、6 组散点（1086 点）、`parallel.rows=181`。`status=completed` 不改变集合；`learning_rate∈[0.01,0.05]` 时分布 16、平行坐标 16、散点 96，而 `history` 恒为 181 |
| 试验分页筛选 | `GET /api/runs/{id}/trials` | `learning_rate∈[0.01,0.2]` 命中 181 条，第 3 页返回 5 行；`q=0.05` 命中 6 条；`page=999` 返回空行且不越界 |
| 比较证据 | `GET /api/compare` | 两条新 `EXP-*` 返回 2 行、不含 `trials`、`ranking=False`；工业 `IND-d65829497a924660` + `IND-1f11ac22513e472b` 为 `comparable=True`，另一对证据不足的组合为 `comparable=False`，两者均未生成排名 |
| 日志接口 | `GET /api/jobs/{id}/events`、`/export` | 分页与搜索可用；导出 72 bytes / 1 行 JSONL |
| 多文件导入 | `POST /api/uploads?upload=...` | 同一 `UP-*` 目录写入 `part1.csv`、`part2.csv`，`count=2`；验收用的临时目录已删除 |
| 手机端模拟器编译 | 微信开发者工具以 `miniprogram/` 为项目目录导入 | 2026-11-10 22:27 截图确认：七个 tab、「无法连接本机后端」错误卡、「本机工作区」卡片与「重试连接」按钮全部正常渲染，`something wrong in electron appservice` 白屏消失 |

冒烟测试后 `/api/runs` 从 88 条增加到 92 条，新增 3 条 `EXP-*`（含 1 条取消）、1 条 `IND-*` 与 1 个 `LOOP-*` 会话。

### 失败后已修复

- 首轮 `unittest` 16 项中 2 项失败：`test_event_search_returns_match_count_and_page` 的断言写错了事件顺序；`filter_rows()` 降序时把缺失目标值的试验排到最前。前者修正断言，后者改为把 present / missing 分组后只排序 present，复测 16/16 通过。
- 详情首轮瘦身仍有约 151 KB，因为 `run_summary()` 内嵌完整图表序列并与 `/charts` 重复传输；改为详情只留轻量元信息、序列统一走 `/charts` 后降到 5879 bytes。
- 静态检查与联调中发现并修正：字符串型 picker 误用数值索引、比较条数判断写反、导入文件未按数据集隔离、工业页 canvas ID 不一致、canvas 节点晚于 `setData` 出现导致绘制失败（增加重试）。
- `index.wxss` 首次写入被工具拒绝（未先读取），读取后重写成功。
- 微信开发者工具首次编译白屏（`something wrong in electron appservice`）。工具日志给出确凿原因：`[DevtoolsFileWatcher] ... false`、`[PreCompiler] updateFileAndDirs files=46896 dirs=3217`、`onProxyError /__pageframe__/instanceframe.html timeout/500`、`routeTo appLaunch timeout` 连续 6 次——项目根目录混入无关的 `exports/vscode-extensions-2026-10-17`（VS Code 扩展备份 5.3 万文件，全项目 5.9 万文件），文件监听失败、模拟器资源服务超时，并非业务代码错误（模拟小程序环境加载 5 个模块全部成功，`Page.data` 70 字段）。顺带查出 `index.wxml` 5 处 `{{upload.names.join('、')}}` 是 WXML 不支持的方法调用，已改为 JS 里预计算 `upload.label`；此前 WXML 静态检查只覆盖标签配对与 handler，未覆盖表达式方法调用，属于检查盲点。修复方式为新增 `miniprogram/project.config.json` 与 `project.private.config.json`（不设 `miniprogramRoot`，可直接以 `miniprogram/` 作为项目目录导入，只扫描 11 个文件），并在根目录 `project.config.json` 的 `packOptions.ignore` 中排除 exports/backend/rac-data 等非小程序目录。

### 未验证 / 已知限制

- **交付物是手机端微信小程序**（用户 2026-11-10 明确强调），不是桌面应用、网页或后台服务。验收必须以手机端模拟器 / 真机 UI 为准，接口层测试与静态检查不能替代 UI 验收。
- 2026-11-10 22:16 首次编译出现 `something wrong in electron appservice` 白屏，已按工具日志定位到项目根目录体积并改用 `miniprogram/` 导入；22:27 截图确认**手机端模拟器编译已成功、页面正常渲染**。后端随后启动，`/health`、`/api/me`（admin）、`/api/runs`（92 条）正常。但点「重试连接」之后的七页 UI 功能流程（运行、取消、切页回看当前任务、历史、图表、比较、ZIP 导出）以及真机**仍未验收**；开发者工具需重新登录。
- 桌面版与小程序同数据版本、同 seed、同预算的对照实验未执行。
- 大于 32 MB 的单文件、多文件工业数据集的完整真实导入与失败提示未端到端验收；目前只验证了上传接口追加文件与目录返回。
- 历史实验 `EXP-010bc65ebc894a8a` 的 `selection.method` 均为空，按该字段搜索会正确返回空集；这是产物字段本身缺失，不是筛选实现问题。
- `backend/tests/smoke.cjs` 会新增真实实验记录，运行前需知悉；建议在隔离工作区执行会改变会话状态的验收。
- Docker/cloud 模式、微信登录、真实成员、持久存储、真实数据上传与费用仍未验证，也不在本轮单机范围内。

## 图表增强实施（2026-11-11）

按 `chart-preview.html` 的效果图落地六类新增图表。**只改小程序端，后端接口与算法核心未改动**，因此无需重启 Python 服务，但微信开发者工具需重新编译。

### 实现与数据来源

| 图表 | 实现 | 真实数据来源 |
|---|---|---|
| 结果概览 KPI | `index.wxml` 卡片 + `view.js` 的 `kpiCards()` | `best_value`、`trial_count`、`metrics.overall_mae` / `overall_error`、`target_unit` |
| 分工况 MAE 条形 | WXSS 百分比宽度 | `metrics.regimes`，回归取 `mae`（带单位），分类取 `error`（无量纲），两者不混用 |
| 试验状态环形 | `charts.js` 的 `drawRing()` + WXML 图例 | `/charts` 与详情 `charts` 的 `status` 计数，全量统计，不随筛选变化 |
| 数据划分堆叠条 | WXSS 百分比宽度 | `manifest.split_summary.{train,validation,test}.rows`（工业）或 prepare 结果 `split_counts`（数据管理）；测试集只在显式最终评估读取 |
| 训练曲线采样折线 | `charts.js` 的 `drawCurve()` | `metrics.learning_curve` 真实采样点 `iteration` / `train_loss` / `validation_loss` |
| 分类 / 回归指标卡 | `view.js` 的 `indicatorCards()` | `metrics` 中 `overall_f1_macro`、`overall_balanced_accuracy`、`overall_error`、`overall_mae`、`robust_objective` 等，最多 4 项 |
| 并排比较柱状 | `view.js` 的 `compareBars()` | `/api/compare` 的 `runs[].best_value` 与 `target_unit`；单位不一致或缺少数值时只并排展示，不绘制柱状图、不生成排名 |

改动文件：`view.js`（新增 `visuals()` / `compareBars()` 与六个预计算函数）、`charts.js`（提取 `withContext()` 共用重试，新增 `drawRing()` / `drawCurve()`）、`index.wxss`（新增 KPI / 条形 / 堆叠 / 柱状 / 环形 / 图例样式）、`index.wxml`（新增 `id="visuals"` 卡片与比较柱状）、`index.js`（`visual` / `compareBars` 状态、`openDetail` / `reloadTrials` / `compareRuns` / `watch` / `clearOutput` 接线、`drawVisuals()` 调度、滚动目标改为 `#visuals`）。

所有百分比、标签、颜色、className 都在 `view.js` 预计算，WXML 表达式不调用 JS 方法。缺失数据显示空态，不绘制残差、参数重要性或排名；现有五类 canvas 图表与「累计最优保留完整序列、筛选只影响分布/散点/平行坐标」的语义保持不变。

### 本轮检查（2026-11-11）

- 后端 `unittest discover`：16/16 通过（后端未改动，回归确认）。
- `node --check` 6/6；WXML 303 个标签无错配、28 个 handler 全有定义、`{{}}` 中无方法调用、52 个静态 class 全部有样式定义、6 个 element id 无重复；WXSS 花括号 67/67 配对；3 处动态 `style` 绑定均为纯数据路径。
- 模拟小程序环境：全部模块加载成功（`Page.data` 72 字段），五类原有 painter 加 `drawRing` / `drawCurve`（含空数据分支）实际执行无异常。
- 真实接口端到端接线：`openDetail(EXP-069b4039b663450f)` 得到 1 段状态环 + 2 条工况条；`openDetail(IND-375b971cd11e4d7e)` 得到 3 段划分、4 张指标卡、10 个曲线采样点；`compareRuns()` 得到 2 根柱（单位 kWh）；`clearOutput()` 正确清空；全程 `error` 为空。

### 仍需人工验收

微信开发者工具**重新编译**后在手机端模拟器逐页查看新增卡片：EXP / IND 详情的 KPI、工况条、状态环、划分堆叠、曲线与指标卡，以及并排比较柱状；确认 canvas 在页面滚动后仍正常绘制、切 tab 不丢失图表、真机表现。本轮只做了静态检查、模拟环境与接口层联调，未做手机端 UI 验收。

## 三页合并（2026-11-13）

用户要求「不要这么多的页面，综合一下」，选择把七个入口合并为 3 个 tab，**功能不删**。

| 新 tab | 内容 | 原入口 |
|---|---|---|
| 0 运行 | 任务类型 chips + 对应表单，一次只显示一张 | 0 数据管理 / 1 模型训练 / 2 参数优化 / 5 自主优化 / 6 工业训练 |
| 1 结果 | 实验切换、五类图表主图、试验列表、并排比较、数据可视化、结构化详情 | 2 / 3 / 6 的图表与比较 + 全局结果卡片 |
| 2 记录 | 历史实验、任务日志与持久事件、闭环会话列表 | 3 / 4 / 5 的列表 |

架构决定：

- `tab` 只表示三个大页面；新增 `task`（0 数据审计 / 1 基线训练 / 2 参数搜索 / 3 工业训练 / 4 自主优化）表示运行页内部类型。
- 数据集绑定从 `keyForTab(tab)` 改为 `keyForTask(task)`；切换任务类型或数据集时清除不匹配的导入文件。
- 打开实验或会话详情统一切到「结果」页；任务日志列表改为直接加载持久事件，留在记录页，避免来回切页。
- `sectionsFor()` 不再依赖 tab，改按 `id` 前缀与 `algorithm` 判断展示模板。
- 原两个主图 canvas 合并为一个 `id="chart"`；`drawChart()` / `drawVisuals()` 只在结果页执行，防止 canvas 节点不存在时重试报错。
- 结果页加实验切换 picker（`runChoices`），并记录页勾选比较后自动切到结果页查看结果。

### 三页合并验收（2026-11-13）

- 静态检查：`node --check` 6/6；WXML 303 标签无错配、31 个 handler 全有定义（新增 `switchTask` / `pickRun` / `openJobLog`）、`{{}}` 无方法调用、52 个静态 class 全有样式、5 个 element id 无重复；WXSS 67/67 括号配对；`tab` 取值只有 0/1/2，`task` 取值 0–4；13 个 `data-action` 与 `backend/actions.py` 的 `ACTIONS` 完全一致；5 个 picker 的 `data-source` 均存在。
- 模拟小程序环境端到端：3 个 tab、5 个任务类型切换后的数据集键分别为 `steel_energy` / `ai4i_2020` / `cnc_milling_2025`；记录页 refresh 加载 92 条实验、24 个任务、3 个会话，结果页加载 92 条实验并生成 92 个 `runChoices`；`details()` 打开 EXP 自动落在结果页并得到状态环 1 段、工况条 2 条、指标卡 2 张；`pickRun()` 切到 IND 得到划分 3 段、曲线 10 个采样点、指标卡 4 张；`compareRuns()` 得到 2 根柱（kWh）并切到结果页；`reloadTrials()` 得到 181 条试验、首页 20 行、`history` 181、`scatter` 6；`loadEvents()` 读到 1 条持久事件。全程 `error` 为空。
- 两个主图 canvas 合并为 `id="chart"`（`chart-industrial` 移除）；`drawChart()` / `drawVisuals()` 只在结果页运行，避免在其它页因节点不存在触发重试报错。
- 后端未改动，无需重启 Python 服务；微信开发者工具需**重新编译**。
- 未做手机端 UI 验收：需在开发者工具导入 `miniprogram/` 后逐页确认任务类型 chips、结果页图表与可视化、记录页列表与日志事件，以及真机表现。

## 编译卡死根治（2026-11-13 10:33）

- 第三次复发：开发者工具 `autoOpen` 自动打开项目根目录，日志 `2026-11-13-10-31-15-767.log` 显示 `genCreateSimulatorOptions error timeout` 从 10:32:27 反复到 10:33:27，`2026-11-13-10-31-23-584.log` 显示 `[DevtoolsFileWatcher] ... false`。模拟器始终停在欢迎页，Network 面板无请求。
- 根因：根目录 `exports/vscode-extensions-2026-10-17`（VS Code 扩展备份，53255 文件 + 1.47 GB zip）让预编译器全量扫描 46900 文件、文件监听失败。`packOptions.ignore` 与 `ignoreDevUnusedFiles` 对 `non-conditional` 扫描无效，配置层面解决不了。
- 处理：把整个 `exports/` 同盘移动到项目外 `F:/A_LIU_Astrspire/A_competition/Labsafety-exports-backup/`，**完整保留、可逆、未删除任何文件**。根目录文件数 59338 → 6082（`.git` 4705 / `rac-data` 1193 / `backend` 144 / `miniprogram` 13）。这样根目录项目本身也能编译，不必再手动切导入目录。
- 配置同步：根目录 `project.config.json` 的 `packOptions.ignore` 去掉已不存在的 `exports`、加入 `.git`；README 启动说明改为「根目录现在也能编译，但导入 `miniprogram/` 最稳」。
- 后端未改动，服务仍在本机 8765 运行。
- 待用户操作：**重启微信开发者工具**（重建文件监听缓存）后点编译；若仍异常，改用「导入项目」选 `miniprogram/`。手机端 UI 验收仍未完成。

## 数据处理流程图标 + 图表（2026-11-13 10:45）

用户要求「加图标类的来展示数据处理过程」，并强调「我就是要加图标」、随后补「图表、」。按**图标与图表都要**实现，不是再画一组数值图表。

- 新增全局「数据处理流程」卡片（三个 tab 都能看到），六个圆形阶段图标用连接线串起：数据接入 / 来源校验 / 质量审计 / 固定划分 / 工况与特征 / 验证结果。蓝底=有真实数据，灰底=字段未提供并显示「未提供」；一个真实字段都没有时整张卡片不渲染。
- 图标下面配两张图表，并把它们从结果可视化卡片移到流程卡片（避免重复）：固定划分堆叠条（`manifest.split_summary` / `split_counts`）与工况条形图（`metrics.regimes` 的 `mae` / `error`；prepare 结果的工况样本数改画「工况样本分布」）。
- 每个图标的摘要只用真实字段：`row_count` / `manifest.rows` / `metrics.samples`、`source_sha256` / `data_sha256`、`quality_audit.audited_rows` 与 `flagged_source_rows`、`split_fingerprint`、`regime_definition`、`feature_order`、`target_unit`、`overall_mae` / `overall_error` / `overall_f1_macro`。边界不变：测试集只在显式最终评估读取、分类与回归指标分开、不排名、不补全。
- 改动：`view.js`（`processStages()` / `processStage()` / `splitSummaryText()`、`visuals()` 增加 `process`、`regimeBars()` 兼容数字型工况）、`index.wxss`（`.stage-*` / `.badge-*`）、`index.wxml`（新增流程卡片，从 `id="visuals"` 移出划分与工况两块）。`index.js` 与后端未改。
- 检查：`node --check` 6/6；WXML 313 标签无错配、31 handler 全有定义、`{{}}` 无方法调用、60 个静态 class 全有样式、5 个 id 无重复；WXSS 78/78 括号配对。真实数据验证：prepare 6 节点 5 已完成（验证结果未提供）、划分 3 段、工况样本 2 条；EXP 4 节点有值、工况条 2 条；IND 6 节点全有值、划分 3 段、工况 3 条、曲线 10 点。空态与 `clearOutput()` 后 stages=0、`any=false`，卡片不显示。
- 过程中 `index.wxss` 的编辑工具连续两次 backup 超时，改用 Python 追加写入并复核通过（无内容丢失）。
- 手机端 UI 仍未验收：需在微信开发者工具重新编译后看图标链、连接线与两张图表的实际排版。

## 数据流向 + 实时波形（2026-11-13 11:18）

用户要求把示意图里的内容「全部加入」。本轮新增两项，其余（流程图标、划分堆叠、工况条形、五类 canvas 图表）此前已实现。

- **数据流向条形图**：放在「数据处理流程」卡片顶部，按 原始 → 已审计 → 训练 / 验证 / 测试 递减显示记录数，测试集用灰色标出。数据来自 `row_count` / `manifest.rows`、`quality_audit.audited_rows`、`manifest.split_summary` 或 `split_counts`；缺字段不画。
- **实时波形卡片**：放在「结果」页图表之后。canvas 每 120ms 推进一次，逐点画出折线、末端圆点与扫描线，支持暂停/继续/从头回放，显示已播放点数与当前值。
  - 工业实验回放 `metrics.learning_curve`（IND 有 10 个真实采样点，双通道：训练/验证损失）。
  - 优化实验回放 `/charts` 的 `history`（181 个真实试验点，双通道：单次试验/累计最优）。
  - 数据不足时不渲染卡片；`onHide`、切页、`clearOutput`、打开新详情都会停表并清缓存。
- 实现：`view.js` 新增 `dataFlow()`、`waveSeries()`；`charts.js` 新增 `context()`（缓存 canvas 节点）与 `paintWave()`（重绘网格、折线、圆点、扫描线）；`index.js` 新增 `wave` 状态与 `waveStart / waveTick / waveCurrent / waveToggle / waveReplay / stopWave / paintWave`；`index.wxml` 加数据流向块与波形卡片；`index.wxss` 加 `.chip` / `.flow-*`。
- 边界：波形只画真实序列，不插值、不补点、不拟合；暂停后进度不变；`stopWave()` 改用整对象 setData（原先的 `'wave.running'` 路径写法在测试环境不生效）。
- 检查：`node --check` 6/6；WXML 344 标签无错配、33 个 handler 全有定义、`{{}}` 无方法调用、61 个 class 全有样式、6 个 id 无重复；WXSS 85/85 括号配对。真实数据端到端：prepare 流向 35040→27744→20832/6912/7104；EXP-010bc65ebc894a8a 波形 181 点、600ms 后 index=12、暂停后 index 不变、重播回到 0；IND 波形 10 点、900ms 后 index=7；全程 `error` 为空。
- **真正的实时流仍未做**：`/api/jobs/{id}/events` 只有 started / trial_completed / finished 等离散事件，没有连续数值。要在训练过程中看到实时滚动，需要在 `progress_callback` 里补数值指标并写入 `job_events`，属于后端改动，尚未执行。

### 稳定性加固（2026-11-13 11:27）

开发者工具报 `Cannot read properties of null (reading 'screenHeight')`、`routeDone with a webviewId 5 is not found`，日志同时有 `[DevtoolsFileWatcher] ... false`。三者都在基础库 / 工具层（`WAServiceMainContext.js`、`WASubContext.js`），业务代码里没有读 `screenHeight`。仍做了三处防御：

- `charts.js` 的 `getContext()` 对 `wx.getWindowInfo()` 返回值加 `|| {}` 兜底，避免窗口信息未就绪时读属性抛错。
- `waveTick()` / `paintWave()` / `drawVisuals()` 增加 `this.visible` 与 `tab === 1` 判断，页面不可见或切走后立即停表，不再向后台页面 setData 或绘制 canvas。
- `wx.pageScrollTo` 增加 `fail() {}`，节点未渲染完时静默失败，不抛错。

验证：模拟 `getWindowInfo()` 返回 `null`，打开 181 点实验后波形正常推进（index=9），`visible=false` 后自动停表且 index 不变，切到「运行」页也停表，全程 `error` 为空。

工具侧仍需人工处理：完全关闭开发者工具再打开（不是点编译）；若仍报，改用「导入项目」选 `miniprogram/`（13 个文件）。根目录仍有 6082 个文件（`.git` 4705、`rac-data` 1193），`DevtoolsFileWatcher` 仍返回 false。

### 结果页空态引导（2026-11-13 11:30）

用户反馈「这几个东西一个没见到」。原因是图表、实时波形、可视化都只在打开实验后渲染，而「结果」页初始是空的。

- 「结果」页新增空态卡片：列出最近 5 条实验（`best_value` 有值的排前面），点一条直接打开；附「去记录页看全部」按钮（`gotoRecords`）。
- 进入「结果」页且 `viewingId` 为空时，自动打开最近一条有结果的实验，不再看到空白页；`refresh()` 用 `autoOpening` 标志防止重复触发。
- 验证：进入结果页后 `recentRuns` 5 条（24.75 / 23.74 …），自动打开 `EXP-069b4039b663450f`，`chartSeries=true`、流程图标 6 段、工况条 2 条、波形 2 点，`error` 为空。
- 注意：`EXP-069b4039b663450f` 只有 2 个 trial，波形很短；要看密集波形请选 `EXP-010bc65ebc894a8a`（181 点）。
