# LabSafe AI · 智维云 —— 项目总索引

> 竞赛演示用的单机实验室安全智能平台：**本地 Python 算法引擎 + 微信小程序前端**。
> 全部为虚构/公开演示数据；后端只监听 `127.0.0.1`，不对外暴露。
> 本仓库按分工拆成两块，clone 后各取所需即可协作。

---

## 一、分工与目录导航

| 目录 | 负责人 | 内容 | clone 后是否需要 |
|---|---|---|---|
| [`algorithm/`](algorithm/README.md) | **算法侧** | Python 后端、`rac_hpo` 引擎、全量数据集、训练/优化/评估、PyTRIO 大模型训练 | 算法侧必读；UI 侧可忽略 |
| [`ui/`](ui/README.md) | **UI 侧** | 微信小程序源码、界面设计原型、效果图、UI/验收文档 | UI 侧必读；算法侧可忽略 |
| [`docs/`](docs/) | 共用 | 整体方案、历史改造备份 | 双方可选阅读 |

```
Labsafety/
├── README.md                 本文件：总索引、分工、文档地图、运行入口
├── algorithm/                算法侧（你）
│   ├── README.md             算法包说明：环境、启动、API、测试、数据
│   ├── backend/              HTTP 服务 + rac_hpo 引擎 + 单元测试
│   ├── rac-data/             全量数据：配置、原始数据集、验证工作区、实验记录
│   ├── docs/                 算法文档：RAC-HPO 移植、训练数据交接、PyTRIO 接入
│   ├── Dockerfile            云端镜像构建（可选）
│   └── .env                  PyTRIO API Key（本地私有，已被 .gitignore 排除，不入库）
├── ui/                       UI 侧（你的朋友）
│   ├── README.md             UI 包说明：小程序结构、编译、与设计稿对应
│   ├── miniprogram/          微信小程序源码（页面、图表、样式、视图模型）
│   ├── project.config.json   微信开发者工具项目根配置
│   ├── design/               界面概念原型 HTML、图表/波形预览、界面效果图
│   └── docs/                 UI 与验收文档、验收截图
└── docs/                     共用：整体优化方案 V3、改造前代码备份
```

> **重要**：clone 得到的仓库里**没有 `.env`**（含 API Key）。算法侧需按 `algorithm/README.md` 自行创建，或把 Key 填在小程序「大模型训练」表单里。密钥不会出现在 Git 历史中。

---

## 二、快速开始

### 想看界面（UI 侧 / 无需 Python）
1. 打开微信开发者工具 →「导入项目」→ 选择本仓库的 **`ui/` 目录**（推荐）。
2. **也可以直接选仓库根目录**：根目录的 `project.config.json` 已把 `miniprogramRoot` 设为 `ui/miniprogram/`，两种方式都能正确编译。
   > 若提示 `app.json: 在项目根目录未找到 app.json`，说明工具仍指向旧位置：请「关闭项目 / 重新导入」并选上述目录，或点工具栏「项目 → 重新打开」，让工具重读新的 `project.config.json`。
3. 编译即可看到界面。**无需 Python、无需后端**：小程序自动探测本机引擎，探测不到就进入离线演示模式，用内置的真实结果展示全部界面与交互（训练按钮会回放预置结果）。详见 [`ui/docs/离线演示模式说明.md`](ui/docs/离线演示模式说明.md)。

### 想跑算法（算法侧 / Python 3.11）
```bash
cd algorithm
python -m venv .venv && source .venv/bin/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
python backend/server.py            # 默认 http://127.0.0.1:8765，工作区为 algorithm/rac-data
```
- 单元测试：`python -m unittest discover -s backend/tests -p 'test_*.py'`
- 详细参数、PyTRIO Key 配置、环境变量见 [`algorithm/README.md`](algorithm/README.md)。

> 后端与小程序必须同机运行；小程序通过 `http://127.0.0.1:8765` 连接后端。

---

## 三、文档地图（每份文档讲什么、谁该看）

### 算法侧必读 — `algorithm/docs/`
| 文档 | 内容 | 建议读者 |
|---|---|---|
| `README_RAC_HPO.md` | 引擎移植总览、本机联调、数据复制、单机范围 | 算法侧 |
| `PLAN_RAC_HPO.md` | RAC-HPO 移植实施记录（精简版） | 算法侧 |
| `PLAN_RAC_HPO_PORT.md` | 小程序移植的完整计划与执行记录、接口细节 | 算法侧 + UI 侧联调 |
| `RAC_HPO_微信小程序移植交接.md` | 移植交接说明（项目与工作目录） | 算法侧 |
| `LabSafe_AI_模拟实验室训练数据交接文档.md` | 模拟训练数据、来源与模型成果交接 | 算法侧 |
| `PyTRIO接入说明.md` | 大模型（LoRA SFT）训练接入、实测结果、Key 配置、成本与限制 | 算法侧 |
| `github-projects.md` | 相关开源项目调研 | 双方可选 |
| `screenshots/` | 训练结果、loss 曲线、PyTRIO 运行截图（10 张） | 算法侧 |

### UI 侧必读 — `ui/docs/` 与 `ui/design/`
| 文档 / 文件 | 内容 | 建议读者 |
|---|---|---|
| `LabSafe_AI_架构与后续实施步骤.md` | 原生小程序架构、本次已落地与后续待做、边界说明 | UI 侧 |
| `WorkBuddy验收交接.md` | 本机验收的交接说明与步骤 | UI 侧 + 算法侧 |
| `WorkBuddy验收记录.md` | 逐项验收结果（引用同目录 `shot_*.png` 共 7 张） | UI 侧 |
| `7.3_人工验收操作卡.md` | 开发者工具 + 模拟器人工验收操作卡 | UI 侧 |
| **`离线演示模式说明.md`** | **零配置打开即用：演示/真实双模式、能力边界、如何切回真训练** | **UI 侧必看** |
| `实施计划与审计.md` | 改造目标、边界、实施顺序与审计 | UI 侧 + 算法侧 |
| `design/界面概念原型.html` | 最终采用的界面交互原型（可浏览器打开） | UI 侧 |
| `design/chart-preview.html` / `chart-schematic.html` / `waveform-preview.html` | 图表、波形设计预览 | UI 侧 |
| `design/screenshots/` | 界面改版前后、效果与联调截图（14 张） | UI 侧 |

### 共用 — `docs/`
| 文档 | 内容 |
|---|---|
| `LabSafe_AI_整合优化项目方案_V3.md` | 整体平台方案、GitHub 竞品解析（V3） |
| `archive/改造前代码备份_20261122_143245.zip` | 界面改造前的代码备份，供对照回滚 |

---

## 四、给协作者的两点提醒

1. **历史文档里的路径**：早期文档写的是 `backend/`、`rac-data/`、`miniprogram/`（均在仓库根）。重组后：
   - `backend/` 与 `rac-data/` → `algorithm/backend/`、`algorithm/rac-data/`
   - `miniprogram/` 与 `project.config.json` → `ui/`
   - 命令中的相对路径需相应加前缀；接口本身、端口号（8765）未变。
2. **数据与密钥**：`algorithm/rac-data/` 的全量数据已入库；真实 PyTRIO Key 只在本机 `.env`，永不入库，请各自保管自己的 Key。

---

*本索引随仓库结构维护；目录或文档增减时请同步更新此文件。*
