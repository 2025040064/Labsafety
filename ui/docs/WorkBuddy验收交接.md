# 智维云本地验收交接

在 `F:\A_LIU_Astrspire\A_competition\Labsafety` 接续当前工作树。先跑后端基线，再用微信开发者工具实际操作 AI4I 和自定义 CSV 两条流程。交付验收记录，保留可复现的失败信息。发现本次改动造成的问题时，先复现再做最小修复。

- 保留现有未提交改动、微信 AppID、原始数据、数据库和历史实验。验收新增任务写入独立工作区。
- 仅做本机编译和模拟器验收。Git 提交、推送、小程序上传及发布须另获用户授权。
- 架构、改造前备份和历史审计见本目录的 `实施计划与审计.md`。当前首页、目录和 CSV 入口已经写入工作树，以当前源码为准。

## 1. 先跑自动检查

使用 Windows PowerShell，在项目根目录执行：

```powershell
git status --short --branch
$source = 'F:\A_LIU_Astrspire\A_competition\工业制造场景下的模型训练与超参数优化系统'
$python = Join-Path $source '.venv\Scripts\python.exe'
& $python -c "import pandas, sklearn, lightgbm, optuna"
& $python -m unittest discover -s backend/tests -p 'test_*.py'
& $python backend/tests/industrial_smoke.py
node --check miniprogram/pages/workspace/index.js
node --check miniprogram/pages/workspace/config.js
```

原项目的 Python 3.11 环境已验证可用。系统默认 Python 缺 `pandas`，曾导致基线 5 项报错。现有后端测试最近一次为 21/21 通过。AI4I 冒烟已验证真实 RAC-HPO 搜索、逐轮记录和重启后的历史查询，时间点早于最后一轮 CSV 接入，请以上述重跑结果为准。两轮搜索只覆盖算法调用链和预热阶段，代理门禁是否启用以实际证据为准。

## 2. 启动独立工作区和微信开发者工具

在项目根目录新开一个 PowerShell 窗口执行：

```powershell
$source = 'F:\A_LIU_Astrspire\A_competition\工业制造场景下的模型训练与超参数优化系统'
$python = Join-Path $source '.venv\Scripts\python.exe'
$verifyRoot = Join-Path (Get-Location) ('rac-data/workbuddy-verify-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path (Join-Path $verifyRoot 'datasets/raw') | Out-Null
Copy-Item -LiteralPath 'rac-data/configs' -Destination $verifyRoot -Recurse
Copy-Item -LiteralPath 'rac-data/datasets/raw/ai4i_2020' -Destination (Join-Path $verifyRoot 'datasets/raw') -Recurse
$env:LAB_ROOT = $verifyRoot
$env:LAB_MODE = 'local'
$env:PORT = '8765'
$env:MPLCONFIGDIR = Join-Path $verifyRoot '.cache/matplotlib'
& $python backend/server.py
```

当前小程序连接本机 `http://127.0.0.1:8765`。端口若已占用，先确认进程和数据目录，避免在现有服务上跑验收或停止不相关进程。另开终端检查：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8765/api/dashboard' -Headers @{ 'X-Lab-Local' = '1' }
& 'D:\tools\微信web开发者工具\cli.bat' open --project (Join-Path (Get-Location) 'miniprogram') --lang zh
```

用户已重新登录，CLI 曾返回 `√ open`。打开项目与编译通过是两条验收记录。确认开发者工具打开的是本项目的 `miniprogram/`，点击本地「编译」。查看「构建」和模拟器，不以 Extension Host 日志判定小程序编译结果。若自动截图捕获到其他窗口，先确认目标窗口再取证。

## 3. 模拟器验收

1. **首页与数据预览**：确认显示「智维云」、服务连接和真实统计。打开 AI4I 预览，检查字段与记录、公开合成示例标识。
2. **公开数据任务**：选择「创建工业分类任务」→ `ai4i_2020` → `rac_hpo` → `random_forest`。先用种子 42、10 棵树、2 轮、60 秒跑通。记录实际 `JOB-*` 和 `IND-*`。确认任务创建后可继续操作，完成后能打开结果、查看验证指标、最优参数、逐轮记录和图表。关闭并重新启动本次隔离后端，确认同一实验仍可从历史打开。验证 ZIP 导出时只保存到本地。
3. **自定义 CSV**：创建本地 UTF-8 测试 CSV，列为 `device_id,label,temp`。使用 0 和 1 两个类别，每类至少 3 个不同设备，每个设备写 2 行，每个设备只属于一个类别，`temp` 为数值。选 `user_csv`，上传单个 CSV，核对行数和列质量，映射标签 `label`、分组 `device_id`、特征 `temp`。先点「审计」，再跑 10 棵树的 `baseline`，查看持久化结果和用户上传来源。验证重复列名、错误列映射和类别分组不足至少各拒绝一次。如果模拟器无法使用文件选择器，记录具体错误，另用本机 HTTP 接口验证上传、检查和训练，并保留模拟器上传为未验证。
4. **失败反馈**：停止本次隔离后端，重新触发请求，检查错误是否清楚。重启后测试重试连接。

手动接口排查可复用同一隔离工作区。上传使用 `POST /api/uploads?ext=csv&name=fixture.csv` 和原始文件字节，检查使用 `GET /api/uploads/{id}/inspection`。创建任务使用 `POST /api/jobs`，自定义 CSV 的请求体包含 `action: industrial_run`、`dataset: user_csv`、`upload: UP-...`、`algorithm: baseline` 和 `mapping: {target, group, features}`。所有本机请求带 `X-Lab-Local: 1`。任务和结果分别从 `/api/jobs/{id}` 与 `/api/runs/{id}` 查询。

## 4. 回写验收结果

把结果写入本目录的 `WorkBuddy验收记录.md`。为后端自动检查、真实训练、微信编译、模拟器流程和真机分别标记「通过 / 失败 / 未验证」，记录命令、实际测试数量、任务 ID、构建信息和失败原因。分类指标注明评估集，演示 CSV 标明是测试夹具。若有修复，列出改动文件和复测结果。

本轮优先完成上述本机验收。完整任务书中的预测接口、生产部署及多用户隔离有独立验收要求，仅在实际完成后标记通过。结束时停止本次启动的后端，清理本次生成的临时夹具和验收数据目录，保留验收记录与所需证据。
