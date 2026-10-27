# 参考资料与链接

更新：2026-10-26。集中保留原交接材料中的外部资料，增加本项目 uv 工程资料。链接是研究线索，不代表安装依赖、复制许可或已完成实机验证。

## uv 工程

| 资料 | 用途 |
| --- | --- |
| [项目初始化](https://docs.astral.sh/uv/concepts/projects/init/) | 应用包、src 布局与初始化选项 |
| [锁定与同步](https://docs.astral.sh/uv/concepts/projects/sync/) | uv.lock、--locked 与环境同步 |
| [uv 构建后端](https://docs.astral.sh/uv/concepts/build-backend/) | uv_build 与项目打包 |

## Windows 采集与本地索引

| 资料 | 用途 / 已核对边界 |
| --- | --- |
| [Windows.Graphics.Capture](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture) | WGC API 总览 |
| [CreateForWindow](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow) | 按 HWND 创建捕获对象；文档最低客户端为 Windows 10 1903 / Build 18362，不能直接作为产品支持承诺 |
| [GetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow) | 前台窗口句柄 |
| [SetWinEventHook](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwineventhook) | 前台窗口事件候选机制 |
| [GetLastInputInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo) | 当前调用会话的输入空闲，不代表用户是否仍在观看或阅读 |
| [windows-capture 2.0.1](https://pypi.org/project/windows-capture/2.0.1/) | P0 已锁定安装，HWND 合成窗口短测通过；完整 Windows 场景待验 |
| [SQLite FTS5](https://www.sqlite.org/fts5.html) | P1 全文索引候选 |
| [FTS5 trigram](https://www.sqlite.org/fts5.html#the_trigram_tokenizer) | 全文子串查询少于 3 个 Unicode 字符时不匹配，中文短查询需额外方案 |

2026-10-25 至 26 的先前核对读取了 windows-capture 2.0.1 官方 wheel，确认 window_hwnd、start_free_threaded、CaptureControl.stop/is_finished 和 row pitch 四通道 NumPy 视图存在；Python 元数据为 >=3.9、发布页标示 MIT。随后 P0 已实际安装并验证部分捕获行为，结果见测试文档；不能据此宣称全部 Python/Windows 组合兼容。

核对对象：`windows_capture-2.0.1-cp39-abi3-win_amd64.whl`；下载内容 SHA-256 与 PyPI 元数据一致：`209c2318e2168802b9afd1005e99499639e8835e71a1e188a3ddc77c7ccc3f04`。正式使用前按最终版本复核许可证、传递依赖和分发要求。

## 后续技术候选

- [RapidOCR](https://github.com/RapidAI/RapidOCR)：P1 本地 OCR 评估，需测试中文、英文、代码、坐标和吞吐。
- [Ollama](https://ollama.com/)：模型选型基线见 [模型环境](../development/ollama.md)，业务 Provider、引用校验与本机实测见 [本地分析](../development/analysis.md)。

## 产品与架构参考项目

| 项目 | 研究方向 |
| --- | --- |
| [Windrecorder](https://github.com/yuka-friends/Windrecorder) | Windows 历史、OCR、去重与回溯 |
| [Screenpipe](https://github.com/screenpipe/screenpipe) | 多源采集、异步流水线和检索接口 |
| [Dayflow](https://github.com/JerryZLiu/Dayflow) | 活动片段与回顾体验 |
| [ActivityWatch](https://github.com/ActivityWatch/activitywatch) | 活动元数据与隐私优先 |
| [OpenRecall](https://github.com/openrecall/openrecall) | 截图索引与历史检索 |
| [Pensieve](https://github.com/arkohut/pensieve) | 个人历史的数据组织 |

这些项目的当前维护状态、许可证和全部功能未逐项审计。如使用其代码，需要单独核对具体版本及依赖许可，不能将某个候选包的 MIT 结论套用到所有参考项目。

本项目自身旧原型的结论见 [原型经验](prototype-findings.md)。技术选择与状态以 [决策记录](../technical/decisions.md) 为准，实测结论以 [测试记录](../development/testing.md) 为准。
