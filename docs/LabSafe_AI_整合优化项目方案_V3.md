# LabSafe AI｜智能实验室管理与安全协同平台

> **工程优化与 GitHub 解析版 V3｜2026-10-15**  
> 基于 `LabSafe_AI_整合优化项目方案_V2.md`、当前工作区实况，以及 GitHub 仓库 README、源码、许可证和提交记录核验结果整理。

## 0. 先给结论

LabSafe AI 的首版不应继续扩张为“大而全的实验室平台”，而应收敛为一个能离线重复演示、能被测试、能说明安全边界的原生微信小程序：

1. 用一条真实可走通的业务主线证明价值：`设备 → 巡检 → 异常 → 分派 → 处理 → 复核 → 关闭`。
2. 用一条可核查的知识主线证明“AI 可信”：`查询 → 命中资料片段 → 展示版本与来源 → 证据不足时拒答`。
3. 首版中的“学生、教师、管理员”只是同一设备上的演示身份，不宣称真实认证、跨设备协作或不可篡改审计。
4. 第一阶段不调用大模型，不接后端，不做实时视觉识别；AI 相关写入只能生成待确认草稿，业务模块校验后才能保存。
5. GitHub 工程只借鉴经过源码与许可核验的模块设计，不整仓拼接，不把 Web/Python 后端直接塞进微信小程序。

这比 V2 更重要的一项调整是：**先定义业务不变量和模块接口，再排页面开发顺序。** 页面只是调用模块的入口，状态流、引用、备份和统计不能散落在页面事件中。

---

## 1. 本次核验边界

### 1.1 已确认

- 已读取桌面上的 `LabSafe_AI_整合优化项目方案_V2.md`。
- 当前工作区 `F:\A_LIU_Astrspire\A_competition\Labsafety` 在核验时为空目录，且不是 Git 仓库。
- 因此本次没有可供静态分析、构建或运行验证的 LabSafe 源码；“优化项目”在本文件中指**优化产品范围、数据模型、工程架构、测试方案和开源复用决策**。
- 已通过 GitHub 仓库元数据、README、关键源码、许可证与最近提交记录核验参考工程。

### 1.2 尚未确认

- 没有实际小程序页面、`app.json`、`project.config.json`、存储代码或测试可验证。
- 没有真实实验室台账、规程、巡检表和异常样本。
- 没有真机存储容量、图片持久化、导入导出和性能测试结果。
- 没有后端、可信账号、模型调用通道、摄像头或传感器接入。

所有“建议实现”均不等于“已经实现”。后续拿到源码后，应先做一次只读工程审计，再决定具体文件修改。

---

## 2. V2 的主要问题与 V3 的修正

| V2 中的问题 | 风险 | V3 修正 |
|---|---|---|
| 先列页面和服务名，缺少业务不变量 | 状态校验容易分散在页面中，出现绕过流程的写入 | 增加 `WorkflowEngine`，所有巡检和异常状态变更只能通过一个接口完成 |
| 单机演示角色与“分派/权限/审计”混在一起 | 容易被误讲成真实多用户系统 | UI 明示“演示身份”；日志定义为本机操作记录，不称可信审计 |
| `StorageAdapter`、`SearchService`、`AIProvider` 一次性全部抽象 | 只有一个实现时会产生浅模块和无意义转发 | 只为真实变化点建立 seam；本地存储至少有生产与内存测试两个 adapter，`AIProvider` 推迟到阶段 B |
| 实体缺少文档版本、签收、状态事件和备份清单 | 引用和流程难以真正复核 | 增加 `DocumentVersion`、`DocumentChunk`、`DocumentAcknowledgement`、`WorkflowEvent`、`BackupManifest` |
| “引用校验”停留在文案 | 模型可能给任意文本补一个看似正确的引用 | 引用对象必须来自检索结果；回答句与证据片段建立映射，不允许事后强行补 `[Source 1]` |
| GitHub 项目只列名称，未核查源码与许可 | 可能误用无源码或许可不明确的内容 | 增加分级：可复用代码 / 仅借鉴设计 / 仅作需求参考 / 不采用 |
| 八周计划只有时间，没有退出门槛 | 页面做完也可能没有可靠闭环 | 每阶段增加 Gate；未通过数据恢复、状态流和引用测试，不进入下一阶段 |
| 图片、附件和备份策略不具体 | 本地记录可能丢失，导出文件可能不可恢复 | 结构化数据与附件分离；备份含 schema 版本、清单、校验值和导入预检 |

---

## 3. 产品定义与范围

### 3.1 一句话定位

LabSafe AI 是一个面向高校实验室的**本地优先安全管理小程序**，把设备台账、巡检、异常整改、规程查阅与证据引用连接为可追踪闭环；AI 只负责降低查询与填报成本，不替代负责人、安全规程、设备联锁或应急判断。

### 3.2 首版的唯一主故事

学生发现设备异常，查阅该设备当前有效规程，提交带证据的异常记录；管理员在同一演示设备上切换身份，确认、分派、处理并提交复核；教师或管理员复核关闭；仪表盘只根据这些已保存记录计算数据。

### 3.3 功能分层

| 层级 | 必须交付 | 明确不做 |
|---|---|---|
| P0 | 设备台账、巡检模板与任务、异常闭环、版本化规程、精确/关键词检索、引用卡片、本地统计、备份恢复、演示身份 | 后端、云函数、真实账号、跨端同步、大模型、实时视频识别 |
| P1 | 规程签收记录、二维码定位设备、规则式自然语言草稿、CSV 导出、失败案例看板 | 自动审批、模型直接写入、客户端 API Key |
| P2 | 安全后端、正式认证授权、跨端协作、真实 RAG、模型评测、消息通知 | 无人监督的安全决策 |
| 研究项 | PPE/烟火/危险行为识别、传感器或 PLC 接入 | 未经数据集、设备和现场验证就对外宣称准确率或实时安全能力 |

P0 结束前，不增加耗材采购、课程、考试、复杂预约、财务、样品检测全流程等旁支模块。

---

## 4. 两条可验收业务闭环

### 4.1 管理闭环

```text
设备建档
  → 创建巡检任务
  → 执行并记录检查项
  → 发现异常并生成 Incident
  → 确认与分派
  → 处理并提交证据
  → 独立复核
  → 关闭归档
```

必须满足：

- 每次状态变化都保存操作者演示身份、时间、原因、前后状态和关联记录。
- 处理人不能通过修改页面数据直接把异常改成“已关闭”。
- 关闭前必须存在处理说明；需要复核的异常还必须存在复核结论。
- 退回必须填写原因，且保留原处理记录。
- 删除设备不能级联抹掉历史事件；有历史记录时只允许停用。

### 4.2 知识闭环

```text
登记资料
  → 创建不可变版本
  → 切分为可定位段落
  → 建立本地索引
  → 查询与排序
  → 返回 Citation 对象
  → 展示原文、版本和位置
  → 证据不足时拒答
```

必须满足：

- `Citation` 只能引用真实存在的 `DocumentChunk`。
- 一个文档发布新版本后，旧版本及其历史引用仍能复核。
- 默认只检索“当前有效且已授权”的版本；历史版本需要显式筛选。
- 未命中时返回固定拒答，不从常识补充高风险操作建议。
- 页面显示的标题、版本、章节和摘录从数据对象读取，不由模型自由生成。

---

## 5. 工程架构：少接口、深模块、强不变量

### 5.1 总体结构

```text
pages / components
       │  只提交用户意图并显示结果
       ▼
┌─────────────────────────────────────────────┐
│  AssetRegistry   WorkflowEngine             │
│  KnowledgeEngine AssistantDraftEngine       │
│  BackupEngine    DashboardProjection        │
└─────────────────────────────────────────────┘
       │  统一校验、状态流、引用和事件记录
       ▼
┌─────────────────────────────────────────────┐
│ Repository interface                        │
│ LocalRepository adapter / MemoryRepository  │
└─────────────────────────────────────────────┘
       │
       ▼
wx 本地结构化存储 + 小程序本地文件系统
```

### 5.2 深模块及其最小接口

#### `AssetRegistry`

接口建议：

```js
saveLab(input)
saveDevice(input)
retireDevice({ deviceId, reason, actor })
getDeviceProfile(deviceId)
```

模块内部处理编号唯一性、必填字段、停用规则、关联规程与维护/校准摘要。页面不直接拼装存储键。

#### `WorkflowEngine`

接口建议：

```js
createInspection(input)
submitInspection(input)
reportIncident(input)
transitionIncident({ incidentId, command, payload, actor })
getIncidentTimeline(incidentId)
```

`command` 使用业务命令，如 `CONFIRM`、`ASSIGN`、`START`、`SUBMIT_REVIEW`、`APPROVE`、`RETURN`，不允许页面直接传任意目标状态。模块内部计算合法目标状态、校验前置条件、保存业务记录并追加 `WorkflowEvent`。

#### `KnowledgeEngine`

接口建议：

```js
publishDocument(input)
rebuildIndex()
query({ text, filters, topK })
openCitation(citationId)
```

模块内部隐藏分词、同义词、字段权重、精确编号匹配、版本过滤和拒答阈值。调用者只学习一个查询接口。

#### `AssistantDraftEngine`

接口建议：

```js
propose({ text, scene, actor })
confirm({ proposalId, editedFields, actor })
```

P0 的实现是本地规则解析。`propose` 只返回可编辑草稿与缺失字段，不写业务数据；`confirm` 也不能直接写存储，而是调用 `WorkflowEngine` 或 `AssetRegistry` 再次校验。

#### `BackupEngine`

接口建议：

```js
exportBundle()
inspectBundle(file)
restoreBundle(file, mode)
```

`inspectBundle` 必须在写入前完成 schema、清单、ID 冲突、附件缺失和校验值检查；`restoreBundle` 支持“全量替换”和“安全合并”，并先生成可恢复快照。

#### `DashboardProjection`

接口建议：

```js
buildDashboard({ from, to, labId })
```

所有数字从已保存实体与事件计算，演示数据必须带 `isDemo: true`；禁止在页面写固定 KPI。

### 5.3 seam 与 adapter 决策

| 依赖 | 分类 | 决策 |
|---|---|---|
| 状态流、统计、检索评分 | 进程内 | 直接作为深模块实现，不再套一层空接口 |
| 小程序本地存储 | 本地可替代 | `LocalRepository` 用于运行，`MemoryRepository` 用于自动测试；这是一个真实 seam |
| 本地文件系统 | 本地可替代 | 作为 `BackupEngine` 的内部 seam，测试用内存文件 adapter，不暴露到页面 |
| 未来自建后端 | 远程但自有 | 真正接入时定义 port，生产用 HTTP adapter，测试用 in-memory adapter |
| 未来模型服务 | 真外部依赖 | 阶段 B 再定义 `ModelProvider`；生产 adapter + mock adapter，禁止 P0 先造空壳 |

### 5.4 建议目录

```text
miniprogram/
├─ app.js
├─ app.json
├─ pages/
│  ├─ dashboard/
│  ├─ devices/
│  ├─ inspections/
│  ├─ incidents/
│  ├─ knowledge/
│  ├─ assistant/
│  └─ settings/
├─ components/
│  ├─ citation-card/
│  ├─ status-chip/
│  ├─ timeline/
│  └─ empty-state/
├─ domain/
│  ├─ assets/AssetRegistry.js
│  ├─ workflow/WorkflowEngine.js
│  ├─ knowledge/KnowledgeEngine.js
│  ├─ assistant/AssistantDraftEngine.js
│  ├─ backup/BackupEngine.js
│  └─ dashboard/DashboardProjection.js
├─ infrastructure/
│  ├─ repository/LocalRepository.js
│  ├─ repository/MemoryRepository.js
│  ├─ files/LocalFileAdapter.js
│  └─ clock/SystemClock.js
├─ data/
│  ├─ demo/
│  ├─ documents/
│  └─ migrations/
├─ tests/
│  ├─ workflow/
│  ├─ knowledge/
│  ├─ backup/
│  └─ fixtures/
└─ utils/
```

目录是目标结构，不是在没有源码时要求一次性重构。拿到现有项目后，应先映射现状，再做最小迁移。

---

## 6. 数据模型与不变量

### 6.1 核心实体

| 实体 | 关键字段 | 关键不变量 |
|---|---|---|
| `Lab` | `id`, `code`, `name`, `location`, `managerName`, `status` | `code` 唯一；停用后不可新建任务 |
| `Device` | `id`, `code`, `labId`, `name`, `model`, `criticality`, `status`, `ownerName` | 有历史记录时只能停用，不能物理删除 |
| `InspectionTemplate` | `id`, `name`, `version`, `items[]`, `applicableDeviceTypes` | 已被任务使用的版本不可原地改写 |
| `InspectionTask` | `id`, `templateVersionId`, `deviceId`, `dueAt`, `status`, `answers[]` | 提交后保留快照，模板更新不影响历史任务 |
| `Incident` | `id`, `code`, `deviceId`, `severity`, `status`, `description`, `assignee`, `resolution` | 状态只能经 `WorkflowEngine` 迁移 |
| `WorkflowEvent` | `id`, `entityType`, `entityId`, `command`, `from`, `to`, `reason`, `actor`, `at`, `payloadHash` | 只追加，不在普通编辑流程中覆盖 |
| `Document` | `id`, `title`, `category`, `owner`, `authorizationNote` | 只承载文档身份，不承载可变正文 |
| `DocumentVersion` | `id`, `documentId`, `version`, `effectiveAt`, `status`, `contentHash` | 发布后正文不可变；废止不删除 |
| `DocumentChunk` | `id`, `documentVersionId`, `section`, `ordinal`, `text` | `Citation` 必须能反查到真实 chunk |
| `Citation` | `id`, `chunkId`, `queryId`, `score`, `snippet` | `snippet` 必须来自 chunk，不接受自由文本 |
| `DocumentAcknowledgement` | `id`, `documentVersionId`, `actor`, `at` | 签收绑定具体版本；新版本需重新签收 |
| `BackupManifest` | `schemaVersion`, `exportedAt`, `appVersion`, `recordCounts`, `fileChecksums` | 恢复前必须通过预检 |

### 6.2 通用元数据

所有业务记录至少包含：

```json
{
  "id": "inc_01J...",
  "schemaVersion": 3,
  "createdAt": "2026-10-15T12:30:00+08:00",
  "updatedAt": "2026-10-15T12:45:00+08:00",
  "createdBy": { "demoRole": "student", "displayName": "演示学生" },
  "isDemo": true,
  "revision": 1
}
```

`revision` 用于防止页面拿着旧数据覆盖新变化。即使首版只有单设备，也应在写入前比较 `expectedRevision`。

### 6.3 附件策略

- 结构化实体只保存附件元数据和持久文件路径，不在 JSON 中保存大段 Base64。
- 用户选择的临时图片要复制到可持续保存的位置，再写入记录。
- 附件记录保存 `id`、原文件名、MIME、大小、内容校验值、关联实体和创建时间。
- 删除业务记录默认不立即删除附件；由“孤立附件清理”在备份确认后处理。
- 导出包必须包含附件清单；恢复时先检查缺失和校验失败，再决定是否写入。

---

## 7. 状态机设计

### 7.1 异常状态

```text
DRAFT
  └─SUBMIT→ REPORTED
REPORTED
  ├─CONFIRM→ CONFIRMED
  └─REJECT→ REJECTED
CONFIRMED
  └─ASSIGN→ ASSIGNED
ASSIGNED
  └─START→ IN_PROGRESS
IN_PROGRESS
  └─SUBMIT_REVIEW→ PENDING_REVIEW
PENDING_REVIEW
  ├─APPROVE→ CLOSED
  └─RETURN→ IN_PROGRESS
```

关键 guard：

| 命令 | 必须满足 | 失败行为 |
|---|---|---|
| `SUBMIT` | 设备存在、描述非空、严重度合法 | 返回字段错误，不创建事件 |
| `ASSIGN` | 已确认、处理人非空、设备未停用 | 保持原状态 |
| `SUBMIT_REVIEW` | 处理说明非空；要求证据时附件存在 | 提示缺失项 |
| `APPROVE` | 当前为待复核；复核结论非空 | 禁止跳过复核 |
| `RETURN` | 退回原因非空 | 记录原因并回到处理中 |

### 7.2 巡检状态

`PLANNED → IN_PROGRESS → SUBMITTED → VERIFIED`，另有 `CANCELLED`。过期是派生属性 `isOverdue`，不是需要人工维护的独立状态，避免“状态”和“日期”冲突。

### 7.3 为什么要保存事件

当前实体保存最新状态，`WorkflowEvent` 保存变化过程。这样既能快速显示列表，又能重建时间线、计算处理时长和验证非法跳转。它仍是本机可修改数据，所以只能称“操作历史”或“演示追踪”，不能称“不可篡改审计”。

---

## 8. 本地知识检索与可信引用

### 8.1 P0 检索策略

P0 不需要向量数据库。对几十到几百个结构化段落，可采用可解释的混合评分：

```text
score = 6 × exactCode
      + 4 × titleHit
      + 3 × keywordCoverage
      + 2 × phraseHit
      + 1 × tagHit
      - 5 × obsoleteVersion
```

处理顺序：

1. 标准化空格、大小写、全半角和常见标点。
2. 对设备编号、报警码、SOP 编号先走精确匹配。
3. 对中文查询使用词典 + 二元切分，保留领域短语。
4. 按资料状态、实验室、设备类别和版本先过滤，再评分。
5. 返回 Top-k `Citation` 对象；最高分不足阈值时返回拒答。

### 8.2 回答展示

本地阶段不生成长答案，只展示：

- 命中的原文摘录；
- 文档标题、版本、章节、更新时间；
- 与设备或任务的关联；
- “查看原文”入口；
- 证据不足提示。

固定拒答：

> 当前已授权资料中未找到足够依据。请核对原始规程或联系实验室负责人；不要仅依据本工具执行高风险操作。

### 8.3 阶段 B 的 RAG 安全门

未来接模型时，必须执行：

1. 先检索，后生成；高风险问题禁止“通用知识模式”。
2. 模型只看到必要片段，不获得数据库凭据，也不能执行任意查询。
3. 返回结构化 `claims[]`，每条 claim 必须引用允许列表中的 `chunkId[]`。
4. 程序校验 chunk 存在、版本有效、片段确实来自已检索集合。
5. 不允许仅因为“有检索结果”就给答案自动补一个引用。
6. 任何写操作先生成有时效的 proposal，用户确认时重新校验记录 revision 和权限。

---

## 9. GitHub 工程核验与复用决策

> 核验时间：2026-10-15。提交活跃度只表示仓库有更新，不代表代码质量、合规性或现场适用性已通过验证。

本次关键源码结论固定在以下 `main` 分支提交快照，避免仓库后续变化导致描述失真：

| 仓库 | 核验 commit |
|---|---|
| `Together1022/Laboratory-Comprehensive-Management-System` | `74f82d10402942979dc3535d936e9ea7b1fd2334` |
| `LXIANCodes/LIMS_system` | `e9f85726d2c38f48323ff7f99aad610f9d532b0d` |
| `Mokey2002/OpenLIMS` | `2f07b5a5fece4e0d641a73a7ec3d82f21f3874c7` |
| `mbmera/LEMS` | `896e5f2c2bdead0bf0e68df8041b6da1f83f0752` |
| `gb119/labman` | `1e558afbeb0b3bd142f1f6c7ab9720dc89941592` |
| `s26-redi-ml-ai/provenance-energy-rag-chatbot` | `9b171bd546532075f0180f775ad0df7d167c35f7` |

### 9.1 总表

| 工程 | 核验结果 | 可借鉴内容 | 不应直接使用的原因 | 决策 |
|---|---|---|---|---|
| [Together1022/Laboratory-Comprehensive-Management-System](https://github.com/Together1022/Laboratory-Comprehensive-Management-System) | 公共仓库，但目前主要是 README、截图和商业版说明；未找到标准 `LICENSE` 文件；最近提交也是 README 更新 | 风险分级、隐患整改、设备全生命周期等需求词汇 | 无可审查源码、许可边界不清、商业授权说明限制明显 | **仅作需求参考，不复制代码或素材** |
| [LXIANCodes/LIMS_system](https://github.com/LXIANCodes/LIMS_system) | README 描述 FastAPI + Vue + MySQL；仓库主体是 `Lims-本地.zip`；README 声称 MIT，但链接的 `LICENSE` 文件不存在 | 中文实体命名、任务/设备/报告的业务分区 | 源码未以正常树展示，难以逐文件审计；许可文件缺失；技术栈与 P0 不符 | **仅作业务结构参考，暂不复用代码** |
| [Mokey2002/OpenLIMS](https://github.com/Mokey2002/OpenLIMS) | 完整源码，Apache-2.0；截至核验日提交活跃；README 明示仍是 production-style prototype | 状态策略、变更原因、revision 冲突、AI 写入预览/确认/过期/重检、审计事件 | Django/React/数据库体系远超本地小程序；不能整体移植 | **最高优先级设计参考，可按许可摘取少量通用思想或代码** |
| [mbmera/LEMS](https://github.com/mbmera/LEMS) | 完整 Python/Streamlit/SQLite 工程，MIT；设备、校准、提醒、活动记录与导出较集中；仓库规模较小 | 到期阈值、状态集中计算、设备责任人与证书关联 | 不是小程序；项目较新且提交数少，不能把 README 当成成熟度证明 | **设备生命周期与提醒规则参考** |
| [gb119/labman](https://github.com/gb119/labman) | 完整 Django 工程，MIT；README 与源码均包含安全文档、风险评估、文档版本签收 | `DocumentSignOff` 绑定具体版本、设备准入前检查签收、位置层级 | 正式账号、PostgreSQL 与预约模块超出 P0 | **文档版本和签收模型参考** |
| [s26-redi-ml-ai/provenance-energy-rag-chatbot](https://github.com/s26-redi-ml-ai/provenance-energy-rag-chatbot) | 完整 FastAPI/Streamlit RAG 工程，MIT；具备来源卡片、拒答、精确故障码查找和测试 | `documentId/page/section/chunkId/score/snippet` 来源对象、精确编号优先、无依据拒答 | 当前 `validate_citations` 会在有来源但回答无标记时自动补 `[Source 1]`，这不能证明句子真的受该来源支持 | **复用来源数据结构与测试思路，不照搬引用补全逻辑** |

### 9.2 最值得落地的五个模式

#### 模式 A：AI 写入必须“预览 → 确认 → 再校验”

[OpenLIMS 的 confirmed actions 文档](https://github.com/Mokey2002/OpenLIMS/blob/main/docs/assistant-guided-actions.md)明确：预览不会改记录；确认前重新检查权限、记录、schema、重复键和时效。LabSafe 应把本地规则助手也放在同一约束下，而不是因为“不是大模型”就允许直接写。

#### 模式 B：状态策略带 revision 与变更原因

[OpenLIMS 的 `status_policy.py`](https://github.com/Mokey2002/OpenLIMS/blob/main/backend/settings_app/status_policy.py)使用 `expected_revision`、变更原因和冲突响应，且限制管理员只能在受支持的生命周期内收紧策略。LabSafe P0 可缩小实现，但应保留 `expectedRevision` 和事件中的 `reason`。

#### 模式 C：安全文档签收绑定具体版本

[LabMan 的设备模型](https://github.com/gb119/labman/blob/main/apps/equipment/models.py)将签收记录绑定到特定文档版本。LabSafe 不应只记录“用户读过某文档”，而应记录“演示用户在何时读过 v2.1”；新版本发布后原签收不再代表当前版本已读。

#### 模式 D：设备状态集中计算

[LEMS](https://github.com/mbmera/LEMS)把校准到期、提醒阈值和仪表盘状态集中管理。LabSafe 应把“正常 / 临期 / 逾期 / 停用”作为统一派生规则，页面只显示结果，不在多个页面分别判断日期。

#### 模式 E：来源卡片来自检索对象，不来自生成文本

[Provenance RAG 的 `provenance.py`](https://github.com/s26-redi-ml-ai/provenance-energy-rag-chatbot/blob/main/backend/app/rag/provenance.py)从已检索 chunk 构建结构化来源卡片，字段设计值得复用；但其 [`verifier.py`](https://github.com/s26-redi-ml-ai/provenance-energy-rag-chatbot/blob/main/backend/app/rag/verifier.py)的自动补引用只能视为 UI 完整性处理，不能作为事实支持校验。LabSafe 应采用 claim-to-chunk 映射。

### 9.3 开源使用规则

- 复用前固定仓库 URL、commit SHA、文件路径和许可证版本。
- 保存 `THIRD_PARTY_NOTICES.md`，记录复制或改写的文件、原作者、许可与本项目修改。
- MIT 内容保留版权与许可声明；Apache-2.0 内容同时检查 NOTICE、修改声明和专利条款。
- 无 `LICENSE` 不等于可自由使用；只能阅读思想和事实，不复制代码、图片、文案或数据库结构。
- 竞赛答辩要区分“参考开源设计”“实际复用代码”“本项目原创实现”，不得把上游能力当成本项目实测结果。

### 9.4 未来视觉安全扩展：已调研，但不进入 P0

如果后续明确要做摄像头 PPE、烟火或危险行为识别，建议保持它与小程序管理主线解耦：

```text
摄像头/录像
  → MediaMTX（视频接入与转发）
  → ONNX Runtime 或硬件专用推理后端
  → supervision（检测结果、跟踪、区域规则）
  → LabSafe 自有事件聚合模块
  → 标准化 Incident 草稿
  → 人工确认后进入 WorkflowEngine
```

候选一手工程：

- [bluenviron/mediamtx](https://github.com/bluenviron/mediamtx)：MIT，适合视频协议接入、转发和录像，不负责 AI 语义。
- [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime)：MIT，适合 Windows/CPU 与多种硬件执行后端的统一 ONNX 推理。
- [roboflow/supervision](https://github.com/roboflow/supervision)：MIT，适合跟踪、区域、越线和可视化后处理；业务告警规则仍应由 LabSafe 自己实现。
- [cvat-ai/cvat](https://github.com/cvat-ai/cvat)：MIT，适合建立本地实验室图片/视频标注与复核闭环。
- [gaia-solutions-on-demand/DFireDataset](https://github.com/gaia-solutions-on-demand/DFireDataset)：可作为烟火冷启动数据，但不能替代实验室蒸汽、粉尘、酒精灯、反光等本地易混淆样本。
- [open-mmlab/mmaction2](https://github.com/open-mmlab/mmaction2)：Apache-2.0，可用于后续时序动作研究；首版危险行为更适合用区域、姿态、持续时间等可解释规则。
- [open-edge-platform/dlstreamer](https://github.com/open-edge-platform/dlstreamer) 与 [NVIDIA/DeepStream](https://github.com/NVIDIA/DeepStream)：分别用于 Intel 和 NVIDIA 硬件专项优化，应二选一，不应同时成为首版依赖。
- [ultralytics/ultralytics](https://github.com/ultralytics/ultralytics)：训练与导出能力成熟，但开源版为 AGPL-3.0，并提供商业许可。闭源或网络服务场景必须先做许可决策，模型权重和数据集还要分别核验许可。

视觉扩展的启动门槛：已经确定摄像头与目标硬件；具备本地授权数据和隐私方案；定义固定录像回放集；能报告 Precision、Recall、每小时误报数和端到端时延；视觉结果只生成候选事件，不直接触发最高级别处置或设备联锁。

---

## 10. 实施路线：以 Gate 控制，而不是只看周次

### Gate 0｜拿到源码并完成只读审计

产物：

- 实际目录、页面、组件、数据键和依赖清单；
- 当前主流程与可复用模块；
- 未提交修改、构建方式与开发者工具实际打开目录；
- 与本 V3 的差距表。

退出条件：能明确指出每个 P0 能力将落在哪些现有文件；尚未改代码。

### Gate 1｜数据底座可恢复

实现：`Repository`、schema 版本、迁移、演示数据、备份清单、导入预检。

退出条件：

- 重启后数据仍在；
- 导出后清空，再恢复得到相同记录数与引用关系；
- 损坏文件、旧 schema、重复 ID 和缺失附件均能安全拒绝或生成报告；
- 自动测试使用 `MemoryRepository`，不依赖微信 UI。

### Gate 2｜设备、巡检与异常闭环

实现：`AssetRegistry`、`WorkflowEngine`、设备/巡检/异常页面、时间线。

退出条件：正常、退回、重复点击、旧 revision、停用设备、缺失处理证据等路径全部有测试；页面无法绕过命令直接设状态。

### Gate 3｜版本化资料与检索

实现：`KnowledgeEngine`、文档版本、chunk、索引、引用卡片、拒答。

退出条件：

- 50–100 条固定问题有人工标注的目标段落；
- 能输出 Top-k 命中、无答案判断和失败案例；
- 旧版本引用仍可打开；
- 任何引用 ID 都能反查原文。

### Gate 4｜规则助手与统计展示

实现：`AssistantDraftEngine`、确认卡片、仪表盘投影、CSV 导出。

退出条件：助手无法直接写记录；修改草稿后仍会业务校验；统计能从测试数据重算且无固定数字。

### Gate 5｜真机、演示与答辩证据

实现：真机附件测试、离线演示包、录屏脚本、测试报告、第三方声明。

退出条件：同一套固定演示数据可连续重复演示三次；每项宣传能力都有界面、记录或测试报告支撑。

---

## 11. 测试与评测清单

### 11.1 自动测试

| 编号 | 测试 | 期望 |
|---|---|---|
| WF-01 | 从 `REPORTED` 直接 `APPROVE` | 拒绝，无事件写入 |
| WF-02 | `PENDING_REVIEW` 退回不写原因 | 拒绝，状态不变 |
| WF-03 | 同一 command 重复提交 | 幂等或明确拒绝，不生成重复关闭事件 |
| WF-04 | 使用旧 `expectedRevision` 写入 | 返回冲突，提示刷新 |
| ST-01 | v1 备份导入 v3 | 先迁移并报告变化，失败不污染当前数据 |
| ST-02 | 附件校验值不匹配 | 预检失败，不恢复 |
| KN-01 | 查询精确设备编号 | 精确匹配排在语义相似结果之前 |
| KN-02 | 问题无授权资料依据 | 固定拒答，`citations=[]` |
| KN-03 | 发布文档新版本 | 默认命中新版，历史引用仍打开旧版 |
| KN-04 | Citation 指向不存在 chunk | 保存失败 |
| AI-01 | 草稿缺设备 | 返回缺失字段，不写 Incident |
| AI-02 | 草稿确认前记录已变化 | 重新校验失败，要求刷新 |
| BK-01 | 仪表盘统计 | 与实体和事件重算值一致 |

### 11.2 人工真机测试

- 微信开发者工具编译、预览和真机运行分别记录，不互相替代。
- 飞行模式下完成设备查询、巡检、异常提交、资料搜索和备份导出。
- 选择图片后退出小程序并重新进入，验证附件仍可打开。
- 手机存储紧张、文件被移除、导入包被篡改时给出可理解错误。
- 演示身份切换必须醒目标注，避免观众误认为真正登录。

### 11.3 评测报告必须保留

- 测试集版本与标注人；
- 每次运行的应用版本、资料版本和参数；
- 成功数、失败数、失败样例与处理结论；
- 不使用“提升 XX%”等没有原始记录支撑的数字。

---

## 12. 竞赛创新点的可证据化表达

### 创新点 1：版本化安全知识证据链

不说“AI 能回答所有安全问题”，而说：系统把查询结果绑定到具体资料版本和段落，证据不足时拒答，并能用固定测试集检查引用是否真实。

展示证据：一条命中、一条旧版本引用、一条拒答、引用反查测试报告。

### 创新点 2：受约束的智能操作

不说“AI 自动管理实验室”，而说：自然语言只生成可审阅草稿；确认时重新进行状态、字段和 revision 校验，任何写入仍受业务规则控制。

展示证据：正常确认、缺字段、记录已变化、取消四条路径。

### 创新点 3：离线可恢复的安全闭环

不说“已经实现可信云平台”，而说：在无服务器条件下，核心闭环可离线运行，数据可导出、预检和恢复；为后续后端协作保留明确 seam。

展示证据：离线全流程、备份恢复、损坏导入包拒绝、未来架构图。

---

## 13. 风险登记

| 风险 | 概率/影响 | 当前控制 | 升级条件 |
|---|---|---|---|
| 本地数据被清理或换机丢失 | 中/高 | 明示单机边界、备份提醒、恢复预检 | 出现跨用户协作需求时必须上后端 |
| 演示角色被误解为真实权限 | 高/高 | 页头水印、答辩声明、日志称“本机记录” | 正式试点前完成认证授权 |
| 资料过期导致错误引用 | 中/高 | 版本状态、有效日期、负责人、旧版标记 | 资料库规模扩大后建立发布审核流程 |
| 规则检索召回不足 | 中/中 | 精确编号通道、同义词、固定测试集、拒答 | 达不到目标时再评估本地 embedding 或后端检索 |
| AI 草稿误写记录 | 低/高 | proposal、确认、revision、业务模块二次校验 | 任何自动执行需求需单独安全评审 |
| 开源代码许可不清 | 中/高 | commit 固定、许可证核验、第三方清单 | 无 LICENSE 或商业条款时禁止复制 |
| 视觉识别成为范围蔓延 | 高/高 | 列为研究项，不进入 P0 | 只有在数据、算力、设备和评测负责人齐备时立项 |

---

## 14. 后续拿到源码后的首轮任务单

1. 只读列出真实小程序根目录、所有页面路由、组件、npm 依赖和本地存储键。
2. 找出所有直接调用 `wx.setStorage*` 的位置，画出现有数据关系，不先重构。
3. 用一条现有记录贯穿“创建—读取—修改—删除/停用”，确认实际行为。
4. 检查图片是否仍指向临时路径，检查导出是否包含附件。
5. 检查页面是否直接修改 `status`，列出需要迁移到 `WorkflowEngine` 的调用点。
6. 选一个最小纵向切片：`设备列表 → 设备详情 → 上报异常 → 时间线`。
7. 先补测试与 `MemoryRepository`，再迁移该切片；通过后再扩展到巡检和资料。

首轮不得同时重做 UI、引入后端、加入大模型和改造全部存储。每次变更需要：涉及文件、数据迁移、测试步骤、真机边界和回退方案。

---

## 15. 建议写入项目的 Codex 约束

> 在修改 LabSafe AI 前，先只读核验真实微信小程序根目录、当前分支/工作区、`app.json`、数据存储键、开发者工具打开目录和未提交改动。P0 必须无服务器、无云函数、无客户端大模型密钥，并可在单设备离线运行。页面不得直接写业务状态；设备、巡检、异常的变更必须经过领域模块并追加事件。助手只能生成待确认 proposal，确认时重新校验字段、状态和 revision。引用必须来自真实 `DocumentChunk`，无法定位原文时拒答。未经明确批准，不修改范围外页面、配置、样式或既有业务；每次提交都列出文件、迁移、测试、验证边界和回退方法。

---

## 16. 来源

### 现有方案

- `C:\Users\Legion\Desktop\MD文件\LabSafe_AI_整合优化项目方案_V2.md`

### GitHub 一手来源

- [Together1022/Laboratory-Comprehensive-Management-System](https://github.com/Together1022/Laboratory-Comprehensive-Management-System)
- [LXIANCodes/LIMS_system](https://github.com/LXIANCodes/LIMS_system)
- [Mokey2002/OpenLIMS](https://github.com/Mokey2002/OpenLIMS)
- [OpenLIMS confirmed lab actions](https://github.com/Mokey2002/OpenLIMS/blob/main/docs/assistant-guided-actions.md)
- [OpenLIMS status policy](https://github.com/Mokey2002/OpenLIMS/blob/main/backend/settings_app/status_policy.py)
- [mbmera/LEMS](https://github.com/mbmera/LEMS)
- [gb119/labman](https://github.com/gb119/labman)
- [LabMan equipment models](https://github.com/gb119/labman/blob/main/apps/equipment/models.py)
- [s26-redi-ml-ai/provenance-energy-rag-chatbot](https://github.com/s26-redi-ml-ai/provenance-energy-rag-chatbot)
- [Provenance source-card builder](https://github.com/s26-redi-ml-ai/provenance-energy-rag-chatbot/blob/main/backend/app/rag/provenance.py)
- [Provenance citation verifier](https://github.com/s26-redi-ml-ai/provenance-energy-rag-chatbot/blob/main/backend/app/rag/verifier.py)
- [bluenviron/mediamtx](https://github.com/bluenviron/mediamtx)
- [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime)
- [roboflow/supervision](https://github.com/roboflow/supervision)
- [cvat-ai/cvat](https://github.com/cvat-ai/cvat)
- [gaia-solutions-on-demand/DFireDataset](https://github.com/gaia-solutions-on-demand/DFireDataset)
- [open-mmlab/mmaction2](https://github.com/open-mmlab/mmaction2)
- [open-edge-platform/dlstreamer](https://github.com/open-edge-platform/dlstreamer)
- [NVIDIA/DeepStream](https://github.com/NVIDIA/DeepStream)
- [ultralytics/ultralytics](https://github.com/ultralytics/ultralytics)

---

## 17. 最终验收口径

当且仅当下列事实都有可复查证据时，才能称 P0 完成：

- 同一设备离线完成设备、巡检、异常、处理、复核、关闭全流程；
- 每次状态变化都由合法命令触发并出现在时间线；
- 数据能导出、预检和恢复，损坏包不会覆盖现有数据；
- 查询结果能打开真实资料版本与段落，无依据时拒答；
- 助手只生成草稿，不能绕过业务规则直接写入；
- 统计来自真实保存记录，演示数据有明显标识；
- 自动测试、真机测试、失败案例、开源引用和能力边界均有记录。

V3 是可以指导实施的工程基线，不是已完成代码、真机验证或现场安全认证。
