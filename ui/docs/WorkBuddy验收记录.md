# 智维云本地验收记录

- 日期：2026-11-22 14:46–15:06（GMT+8）
- 工作树：`F:\A_LIU_Astrspire\A_competition\Labsafety`，分支 `main`，保留全部用户未提交改动、AppID、原始数据、数据库与历史实验。
- 验收边界：只做本机编译、隔离后端与模拟器/本机 HTTP 验收。未做 Git 提交/推送、小程序上传与发布、真机验证。
- 隔离工作区：`rac-data/workbuddy-verify-20261122-144819`（仅复制 `configs/` 和 `datasets/raw/ai4i_2020/`，端口 8765，`LAB_MODE=local`，所有请求带 `X-Lab-Local: 1`）。原始数据与 `rac-data` 主工作区未被写入验收数据。
- 状态口径：**通过 / 失败 / 未验证** 分开标注；AI4I 为公开合成数据，结果为离线实验，不是实时设备状态；演示 CSV 为本地新建的**测试夹具**，非真实生产数据。

## 1. 后端与前端自动检查 — 通过

| 命令 | 结果 |
| --- | --- |
| `git status --short --branch` | `## main`，存在用户未提交改动与未跟踪文件（本次未改动这些文件） |
| `& $python -c "import pandas, sklearn, lightgbm, optuna"` | `deps OK`，退出码 0 |
| `& $python -m unittest discover -s backend/tests -p 'test_*.py'` | **Ran 21 tests … OK**，退出码 0（6.571s） |
| `& $python backend/tests/industrial_smoke.py` | `PASS: real AI4I RAC-HPO task, progress, saved result and trials`；`PASS: job and experiment remain available after restart` |
| `node --check miniprogram/pages/workspace/index.js` | 退出码 0 |
| `node --check miniprogram/pages/workspace/config.js` | 退出码 0 |

Python 为原项目现有环境 `F:\A_LIU_Astrspire\A_competition\工业制造场景下的模型训练与超参数优化系统\.venv\Scripts\python.exe`（Python 3.11.15）。系统默认 Python 缺 pandas 的问题未出现在本验收环境。

## 2. 隔离工作区与后端启动 — 通过

- 启动前 `Get-NetTCPConnection -LocalPort 8765` 无监听，无相关 `python.exe` 进程；未停止任何不相关服务。
- `RAC-HPO listening on 127.0.0.1:8765`；`GET /api/dashboard` 返回 `datasets_present: 1`。
- 首页真实统计：`本地数据源 1 / 完成任务 0 / 运行任务 0`。
- 失败反馈：停止本次后端后请求返回连接失败（`无法连接到远程服务器`）；重启后 dashboard 显示 `completed_tasks: 1` 且历史任务可查，重试连接恢复。

## 3. 微信开发者工具 — 打开通过 / 编译通过 / 真机未验证

- CLI 打开：`'D:\tools\微信web开发者工具\cli.bat' open --project ...\miniprogram --lang zh` → 输出 `√ open`（非致命的 punycode 弃用告警与 Extension Host manifest 缺失告警，不作为编译判定）。
- 窗口标题 `LabSafe RAC-HPO`，项目路径 `F:\A_LIU_Astrspire\A_competition\Labsafety\miniprogram`，AppID `wx94334f520838a9d0`（未改动）。**已确认打开的是本项目**。
- 编译：首次截图显示旧页面「无法连接本机后端」，日志表明开发者工具在本机后端启动前（14:38/14:41）已打开该项目，属于**过期页面状态**。执行本地编译（Ctrl+B）后模拟器重新加载并显示已连接首页，构建区无编译错误。截图：`shot_devtools_crop.png`（打开初期）、`shot_after_compile.png`（编译后）。
- 真机访问：**未验证**（本轮范围仅本机编译和模拟器）。

## 4. 模拟器/本机接口流程

说明：AI4I 训练与自定义 CSV 的**上传、检查、训练、拒绝校验**使用交接文档第 3 节明确许可的本机 HTTP 接口执行（同一隔离工作区、同一后端调用链）；模拟器侧完成首页、数据预览入口、任务表单、结果/历史/失败记录的可视确认。

### 4.1 首页与数据预览 — 通过

- 模拟器首页显示「智维云 · 工业设备数据分析」、真实统计、「AI4I 2020 是合成示例数据」标识，`ai4i_2020` 行有「预览」按钮。截图：`shot_sim_home_top.png`。
- `GET /api/datasets/ai4i_2020/preview?limit=5` 返回 14 个真实字段：`UID, Product ID, Type, Air temperature, Process temperature, Rotational speed, Torque, Tool wear, Machine failure, TWF, HDF, PWF, OSF, RNF`，5 行示例值（如 `1, M14860, M, 298.1, 308.6, 1551, 42.8, 0, …`），`note=原始数据预览；未显示的字段可能仍参与数据审计`。
- 模拟器点击预览控件为内联展开，自动化点击偶发未命中；预览数据本体已由接口核对通过，模拟器内联展开不作为单独通过项。

### 4.2 公开数据任务（AI4I） — 通过

- 请求体：`action=industrial_run, dataset=ai4i_2020, algorithm=rac_hpo, model=random_forest, seed=42, estimators=10, trials=2, timeout=60`。
- 任务 `JOB-609377f6ef7dd915cd74`（创建返回 `running`）→ 完成，结果 `IND-fa3c15a09a6a4a7f`。
- 真实调用链证据：事件含 `started / baseline_completed / trial_completed × 2 / finished`；2 次试验 `selection.method = tpe_fallback`、`reason = insufficient_history`。**本轮 2 次试验只覆盖算法调用链与预热阶段，代理门禁（RAC 代理排序）未被证明生效**（选择记录明确为 TPE 回退）。
- 持久化：重启本次隔离后端后，`dashboard.completed_tasks=1`，`/api/jobs` 与 `/api/runs` 仍返回该任务/实验，按 ID 打开 `IND-fa3c15a09a6a4a7f` 成功，`/trials` 返回 `total=2`。
- 结果与图表：`/api/runs/{id}` 返回分类结果；`/trials` 分页 2 条含参数、目标值、耗时、选择方式；`/charts` 返回 `history / distribution / scatter / status / parallel` 五类图表数据。模拟器「记录」页可见该实验、`#0/#1` 两条完成试验、累计最优图与「导出 ZIP / 复制」按钮（`shot_history_tab.png`）。
- 分类指标（**评估集：validation 验证集；`evaluation_partition=validation`，`test_accessed=false`**）：`overall_balanced_accuracy=0.7101`、`overall_f1_macro=0.7756`、`overall_log_loss=0.0499`、`samples=2000`、`class_counts={0:1955, 1:45}`。另有 H/L/M 三种工况子集指标，均为验证集切片，不是测试集性能。
- ZIP 导出：`GET /api/runs/IND-fa3c15a09a6a4a7f/export?kind=zip` → `200`、`application/zip`、`Content-Disposition: attachment; filename="IND-fa3c15a09a6a4a7f.zip"`、172798 字节，仅在本机接收，未做远端发布。
- 任务创建后 UI 仍可继续操作；完成后可从「记录」打开结果与逐轮记录。

### 4.3 自定义 CSV — 通过（接口路径）/ 模拟器上传未验证

- **测试夹具**：`智维云实施/fixtures/custom_test.csv`（UTF-8，列 `device_id,label,temp`，12 行；0/1 两类各 3 个设备、每设备 2 行、每设备只属于一个类别、temp 为数值）。**这是本地测试夹具，不是生产数据**。
- 上传：`POST /api/uploads?ext=csv&name=fixture.csv`（原始字节）→ `UP-c2b2cae80e48105559c8`。
- 检查：`GET /api/uploads/UP-c2b2cae80e48105559c8/inspection` → 12 行 3 列；`device_id` 12 个非数值（分组列）、`label`/`temp` 0 个非数值；`note=用户上传数据；请选择标签、分组和数值特征…`。
- 审计：`JOB-6c83f7c274c9784bc7f9` 完成；识别 `task_type=classification`、`features=[temp]`、`target=label`、`group_definition=device_id`、整组分层划分 `train/validation/test = 4/4/4`、`data_origin=user_supplied`、`provenance_verified=false`，`source_files[0].path` 指向 `uploads/UP-c2b2cae80e48105559c8/fixture.csv`。
- 训练：`JOB-6a11da660495ce019c5d` → `IND-62811810fffc43b5`，`algorithm=baseline`，结果 `config.data_path` 指向用户上传文件（**来源已确认是上传夹具**），`evaluation_partition=validation`，`test_accessed=false`。分类指标（**评估集：validation 验证集，仅 4 行**）：`balanced_accuracy=0.5`、`f1_macro=0.3333`、`samples=4`。样本极小，仅证明链路可用，不作为性能结论。
- 拒绝校验（各至少一次，全部被拒）：
  1. 重复列名 → 检查接口 `400 {"error":"CSV must have 1–100 distinct nonempty column names"}`。
  2. 错误列映射 → 映射 `target=temp, features=[label]` 的训练失败：`ValueError: Classification target must contain finite integer class IDs`；映射不存在列 `nonexistent_col` 的审计失败：`ValueError: Selected column not found in CSV`。
  3. 类别分组不足（每类仅 1 个设备）→ `JOB-a6cb86e0674f80c968d2` 失败：`ValueError: Each class requires at least three physical groups for train/validation/test`。模拟器「记录」页也显示该失败原因（`shot_history_top.png`）。
- **模拟器上传：未验证**。自动化未能完成原生文件选择器的本地文件选取：数据集需在内嵌 picker 中滚动到 `user_csv`（第 7 项）后触发系统文件对话框，本轮未取得可复现的模拟器上传成功证据；按交接许可改由本机 HTTP 接口完整验证上传、检查、训练与拒绝路径。模拟器表单/数据集 picker 可正常打开（`shot_form2.png`、`shot_dataset_picker.png`）。

### 4.4 失败反馈 — 通过

- 停止本次隔离后端后重新请求：本机 HTTP 返回连接失败（`无法连接到远程服务器`）；模拟器启动初期截图显示清楚文案「无法连接本机后端。请启动 Python 服务，再点重试。」并提供「重试连接」按钮（`shot_devtools_crop.png`）。
- 重启后端（同一工作区）后 dashboard、任务、结果与历史均恢复；模拟器编译后自动显示已连接首页与历史实验，重试连接路径通过。

## 5. 本次改动造成的问题与修复

- **未发现需修复的源码问题**。初始「无法连接本机后端」经日志核对为开发者工具在后端启动前打开项目导致的过期页面状态，重新编译后消失，非本次改动引入的功能缺陷。
- 交接文档阶段审计中提示「任务完成前事件处理丢弃 trial 明细」的风险点：本轮真实 AI4I 训练的事件与 `/api/runs/{id}/trials` 均含完整 2 条试验明细（参数、目标值、耗时、选择方式），且重启后保留；未观察到该缺陷复现，因此未做改动。
- 未改动任何源码文件；本轮新增文件仅为验收证据截图、`fixtures/` 测试夹具、临时工作区路径记录与本报告。

## 6. 总体结论

| 项目 | 状态 |
| --- | --- |
| 后端自动检查（依赖/21 项单测/真实冒烟/JS 语法） | 通过 |
| 隔离工作区启动与端口/数据隔离 | 通过 |
| 微信开发者工具打开项目 | 通过 |
| 微信开发者工具本地编译 | 通过 |
| 模拟器首页/数据预览（接口核对） | 通过 |
| AI4I 真实分类训练 + 逐轮记录 + 图表 + 重启后历史 | 通过 |
| AI4I ZIP 导出（仅本机） | 通过 |
| 自定义 CSV 上传/检查/审计/baseline（本机接口） | 通过 |
| CSV 重复列名/错误映射/分组不足拒绝 | 通过 |
| 模拟器内 CSV 文件选择器上传 | 未验证 |
| 失败反馈与重启重试 | 通过 |
| RAC 代理门禁实际生效 | 未验证（2 次预热试验均为 `tpe_fallback`） |
| 真机访问、预测接口、生产部署、多用户隔离 | 未验证（本轮范围外，另有验收要求） |
| Git 提交/推送、小程序上传/发布 | 未执行（需另行授权） |

收尾：已停止本轮启动的后端进程；临时夹具与隔离验收数据目录按交接要求清理；保留本报告与证据截图。
