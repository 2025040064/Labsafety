# algorithm/ —— 算法侧交付包

> 负责人：算法侧。本目录是一套**单机本地优先**的 Python 训练/优化引擎与 HTTP 服务，
> 为微信小程序提供数据与算法能力。UI 侧同学无需阅读本目录，只需知道后端地址 `http://127.0.0.1:8765`。

---

## 一、目录结构

```
algorithm/
├── backend/
│   ├── server.py              HTTP 服务入口（默认监听 127.0.0.1:8765）
│   ├── actions.py             动作分发：prepare/train/optimize/industrial_*/loop_*/llm_*
│   ├── views.py               实验、试验、图表的汇总与分页
│   ├── catalog.py / import_workspace.py   数据目录与工作区导入
│   ├── requirements.txt       Python 依赖（已含 pytrio 0.2.9 / pydantic 2.12.5）
│   ├── engine/src/rac_hpo/    核心引擎（数据适配、训练、优化、评估、存储）
│   │   └── application/llm_service.py     PyTRIO LoRA SFT 服务
│   └── tests/                 后端测试（test_*.py）+ 冒烟脚本
├── rac-data/                  全量工作区数据（已随仓库入库）
│   ├── configs/               默认配置 default.yaml
│   ├── datasets/              原始数据集与清单（AI4I、steel_energy 等）
│   ├── workspace/             历史实验、模型、导出、工业任务结果
│   ├── workbuddy-verify-*/    本机验收工作区快照
│   └── lab.sqlite3 / lab.lock 工作区数据库与锁
├── docs/                      算法文档
│   ├── README_RAC_HPO.md      引擎移植总览与本机联调
│   ├── PLAN_RAC_HPO.md / PLAN_RAC_HPO_PORT.md   移植计划与执行记录
│   ├── RAC_HPO_微信小程序移植交接.md
│   ├── LabSafe_AI_模拟实验室训练数据交接文档.md
│   ├── PyTRIO接入说明.md      大模型训练接入与实测结果
│   ├── github-projects.md     开源项目调研
│   └── screenshots/           训练结果 / loss 曲线截图（10 张）
├── Dockerfile                 云端镜像构建（可选，build context = 本目录）
└── .env                       PyTRIO API Key（本地私有，已被 git 忽略；clone 中不存在）
```

---

## 二、环境准备（Python 3.11）

```bash
cd algorithm
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
# source .venv/bin/activate

pip install -r backend/requirements.txt
```

依赖要点：
- 训练/优化栈：numpy、pandas、scipy、scikit-learn、lightgbm、xgboost、optuna、matplotlib。
- PyTRIO 大模型训练：`pytrio==0.2.9`，需要 `pydantic>=2.12.5`，清单已锁到 **2.12.5**（与引擎兼容且实测通过）。若 pip 报与原项目 `==2.12.3` 约束不一致，属预期现象。

---

## 三、启动服务

```bash
cd algorithm
python backend/server.py
```

- 默认地址：`http://127.0.0.1:8765`
- 默认工作区：`rac-data/`（相对当前目录）。启动时会读取 `algorithm/.env` 中的 `PYTRIO_API_KEY`。
- 只在单机回环监听；需与微信开发者工具在同一台电脑运行。
- 云端/自定义工作区用环境变量：`LAB_ROOT=<工作区路径>`、`LAB_MODE=cloud`、`PORT=80`（见 Dockerfile）。

> 早期文档（如 `README_RAC_HPO.md`）中的命令写的是 `python backend/server.py`，
> 那是重组前的写法。现在所有 `backend/`、`rac-data/` 都在 `algorithm/` 下，
> 请先 `cd algorithm` 再执行命令，接口与端口未变。

### 配置 PyTRIO API Key（可选，仅大模型训练需要）
按优先级任选其一：
1. 创建 `algorithm/.env`，写入 `PYTRIO_API_KEY=<你的key>`（推荐，已被 git 忽略）；
2. 或启动服务前设置环境变量 `PYTRIO_API_KEY=<你的key>`；
3. 或在小程序「大模型训练」表单里粘贴 Key。

没有 Key 时，本地训练照常可用；只有大模型训练会被明确拒绝，不会产生付费调用。
详见 [`docs/PyTRIO接入说明.md`](docs/PyTRIO接入说明.md)。

---

## 四、测试

```bash
cd algorithm
python -m unittest discover -s backend/tests -p 'test_*.py'    # 后端测试（26 项）
python -m unittest discover -s backend/engine/tests            # 引擎测试（可选，较全）
node backend/tests/smoke.cjs                                    # 接口冒烟（会写入真实实验记录）
```

> 引擎测试部分依赖源工程数据/环境，普通联调跑 `backend/tests` 即可。

---

## 五、主要 HTTP 接口（供 UI 侧对接）

| 路径 | 用途 |
|---|---|
| `POST /api/jobs` | 创建任务（action 见 `actions.py` 的 `ACTIONS`） |
| `GET /api/jobs/{id}` / `GET /api/jobs/{id}/events` | 任务状态 / 分页日志 |
| `GET /api/runs`、`GET /api/runs/{id}` | 实验列表 / 详情（含 `LLM-*`） |
| `GET /api/runs/{id}/trials` / `/charts` | 试验列表 / 图表序列（分页） |
| `POST /api/llm/status` | 同步检测 PyTRIO SDK 与 Key |
| `GET /api/datasets/{key}/preview` | 数据集预览 |
| `GET /health` | 健康检查 |

所有本机请求需带请求头 `X-Lab-Local: 1`（`ui/miniprogram/api.js` 已内置）。

---

## 六、数据与边界说明

- `rac-data/` 全部为虚构/公开演示数据，已随仓库入库；真实数据源、许可见
  [`docs/LabSafe_AI_模拟实验室训练数据交接文档.md`](docs/LabSafe_AI_模拟实验室训练数据交接文档.md)。
- 服务只做单机演示：Python 服务监听回环地址，训练与数据留在本机，**不是**生产级认证/审计系统。
- 密钥与本机私有配置不入库；`workbuddy-verify-*` 是本机验收快照，可安全删除后用同结构重建。
