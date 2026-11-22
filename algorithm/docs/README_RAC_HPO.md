# 智训引擎微信小程序

小程序位于 `miniprogram/`，Python API 位于 `backend/`。后端核心来源为 RAC-HPO `9badeed`，保留 Apache 2.0 许可。原项目备份保存在源工程的 `workspace/labsafety-backup-20261109`。

## 本机联调

使用 Python 3.11。可复用原 RAC-HPO 的 Python 环境，或者在本项目创建虚拟环境并安装 `backend/requirements.txt`。

```powershell
python backend/server.py
```

微信开发者工具用「导入项目」选择 **`miniprogram/` 目录**（该目录自带 `project.config.json`，项目根即小程序根），编译最稳。项目根目录也可以直接打开，因为根目录 `project.config.json` 的 `miniprogramRoot` 指向 `miniprogram/`。2026-11-13 已把无关的 VS Code 扩展备份（`exports/`，5.3 万文件、1.4 GB）移到项目外的 `F:/A_LIU_Astrspire/A_competition/Labsafety-exports-backup/`，根目录文件数从约 5.9 万降到约 6 千，不会再出现文件监听失败、模拟器创建超时（表现为编译无输出、`something wrong in electron appservice` 或停在欢迎页）。备份目录完整保留，需要时可移回。工具若缓存了旧的项目状态，重启开发者工具或重新导入项目即可。

小程序会自动连接 http://127.0.0.1:8765，进入单机工作区。开发者工具自身仍需登录以打开项目。训练与数据保存在这台电脑。底部「输出」面板默认选中的是 `Extension Host`（编辑器扩展日志），编译结果要看「构建」标签或把日志源切到小程序。另开终端运行 node backend/tests/smoke.cjs 可验证接口，会新增真实实验记录（2026-11-10 已通过，详见 `PLAN_RAC_HPO_PORT.md`）。

导入文件最多选择 9 个，单个文件上限 32 MB，后端单次请求体上限 64 MB；多个文件会追加到同一个 `UP-*` 目录，供工业多文件/目录适配器使用。导入文件与当前数据集绑定，切换数据集或页面时自动清除，避免误用旧文件。超过上限的大型数据集由管理员按对应数据适配器的目录结构放入工作区。结果支持 ZIP 导出，回归与分类指标分别显示和比较。自主优化使用原有 CNC 协议，保留授权、候选门禁和人工切换。

历史实验详情不再一次返回全部 trials。试验列表、图表序列与任务日志分别通过 `/api/runs/{id}/trials`、`/api/runs/{id}/charts`、`/api/jobs/{id}/events` 分页读取，日志可经 `/api/jobs/{id}/export` 导出为 JSONL。累计最优图始终保留完整任务序列，筛选只影响分布、散点和平行坐标。

## 数据复制

当前本机已复制到 `rac-data/`。若要重建，在源桌面程序停止写入时运行以下命令，目标目录必须不存在。

```powershell
python backend/import_workspace.py '<RAC-HPO source path>' '<new workspace path>'
```

原始数据、来源、清单、历史实验、模型和导出文件使用 SHA-256 校验。SQLite 使用一致性备份及完整性检查，复制后的数据库将原工程路径改成工作区内相对路径。历史工业清单保留原文件，新工业任务在当前工作区独立保存清单。数据目录不进入 Git 或镜像。

## 单机使用范围

Python 服务仅监听本机回环地址。微信开发者工具模拟器和 Python 服务需在同一台电脑运行。
