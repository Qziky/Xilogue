# 技术决策与实验参数

更新：2026-10-27。P0–P7 开发已落地，完整验收尚未完成；旧原型中的选择不自动升级为正式决策。

## 当前决策

| ID | 决策 | 状态与依据 |
| --- | --- | --- |
| D-01 | 正式工程在当前根目录；旧原型清理，仅留提炼文档 | 已落实，用户本次要求 |
| D-02 | Windows 优先，P0 仅活跃窗口，先采集后理解 | 已确认，合并后的产品基线 |
| D-03 | WGC 按 HWND 捕获 | 已实现，合成窗口短测通过；完整兼容范围仍待 Windows 矩阵 |
| D-04 | Python 3.11 x64、SQLite、WebP；正式界面改为本地 Web UI | 用户于 2026-10-26 要求迁移；FastAPI/Uvicorn + 随包静态 HTML/CSS/JS，Tkinter 仅用于合成测试 |
| D-05 | windows-capture 2.0.1 | 已锁定安装，实测 BGRA、自有内存复制、行跨度、更新、尺寸变化和停止；未完成完整应用场景验收 |
| D-06 | 采集与重处理异步隔离，有界队列 | 已确认；P0 候选不持久化，后续任务可恢复 |
| D-07 | 使用 uv、根目录 .venv、pyproject、uv.lock 与 src 布局 | 已落实；运行与开发依赖均由锁文件管理 |
| D-08 | P1 本地 OCR 采用 rapidocr-onnxruntime 1.4.4 / CPU；Web UI 见 D-04，P6 扩展使用独立配对通道 | [发布包](https://pypi.org/project/rapidocr-onnxruntime/1.4.4/) 随 wheel 提供 PP-OCRv4 检测/识别及方向模型；合成中文、英文、代码已实测，范围与限制见测试记录 |
| D-09 | Ollama 本地环境保留；2026-10-27 用户要求可自行选择 API 提供商 | 摘要和嵌入独立选择本地 Ollama 或 OpenAI 兼容接口，默认本地；API 目标、凭据、发送许可和证据校验见 [分析](../development/analysis.md)；P5 本地服务见 [相关检索](../development/semantic.md) |
| D-10 | 项目品牌与工程标识统一为“昔录 · Xilogue” | 2026-10-27 用户确认英文项目名并明确无需兼容旧名；包、模块、命令、数据目录和标记同步更名，不迁移旧数据或旧版备份 |

不默认使用整屏裁剪或 DXGI 桌面复制作为窗口捕获 fallback。候选失败时先记录原因，再评估 PyWinRT 或最小原生适配层；替代实现必须满足同一目标与隐私契约。

## 技术核对与来源

WGC API 门槛、候选发布包接口、输入空闲与 FTS5 短查询限制已做文档/源码核对，完整出处见 [参考资料](../references/README.md)。这些证据不构成 Windows 实机通过；待验证项见 [原型经验](../references/prototype-findings.md)。

## 参数唯一来源

下表数值已作为 P0 配置初始值落地，仍不是实测最优值。表中历史字段用于追溯原型，正式字段以 config/config.example.json 和 Config 为准：图像配额为 image_quota_mib，其余图像参数、网格和队列均已配置化。配置仅在启动或暂停后重载时生效；保留维护周期 maintenance_seconds=60。

| 参数 | 初始值 | 历史依据 / 正式要求 |
| --- | --- | --- |
| 采样最短间隔 | 5.0 秒 | config：sample_interval_seconds |
| 窗口稳定等待 | 0.6 秒 | config：foreground_stabilize_seconds |
| 前台轮询 | 0.25 秒 | config：poll_interval_seconds |
| 输入空闲阈值 | 90 秒 | config：idle_seconds；原型直接暂停 |
| 空闲策略 | 默认 pause，允许显式 ignore | 已配置化并提示长文/视频漏采；自动媒体识别不属 P0 |
| 图像配额 | 512 MiB | config：max_storage_mb，代码乘 1024²；只统计登记图像 |
| 保留期限 | 7 天 | config：retention_days |
| WebP 质量 | 78 | config：image_webp_quality |
| 灰度缩略图 | 宽 320、高 180 | diff.py 硬编码 |
| 局部网格 | 16 列 × 9 行，每块 20 × 20 | diff.py 硬编码，依赖缩略图尺寸 |
| 变化像素阈值 | 绝对灰度差 >14 | diff.py 硬编码 |
| 近似重复条件 | MAE <0.8 且 ratio <0.003 且 max_tile_mae <3.0 | diff.py 硬编码，三项同时满足 |
| local_change / large_change 分界 | ratio 0.04；局部条件 max_tile_mae >=3.0 | diff.py 硬编码 |
| 图像队列 / UI 事件队列 | 2 / 300 | engine.py / app.py 硬编码；正式必须有界且可观测 |
| 默认排除进程 | 1password.exe、bitwarden.exe、keepass.exe、keepassxc.exe、passwordmanager.exe | config：excluded_apps；不等于完整敏感应用名单 |

正式配置应校验类型、范围与组合约束：采样/轮询正数，稳定等待非负，期限/配额有效，质量 0–100，队列容量正整数，网格与图像尺寸兼容。无效配置给出可读错误，不进入采集。禁用空闲策略的字段需显式定义，不能把原型 idle_seconds=0 当作禁用。

`5 秒`限制候选处理频率，不保证每 5 秒有回调、有效图片或落盘。图像配额不包含数据库、日志、临时文件与备份；正式程序需分别报告这些开销。

## 路径建议

历史原型的数据目录曾为 `%LOCALAPPDATA%\PersonalMemoryP0`，config.json 位于原型源码目录。正式工程使用 `%LOCALAPPDATA%\Xilogue`，配置为其中 config.json，不处理历史原型目录。

上述正式路径已落实，--data-dir 与 --config 可分别覆盖。正式数据与历史实验隔离，不自动导入、不覆盖旧数据。依赖环境放根目录 .venv，不把正式用户数据放进源码目录或虚拟环境；验收工具显式选择忽略的 data/ 专用测试目录。

名称迁移后，新默认数据目录为 `%LOCALAPPDATA%\Xilogue`。原 `%LOCALAPPDATA%\PersonalMemory` 目录不会被读取、迁移或删除；旧名称版本生成的备份因标记不同不能恢复到新版本。

## 后续决策时点

P0-02 确定捕获后端、像素格式和已验证 Windows 范围；P1 评估中文/英文/代码 OCR、FTS5 能力与短查询；P3 根据用户硬件评估 Ollama 或其他本地 Provider；正式 UI、加密、环形缓冲、向量检索和浏览器通信到相应阶段再验证。

候选技术与设计参考项目统一维护于 [参考资料与链接](../references/README.md)，不作为依赖或代码复用授权。
