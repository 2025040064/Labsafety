# ui/ —— UI 侧交付包

> 负责人：UI 侧。本目录是微信小程序源码 + 界面设计稿 + UI/验收文档。
> 只需要看界面、改样式、调交互，专注这里即可；算法逻辑在同仓库的 `algorithm/`，本侧无需改动。

---

## 一、目录结构

```
ui/
├── miniprogram/                  小程序源码
│   ├── app.js / app.json / app.wxss   全局入口与全局样式
│   ├── pages/workspace/
│   │   ├── index.wxml            页面结构（表单、卡片、状态条、结果区）
│   │   ├── index.wxss            页面样式（深海军蓝/靛蓝/青绿主题、卡片、胶囊 tab）
│   │   ├── index.js              页面逻辑（任务提交、轮询、结果查看）
│   │   ├── config.js             任务/数据集/模型/参数默认值与文案映射
│   │   ├── view.js               结果详情分区与格式化
│   │   ├── charts.js             Canvas 绘图：历史曲线、loss 曲线、波形等
│   │   └── index.json            页面配置
│   ├── api.js                    双模式请求层：探测本机、连不上自动降级演示、模拟任务
│   ├── demo-data.js              内置演示数据（真实接口响应快照，约 215KB）
│   └── project.config.json       小程序自身的工程配置（appid 等）
├── project.config.json           微信开发者工具项目根配置（miniprogramRoot=miniprogram/）
├── design/                       界面设计与效果
│   ├── 界面概念原型.html          最终采用的完整交互原型（浏览器可直接打开）
│   ├── chart-preview.html / chart-schematic.html / waveform-preview.html
│   └── screenshots/              改版前后与效果截图（14 张）
└── docs/                         UI 与验收文档
    ├── LabSafe_AI_架构与后续实施步骤.md
    ├── WorkBuddy验收交接.md / WorkBuddy验收记录.md（引用同目录 shot_*.png 共 7 张）
    ├── 7.3_人工验收操作卡.md
    └── 实施计划与审计.md
```

---

## 二、打开与编译

1. 安装微信开发者工具（需微信登录）。
2. 「导入项目」→ 目录选择 **`ui/`**（项目根）。
   - 本目录 `project.config.json` 的 `miniprogramRoot` 为 `miniprogram/`，工具会自动定位源码。
   - 也可直接选 `ui/miniprogram/`（该目录也带 `project.config.json`），两种方式都可编译。
   - 仓库根目录同样可打开：根 `project.config.json` 已把 `miniprogramRoot` 指向 `ui/miniprogram/`。
   - 若报 `app.json 未找到`，是工具缓存了旧项目位置，用「项目 → 关闭项目 / 重新导入」选上述目录即可。
3. 编译后进入「智维云」小程序，顶部为 3 个结果页签（运行/结果/记录），任务胶囊共 6 项：
   `数据审计 / 基线训练 / 参数搜索 / 工业训练 / 自主优化 / 大模型训练`。
4. `project.private.config.json` 为个人 IDE 私有配置，已被 git 忽略，不会随仓库分发。

> **零配置可用**：编译后小程序自动探测本机 `127.0.0.1:8765`；探测不到就进入**离线演示模式**，用内置的真实结果展示全部界面与交互，不需要 Python、不需要后端。UI 侧看界面、走查交互走这条即可。
> 要**真正训练**：在 `algorithm/` 下启动后端，并在开发者工具勾选「不校验合法域名」，或在顶部模式条手动切到「用本机引擎」。详情见 [`docs/离线演示模式说明.md`](docs/离线演示模式说明.md)。

---

## 三、界面与代码对应

| 你看到的元素 | 对应代码 |
|---|---|
| 品牌头、本机引擎在线徽章、KPI 卡、任务胶囊 | `index.wxml` + `index.wxss` |
| 各任务表单与默认值 | `config.js`（`pageConfig` / `tasks` / 各数据集与模型列表） |
| 提交、轮询、结果跳转、密钥检测 | `index.js` |
| 结果详情分区（训练配置、loss 曲线、采样对比等） | `view.js` |
| 曲线/波形 Canvas 绘制 | `charts.js` |
| 后端地址与请求头、演示模式探测/降级 | `api.js` |
| 内置真实响应快照 | `demo-data.js` |
| 最终视觉稿 | `design/界面概念原型.html` |

UI 改造前后的对照见 `design/screenshots/`，改造前的完整代码备份在仓库根 `docs/archive/`。

---

## 四、文档导读（谁该看）

| 文档 | 内容 |
|---|---|
| `LabSafe_AI_架构与后续实施步骤.md` | 小程序架构、已落地 vs 待做、演示边界（不是真实认证/审计/AI） |
| `实施计划与审计.md` | 改造目标、边界、实施顺序与审计记录 |
| `WorkBuddy验收交接.md` | 本机验收交接说明 |
| `WorkBuddy验收记录.md` | 逐项验收结果，配 `shot_*.png` 7 张（**请勿移走截图**，文档按文件名同目录引用） |
| `7.3_人工验收操作卡.md` | 开发者工具 + 模拟器人工验收步骤 |
| **`离线演示模式说明.md`** | **零配置打开即用：演示/真实双模式、能做什么、数据来源、如何切回真训练** |

> 历史文档中的 `miniprogram/` 指仓库根下的同名目录，重组后已移到 `ui/miniprogram/`；
> 其中的 `backend/`、`rac-data/` 已移到 `algorithm/` 下，接口与端口未变。

---

## 五、协作约定

- **只改 UI 侧**：改样式/交互/设计稿时，尽量不动 `index.js` 中任务提交与轮询的字段结构，
  它们要与后端 `algorithm/backend/actions.py` 的参数保持一致；需要新字段请与算法侧沟通。
- **提交前**：确认没有把个人 `project.private.config.json` 或任何 API Key 一并提交（均已 git 忽略）。
