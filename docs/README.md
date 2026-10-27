# 文档入口

更新：2026-10-27。已按用户“继续完成所有开发”授权实现 P0–P7 路线中的开发功能。自动化、合成材料、真实本地模型与打包验证分别留证；剩余实机、扩展安装、长期运行验收仍按用户安排待完成，未正式放行。

当前正式界面使用本地 Fluent Web UI，浏览器扩展弹窗采用同一组件库与项目图标；原有验收待办继续保留。

AI 摘要和向量检索使用统一的提供商下拉框，Ollama 与其他服务并列，并可自动读取模型列表；服务地址、密钥、发送许可与测试入口见 [AI 提供商](development/analysis.md)。默认仍为本地处理。

P1 后续已补齐原始记录分页、OCR 状态筛选、单条失败重试与来源删除，使用见 [Web UI 说明](development/web-ui.md)，验证结果见 [测试记录](development/testing.md)。

现支持明确发起“仅识别已保存图片”，不启动采集；批次结束自动暂停并释放 OCR 会话。默认启动仍停止。

## 阅读顺序

| 分类 | 文档 | 用途 |
| --- | --- | --- |
| 产品 | [需求与边界](product/requirements.md) | 定位、用户体验、MVP 与 P0 范围 |
| 产品 | [阶段路线](product/roadmap.md) | P0–P7 交付与门槛 |
| 技术 | [架构](technical/architecture.md) | 分层、模块、状态与并发契约 |
| 技术 | [数据模型](technical/data-model.md) | Observation、后续模型、存储与删除 |
| 技术 | [隐私](technical/privacy.md) | 用户控制、采集检查和生命周期 |
| 技术 | [决策与参数](technical/decisions.md) | 已确认、暂定与待验证事项 |
| 开发 | [环境与命令](development/setup.md) | uv、目录、Git 和依赖操作 |
| 开发 | [本地视觉模型](development/ollama.md) | Ollama 安装、调用参数与图像验证 |
| 开发 | [P0 任务单](development/p0-plan.md) | 正式开发后的执行顺序 |
| 开发 | [P1 任务单](development/p1-plan.md) | 本地 OCR、持久任务与文字检索 |
| 开发 | [完整开发任务单](development/full-plan.md) | 2026-10-27 授权的 P2–P7 开发、集成与验证 |
| 开发 | [P2 活动片段](development/p2-plan.md) | 分组、代表图、来源、拆分合并及删除传播 |
| 开发 | [AI 提供商与分析](development/analysis.md) | 可选本地/API Provider、凭据、发送许可、引用、可恢复任务与释放 |
| 开发 | [每日回顾](development/reviews.md) | 本地日界、来源、反馈、修正与隐藏 |
| 开发 | [相关检索与主题](development/semantic.md) | 可选本地嵌入、模型版本、相关依据及跨日主题 |
| 开发 | [浏览器扩展](development/browser-extension.md) | 临时配对、主动可见页面、权限与限制 |
| 开发 | [桌面与离线分发](development/distribution.md) | 托盘、登录启动、打包、安装更新卸载、备份与恢复 |
| 开发 | [OCR 与文字检索](development/ocr.md) | 本地引擎、控制界面、离线处理及搜索命令 |
| 开发 | [本地 Web UI](development/web-ui.md) | 启动、页面、鉴权、退出与浏览器限制 |
| 开发 | [测试与验收](development/testing.md) | 初始化证据及待执行矩阵 |
| 开发 | [日常使用长测](development/real-usage-test.md) | 用户正常使用、结束后本地分析的操作步骤 |
| 参考 | [资料与链接](references/README.md) | 官方资料、候选技术和参考项目 |
| 参考 | [原型经验](references/prototype-findings.md) | 旧原型有效经验、风险和核对结论 |

每个主题只有一个维护来源。规格中的目标架构、字段和参数不代表已经实现；实现状态与验证结果分别在开发说明和测试文档维护。

## 本次归并与清理

旧交接文件、重复开工提示与完整文本归档已合入上述文档，不保留重复副本。旧 personal-memory-p0 实验代码、启动脚本、配置与测试已移除；原型技术经验和所有原参考链接已提炼保留。

根目录保留必要工程文件、项目 README 和 AGENTS.md 工作约定。正式代码直接位于本项目的 src/xilogue/，不新建另一层工程目录。真实测试图片、数据库及原始报告位于忽略的 data/；工程检查、合成窗口短测与完整 Windows 验收分别记录，不互相替代。
