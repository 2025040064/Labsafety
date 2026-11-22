# LabSafe AI：GitHub 开源工程调研与集成建议

> 调研快照：2026-10-15（Asia/Shanghai）  
> 范围：仅使用 GitHub 仓库、源码、Release 与项目官方文档等一手来源。维护状态中的日期取 GitHub 仓库 `pushed_at`；许可证以仓库 `LICENSE`、README 或官方许可说明为准。  
> 边界：本文是工程选型与复用分析，不代表这些项目已经集成进 LabSafe AI，也不把仓库 README 的功能声明当成 LabSafe AI 的实测结果。

## 1. 结论先行

LabSafe AI 不宜从某一个“实验室安全检测”演示仓库整体复制。更稳妥的做法是把成熟开源项目按职责拆开使用：

1. **模型训练与导出**：Ultralytics YOLO 可快速验证 PPE、烟火和通用目标检测，但其 AGPL-3.0 是闭源部署的主要合规风险；生产版应先确定开源策略、购买商业许可，或将训练结果迁移到许可兼容的推理链路。
2. **检测后处理与规则**：优先使用 MIT 许可的 `roboflow/supervision` 统一检测结果、跟踪、区域和越线规则；把“连续帧确认、冷却时间、人员与 PPE 关联”写成 LabSafe 自有规则层。
3. **视频接入与边缘部署**：普通跨平台/Windows 演示可用 MediaMTX + ONNX Runtime；Intel 设备选 DL Streamer；NVIDIA Jetson/RTX 选 DeepStream。三者应是可替换后端，而不是同时堆进首版。
4. **训练数据闭环**：CVAT 管理本地实验室图片、视频和复核标注；公开 PPE/烟火数据只能做预训练或冷启动，必须补充本实验室场景数据和负样本。
5. **安全知识库/RAG**：Haystack + Qdrant 可建立 SOP、化学品 SDS、设备操作规程和应急预案的可追溯问答，但 RAG 只能提供带引用的辅助建议，不能处于实时告警或联锁控制的关键路径。
6. **危险行为**：首版先用姿态、区域、速度和停留时间等可解释规则；MMAction2 放到二期，在具备本地标注视频和离线评测后再接入。

推荐的首版链路：

```text
摄像头/视频文件
  -> MediaMTX（RTSP 接入、转协议、录像）
  -> ONNX Runtime 或硬件专用推理后端
  -> supervision（跟踪、区域、人员-PPE 关联）
  -> LabSafe 事件规则服务（连续帧、冷却、去重、升级）
  -> MQTT/HTTP 事件总线
  -> 告警、证据截图/短视频、复核、统计看板

SOP/SDS/应急预案
  -> Haystack 索引与检索流程
  -> Qdrant（带文档版本、页码、适用实验室等元数据）
  -> 带来源引用的安全问答
```

## 2. 候选工程总表

| 项目 | 许可证 | 维护状态（截至快照日） | 最适合复用到 LabSafe 的部分 | 主要风险与处理建议 |
|---|---|---|---|---|
| [ultralytics/ultralytics](https://github.com/ultralytics/ultralytics) | AGPL-3.0；官方同时提供企业许可 | 未归档，2026-10-15 有推送；[官方仓库](https://github.com/ultralytics/ultralytics)持续维护 | YOLO 训练/验证、图片与视频推理、目标跟踪、ONNX/TensorRT/OpenVINO 导出；官方还提供 [Construction-PPE 数据集说明](https://github.com/ultralytics/ultralytics/blob/main/docs/en/datasets/detect/construction-ppe.md)，含安全帽、手套、反光衣、靴子、护目镜及部分“未佩戴”类别 | AGPL 可能影响网络服务和闭源交付；数据集、模型权重也可能有独立许可。建议只在完成许可审查后作为生产依赖；否则限于研究、训练和模型导出阶段 |
| [roboflow/supervision](https://github.com/roboflow/supervision) | MIT | 未归档，2026-10-15 有推送；2026-09-29 发布 0.30.6，[Releases](https://github.com/roboflow/supervision/releases) | 模型无关的 `Detections`、ByteTrack、PolygonZone/LineZone、轨迹与标注、视频处理、数据集格式转换；适合做 LabSafe 的检测后处理公共层 | 不负责模型服务、持久化和完整告警闭环；版本演进快。建议固定版本并为规则层加回放测试，不把 Roboflow 云 API 作为必需依赖 |
| [nisanuraydin/ppe-safety-detection](https://github.com/nisanuraydin/ppe-safety-detection) | MIT | 2026-08-07 有推送，但为 2026 年新仓库、提交很少、社区规模极小 | 双模型 PPE 检测、人员与 PPE 几何关联、矩形危险区、违规计时、截图/报告、Streamlit 演示；可帮助快速理解端到端页面流程 | 权重和数据不完整、缺少成熟 track-ID 关联、状态主要在会话内。只参考规则和交互，不作为生产底座；相关逻辑应重写并测试 |
| [gaia-solutions-on-demand/DFireDataset](https://github.com/gaia-solutions-on-demand/DFireDataset) | 仓库 API 为 `NOASSERTION`，但 [LICENSE](https://github.com/gaia-solutions-on-demand/DFireDataset/blob/master/LICENSE) 明确为 CC0-1.0 | 未归档，最后源码推送 2025-12-31；数据集型项目，维护频率不应与框架同等解读 | 21,000+ 张火焰/烟雾/无火无烟图像，YOLO 格式；可作为烟火检测冷启动数据和负样本来源 | 数据主要面向一般/户外场景，不能证明对实验室微小火焰、蒸汽、粉尘、强反光有效。必须加入本地场景、易混淆负样本，并按摄像头/场景分组划分训练验证集，避免视频近邻帧泄漏 |
| [pyronear/pyro-engine](https://github.com/pyronear/pyro-engine) | Apache-2.0 | 未归档，2026-10-14 有推送 | ONNX Runtime 边缘推理、RTSP/HTTP 快照/PTZ 摄像机适配、检测结果上报告警 API、Docker、watchdog 和测试；适合复用“摄像机 -> 边缘推理 -> 告警服务”的骨架 | 针对野火远景图像，不是室内实验室模型。只复用相机适配、进程守护和上报结构，烟火模型必须重训和重新定阈值 |
| [open-mmlab/mmaction2](https://github.com/open-mmlab/mmaction2) | Apache-2.0 | 未归档，2026-03-18 有推送；正式 Release 相对较旧 | 视频动作识别、骨架动作识别、时空动作检测、时序定位和模型 zoo，可用于跌倒、打斗、攀爬或违规操作研究 | MMCV/MMEngine/PyTorch/CUDA 版本矩阵复杂；通用动作类别不等于实验室危险行为。建议二期离线验证，首版采用姿态 + 区域 + 时序规则，并明确误报/漏报指标 |
| [blakeblackshear/frigate](https://github.com/blakeblackshear/frigate) | MIT | 未归档，2026-10-15 有推送 | 低开销运动检测门控后再做目标检测、多进程推理、MQTT、事件录像、保留策略、RTSP 重推流、WebRTC/MSE、区域编辑器；其 [README](https://github.com/blakeblackshear/frigate#readme) 明确描述了这些能力 | 面向家庭 NVR，整套系统较重且有 Home Assistant 偏置。建议借鉴 `motion gate -> detect -> event -> MQTT/recording` 分层和配置方式，不直接 fork 整套 UI |
| [bluenviron/mediamtx](https://github.com/bluenviron/mediamtx) | MIT | 未归档，2026-10-15 有推送 | RTSP/RTMP/WebRTC/HLS/SRT 等协议接入与转换、多路流、录像回放、鉴权、Control API、Prometheus 指标和 hooks；[README](https://github.com/bluenviron/mediamtx#readme)说明其为零依赖单可执行程序 | 它不做 AI 推理和事件语义。建议只作为视频路由层，摄像头凭据不进入前端；生产环境启用 HTTP/JWT 鉴权、网络隔离和最小权限 |
| [open-edge-platform/dlstreamer](https://github.com/open-edge-platform/dlstreamer) | MIT | 未归档，2026-10-15 有推送；官方明确提示 `main` 可能不稳定，应选 [稳定 Release](https://github.com/open-edge-platform/dlstreamer/releases) | 基于 GStreamer 的多流视频分析、OpenVINO CPU/GPU/NPU 推理、检测/跟踪、MQTT/Kafka 元数据、Python/C++ 扩展、ONVIF 与 Smart NVR 样例 | Intel/OpenVINO 生态绑定且 GStreamer 调试门槛较高。仅在 Intel 目标硬件上作为生产候选；锁定稳定版本，并让业务规则独立于 pipeline/pad probe |
| [NVIDIA/DeepStream](https://github.com/NVIDIA/DeepStream) | 仓库源码 Apache-2.0；README/仓库内文档与 skills 还含 CC-BY-4.0，需逐项核对 [LICENSE](https://github.com/NVIDIA/DeepStream/blob/main/LICENSE) | 未归档，2026-10-14 有推送；官方仓库面向 DeepStream 9.1 | NVIDIA GPU/Jetson 上的 GStreamer/TensorRT 多流多模型推理、跟踪、消息 broker、参考应用、Service Maker 以及官方 YOLO 工具 | NVIDIA 硬件锁定、部署复杂；样例、SDK、容器与模型可能有不同许可。仅当确定 Jetson/RTX 为目标平台时选用；业务事件契约保持硬件无关 |
| [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime) | MIT | 未归档，2026-10-15 有推送 | 跨平台高性能推理，可运行从 PyTorch、TensorFlow 等导出的 ONNX 模型，并使用不同硬件执行提供程序；适合 Windows/CPU 演示和统一模型接口 | 自定义算子、动态尺寸、NMS 和前后处理可能造成导出兼容问题。每个模型都要固定 opset、输入尺寸、类别表和数值一致性测试，不能只验证“文件能加载” |
| [cvat-ai/cvat](https://github.com/cvat-ai/cvat) | MIT | 未归档，2026-10-15 有推送 | 图片/视频标注、质量复核、协作、REST API/Python SDK，以及 COCO、YOLO、Ultralytics YOLO 等 20+ 格式导入导出；适合构建实验室场景数据闭环 | 完整部署较重；云存储和用户数据涉及隐私。建议与运行系统隔离，在离线/内网标注环境中使用，并建立标注规范、复核比例和数据版本号 |
| [deepset-ai/haystack](https://github.com/deepset-ai/haystack) | Apache-2.0 | 未归档，2026-10-15 有推送 | 模块化索引、检索、排序、路由、生成与评估流程；可把 SOP、SDS、设备说明书、应急预案构造成可追踪 RAG 管线，并通过 Hayhooks 暴露 API | 组件多、版本更新快；LLM 可能幻觉。必须强制显示来源、版本、页码和“不足以回答”状态；涉及应急处置时优先返回原文步骤，不让模型自由补全 |
| [qdrant/qdrant](https://github.com/qdrant/qdrant) | Apache-2.0 | 未归档，2026-10-15 有推送 | 向量检索、payload 元数据过滤、混合检索、量化和分布式部署；适合按实验室、设备、化学品、文档版本和有效期过滤知识 | 官方 quick start 默认无鉴权并监听网络接口。生产必须启用鉴权、TLS/网络隔离、备份与集合版本管理；小规模原型可先用嵌入式/单机模式，避免过早分布式化 |

## 3. 与 LabSafe AI 功能模块的具体映射

### 3.1 PPE 合规检测

建议组合：`Ultralytics（训练/导出） + ONNX Runtime（推理） + supervision（跟踪与规则） + CVAT（复核标注）`。

不要把 `no_helmet`、`no_gloves` 等单一框直接等价为“某人违规”。规则层应围绕人员 track 建立状态：

- 先跟踪人员，再按头部、躯干、手部区域把 PPE 与人员关联；
- 按实验室/区域配置所需 PPE，例如化学区要求护目镜和手套，普通走廊不要求；
- 只有连续 `N` 帧或持续 `T` 秒不合规才生成事件；
- 遮挡、背身、检测置信度不足时输出“无法确认”，而不是直接判定违规；
- 同一人员、同一区域、同一违规类型在冷却窗口内合并，避免告警风暴；
- 将误报复核结果回流 CVAT，形成 hard-negative 数据集。

### 3.2 火焰/烟雾检测

建议用 D-Fire 进行冷启动，但必须追加实验室本地数据：酒精灯火焰、电弧、蒸汽、粉尘、白色烟雾、反光、阳光、显示屏画面、灭火器喷雾等。

告警不应只依赖单帧置信度。推荐状态机：`candidate -> confirmed -> active -> cleared`，综合连续帧、区域、目标增长速度和持续时间。视觉 AI 只能作为早期发现与辅助取证，不能替代合规烟感、温感、气体传感器、消防联动和人工确认。

### 3.3 危险行为与危险区域

首版先实现可解释规则：

- 未授权人员进入危险区；
- 人员越线、长时间停留、靠近运行设备；
- 跌倒候选（人体姿态 + 高宽比 + 持续时间）；
- 快速奔跑、攀爬、倒地后长时间不动；
- 两人/多人过近或在指定设备周围聚集。

MMAction2 只在本地采集并标注相应行为视频、制定按摄像头隔离的数据集划分、完成混淆矩阵和场景回放后再接入。动作识别结果也应进入同一规则状态机，不直接触发最高级别处置。

### 3.4 视频流、告警和证据

视频层与业务层解耦：MediaMTX/DeepStream/DL Streamer 只负责流、推理和元数据；LabSafe 自有服务负责事件聚合、告警策略、升级、确认、关闭、审计和证据索引。

建议统一事件契约：

```json
{
  "event_id": "uuid",
  "camera_id": "lab-a-cam-01",
  "type": "ppe.no_goggles",
  "severity": "warning",
  "object_track_id": "track-184",
  "zone_id": "chemical-bench-2",
  "first_seen": "2026-10-15T14:30:12+08:00",
  "last_seen": "2026-10-15T14:30:18+08:00",
  "confidence": 0.87,
  "evidence_uri": "evidence://...",
  "model_version": "ppe-2026.10.1",
  "rule_version": "ppe-rule-3",
  "status": "open"
}
```

证据必须能追溯到 `camera_id + 时间戳 + 模型版本 + 规则版本`；截图和短视频的保存周期、访问权限、脱敏和导出审计应单独配置。

### 3.5 实验室安全知识库/RAG

Haystack 负责编排，Qdrant 负责向量和元数据检索。知识条目至少保存：`document_id`、标题、版本、发布日期/生效日期、来源部门、适用实验室、设备/化学品标签、页码、原文片段、文件哈希和失效状态。

回答策略应遵循：

1. 先按实验室、设备、化学品和有效期过滤；
2. 再做关键词 + 向量混合检索；
3. 输出原文引用和页码；
4. 来源冲突时显示冲突，不替用户裁决；
5. 找不到可靠依据时明确拒答并引导联系安全负责人；
6. RAG 与实时视觉告警分库、分服务、分权限，避免知识库故障阻断告警。

## 4. 推荐实施顺序

### Phase 0：许可证与数据边界

- 确定项目是否闭源、是否对外提供网络服务，再决定能否使用 Ultralytics AGPL 代码；
- 建立第三方组件、模型权重、数据集的许可证清单，三者分别记录；
- 确定摄像头数据的采集、保留、脱敏、授权和删除规则。

### Phase 1：单摄像头可验证闭环

- MediaMTX/本地视频文件接入；
- 单一 PPE 或烟火 ONNX 模型；
- supervision 跟踪与区域规则；
- 自有事件状态机、截图、确认/关闭接口；
- 用录制视频做可重复回放测试，记录误报、漏报、端到端延迟和掉帧。

### Phase 2：数据闭环与多摄像头

- CVAT 标注/复核；
- 引入场景分组的数据集划分和版本化模型评测；
- 多摄像头并发、断流重连、GPU/CPU 资源限额、告警去重；
- Intel 选 DL Streamer，NVIDIA 选 DeepStream，二选一做硬件专项优化。

### Phase 3：知识库与高级行为识别

- Haystack + Qdrant 构建带来源引用的 SOP/SDS 问答；
- 先上线只读检索和问答，不让其执行告警处置；
- 在有足够本地行为视频和评测集后，再验证 MMAction2 或其他时序模型。

## 5. 只作参考、不建议直接成为生产依赖的仓库

- [manuelbomi/rtsp-yolo-video-analytics-pipeline](https://github.com/manuelbomi/rtsp-yolo-video-analytics-pipeline)：MIT，2026-09-26 有推送。其 RTSP -> YOLO -> 跟踪 -> 区域规则 -> MQTT/Webhook -> WebSocket 看板的模块边界与 LabSafe 很接近，适合阅读架构和接口；但仓库创建于 2026-09、社区规模极小，不能据此推断生产成熟度。
- [ParsaVictor/PPE_detection-Sentinel](https://github.com/ParsaVictor/PPE_detection-Sentinel)：许可证文件为 AGPL-3.0，适合参考“以人员为中心的 PPE 空间关联 + ByteTrack + 时间滞回 + 事件截图”；项目提交少、成熟度低，且存在上游模型/权重许可风险。
- [marcoslucianops/DeepStream-Yolo](https://github.com/marcoslucianops/DeepStream-Yolo)：MIT，可参考旧版 DeepStream 的 YOLO parser、ONNX/TensorRT 和 INT8 配置；若使用 DeepStream 9.1，应优先采用 [NVIDIA/DeepStream](https://github.com/NVIDIA/DeepStream) 官方工具，避免从滞后的兼容层起步。

## 6. 验收指标建议

不能只报告模型 mAP。LabSafe 的系统验收至少应包含：

- 每一类风险的 Precision、Recall、F1、每小时误报数；
- 按摄像头、时段、光照、遮挡和目标尺寸分组的结果；
- 首次出现到告警送达的 P50/P95 端到端延迟；
- RTSP 断流后的重连时间、事件重复率和证据丢失率；
- 连续运行 24/72 小时的 CPU、GPU、内存、显存和磁盘占用；
- 模型/规则升级前后的固定回放集回归；
- RAG 回答的引用覆盖率、无依据拒答率、过期文档命中率；
- 人工复核结论能否回溯到原始帧、模型版本、规则版本和知识来源。

只有通过固定数据集、录像回放和目标硬件测试的能力，才能写成“已实现并验证”；其余应标为“候选方案”或“计划能力”。
