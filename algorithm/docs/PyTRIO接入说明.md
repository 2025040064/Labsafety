# 智维云 · PyTRIO 大模型训练接入说明

## 一、这是什么

在小程序的「运行」tab 新增了第六种任务类型 **大模型训练**（PyTRIO），与现有的数据审计 / 基线训练 / 参数搜索 / 工业训练 / 自主优化**完全隔离、互不干扰**。

- 现有五类任务：本机 CPU 跑 LightGBM / scikit-learn / Optuna，处理结构化工业数据。
- 新增第六类任务：本机只组织数据和训练循环，**GPU 计算通过 PyTRIO Training API 发到云端**，做 LoRA SFT 微调 + 训练前后采样对比。

两者不是一回事，不存在"填个 key 就让原有训练走 PyTRIO"的接法。

## 二、当前状态（已验证）

| 项目 | 状态 |
|---|---|
| pytrio SDK（0.2.9） | 已安装到项目 venv，含 transformers / modelscope 依赖 |
| 后端 `llm_status` 检测 | 通过：同步接口 `/api/llm/status` 直接返回 `sdk:true, key:true`，不再占用 job 队列 |
| 后端 `llm_train` 无 key 拒绝 | 通过：明确报"未配置 PyTRIO API key"，不发起云端调用（未配置时） |
| 小程序第六个任务胶囊 + 表单 | 编译通过，字段渲染正常，切任务自动检测密钥 |
| 双队列并行 | 已验证：本地 `train` 与 `llm_train` 可同时运行；训练中调密钥检测仍即时返回 |
| LLM 结果展示 | `LLM-*` 已纳入结果/记录列表；结果页有真实 loss 曲线（多点连线）和采样对比 |
| 后端测试 | 26 项全通过（原有 21 项 + 新增 5 项 llm_service mock 单测） |
| 真实云端 SFT 训练 | **已执行**：`JOB-f3e6663d89aa507d3695` → `LLM-20261122T080332`，3 样本 × 5 轮，loss `5.413231 → 1.380106`，训练前后采样对比已生成 |
| API key 配置 | 已按用户选择保存到根目录 `.env`（已在 `.gitignore` 中），后端启动时自动加载；小程序表单可留空 |
| 依赖版本 | `pydantic` 已从 2.14.4 降到 2.12.5（pytrio 要求 ≥2.12.5）；21+5 项测试与 pytrio 导入均通过 |

## 三、怎么开始训练

### 1. 准备 API key
1. 打开 https://pytrio.com 用邮箱注册（免费）。
2. 进入控制台 https://pytrio.com/dashboard 复制 API Key。

### 2. 填 key（当前已配好）
- 你提供的 key 已按选择保存到项目根目录 `.env`（该文件已在 `.gitignore` 中，不会入库）。后端启动时自动读取，小程序表单留空即可训练。
- 也可以在「大模型训练」页的 API Key 输入框直接粘贴临时 key 覆盖；点「检测 SDK 与密钥」，状态变绿"SDK 与密钥就绪"即可。
- 若换机器或删除 `.env`，可在启动后端的终端设置 `PYTRIO_API_KEY=<你的key>`。

**密钥安全**：key 只在 worker 进程内传给 SDK，不写入工作区结果、不记录到 job 日志、不回显到结果 JSON；`.env` 仅本机可见且已忽略版本控制。

### 3. 配置训练参数
- 基座模型：实测 PyTRIO 当前支持 `Qwen/Qwen3.5-4B`（默认）与 `Qwen/Qwen3.8-27B`；前端选项已同步。平台列表会变动，以实时返回为准。
- 训练轮数：默认 5，范围 1–100。
- 学习率：默认 0.0001，范围 0.000001–0.1。
- LoRA 秩：默认 8，范围 4–64（SDK 硬上限 64，已同步前后端）。
- 生成长度：默认 40，范围 1–512。
- 训练后采样对比：默认开启，会用同一 prompt 分别采样 base 与 SFT 模型。

当前内置 3 条 SFT 示例（教模型回答"trio 是 AI Infra 产品"），来自 PyTRIO 官方快速开始示例。这是**教学演示数据**，不是你的工业数据。

### 4. 点「开始大模型训练」
任务进入正常 job 队列：单任务互斥、可取消、进度实时可见（连接引擎 → 创建客户端 → 每轮 loss → 保存权重 → 采样对比 → 完成）。结果落盘到 `workspace/llm_runs/LLM-*/result.json`，包含 loss 曲线、训练前后生成文本、权重路径。

**已实测**：`JOB-f3e6663d89aa507d3695` → `LLM-20261122T080332`，5 轮 loss 依次为 `5.413231 / 4.315636 / 3.218962 / 2.265823 / 1.380106`，SFT 采样已明显收敛到目标句式。另用 `.env` 自动读取 key 跑过 1 轮验证任务 `LLM-20261122T080937`，确认表单留空也能训练。切到「大模型训练」胶囊时会自动检测并显示绿色"SDK 与密钥就绪"；训练完成后点结果可查看 LLM 专属的 loss 曲线与采样对比，不再误调实验记录接口。

## 四、成本与限制

- **训练消耗 PyTRIO 账户配额**（共享集群按 Token 计费，专属 Training API 按 GPU 时计费）。本演示 3 条样本 × 5 轮，消耗很小；27B 模型和更多轮数成本会上升。控制台可查余额与用量。
- tokenizer 首次加载会从 ModelScope / HuggingFace 下载，需要网络；较慢属正常。
- 只做 LoRA 微调，不支持全量微调；这是 PyTRIO 平台的约束。
- 模型支持列表会变动。若填的模型不在列表，后端会报错并列出当前可用模型。

## 五、改动的文件

| 文件 | 改动 |
|---|---|
| `backend/engine/src/rac_hpo/application/llm_service.py` | **新增**：PyTRIO SFT 服务（参数校验、密钥处理、训练循环、采样对比、结果落盘）；修复 key 注入时机与 `TensorData.data` loss 计算；新增 `list_runs()` 把 LLM 结果暴露到列表 |
| `backend/actions.py` | 注册 `llm_status` / `llm_train` 两个 action；LoRA 秩上限对齐 SDK 的 64 |
| `backend/server.py` | 启动加载 `.env`；`/api/llm/status` 同步检测；`LLM-*` 详情直读结果文件；`/api/runs` 纳入 LLM 记录并阻止混合比较；本地实验与 LLM 训练双队列并行 |
| `.env` | 新增本机 PyTRIO key；已在 `.gitignore` 中 |
| `miniprogram/pages/workspace/config.js` | 新增第六个任务、LLM 模型列表（实测为 3.5-4B / 3.8-27B）、示例数据、配置项 |
| `miniprogram/pages/workspace/index.js` | 新增 key 同步检测、LLM 参数组装、提交逻辑；秩范围 4–64；切任务自动检测、LLM 结果专属视图、并行任务轮询 |
| `miniprogram/pages/workspace/view.js` | 新增 `llmSections`：训练配置、loss 曲线、训练前后采样对比、产物路径 |
| `miniprogram/pages/workspace/charts.js` | 新增 `drawLoss`，在结果页画真实 loss 曲线 |
| `miniprogram/pages/workspace/index.wxml` | 新增「大模型训练」表单卡片与 LLM loss 画布 |
| `miniprogram/pages/workspace/index.wxss` | 新增状态条 ok/warn 样式 |
| `backend/tests/test_llm_service.py` | **新增**：5 项 mock 单测（密钥检测、无 key 拒绝、参数校验、加权 loss、结果列表） |

## 六、一个需要留意的风险

pytrio 0.2.9 要求 `pydantic>=2.12.5`，而项目原本固定 `2.12.3`，两者无法在同一个环境里同时精确满足。当前折中锁到 **2.12.5**（pytrio 的最低要求）：pytrio 可导入、真实训练已跑通、26 项测试全通过；pip 会提示与原项目的 `==2.12.3` 约束不一致，这是预期现象。

如果以后必须严格回到 2.12.3，唯一干净的做法是把 PyTRIO 训练 worker 放进独立 venv，只让那个环境用 2.12.5+；不要在同一环境反复升降级。
