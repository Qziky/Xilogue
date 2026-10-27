# 桌面入口、离线分发和数据维护（P7）

## 使用与桌面控制

`uv sync --locked` 后 `uv run --locked xilogue`，默认停止，显示托盘并打开本地界面。托盘可打开界面、开始、暂停和退出；关闭网页不等于退出。使用 `--no-tray --no-browser` 可仅运行本地服务，仍默认停止。退出等待在途本地模型请求、释放会话和自有嵌入服务。

登录启动是单独的明确操作：`uv run --locked xilogue --startup enable`；`--startup status` 查看，`--startup disable` 撤销。仅写当前用户 HKCU Run 项，无管理员权限、无开机自动采集。源码入口依赖当前项目环境，移动工程后需重新设置；分发版使用 `Xilogue.exe` 同名参数。开发过程中没有启用用户登录启动或改注册表。

## 构建和离线范围

`uv run --locked python scripts/build_windows.py` 在时间戳 `dist/` 内生成 Windows x64 onedir、portable ZIP、SHA256SUMS 和 build-info。含 Python 3.11、预构建 Fluent 静态页面、扩展 ZIP 素材、RapidOCR 模型和原生库、文档、锁文件、项目根目录 LICENSE 及依赖许可证；运行无需 Node.js 或 CDN。Windows 可执行文件、托盘、网页和扩展使用同一“记忆窗口”图标。前端运行依赖的完整许可文本位于包内 `assets/FRONTEND-LICENSES.txt`，前端源码与 npm 锁文件也随包提供，不含 node_modules。包的 build-info 记录版本、Git 修订、dirty 状态、精确依赖；当前为未签名开发分发，不承诺 SmartScreen 放行。打包后必须做下述入口/OCR/迁移检查，不能把构建成功当运行通过。

采集、OCR、分组、文字搜索、浏览和备份可以离线运行。Ollama 可执行文件及 9B/嵌入模型没有塞进分发包；本地分析/向量需预先安装 Ollama 并准备模型。设置 embedding_models_dir 为已有离线缓存，或显式配置使用本机已有服务。也可按 [AI 提供商](analysis.md) 主动选择 API，此方式需要网络及相应服务凭据；本地模型缺失不自动转云端或下载。

解压 ZIP 可直接运行 exe。安装：从解压目录执行 `powershell -File maintenance/install.ps1`，安装到 `%LOCALAPPDATA%\Programs\Xilogue` 并创建开始菜单快捷方式；不自动启动。更新先退出，再从**新解压包**执行同脚本加 `-Update`，校验文件摘要，保留上版为 `Xilogue.previous`；已有 previous 时先人工保管/移走以免覆盖。升级前先备份数据，因为数据库 schema 只向前迁移，不能只换旧 exe 回滚新版数据库。

卸载：退出程序后执行安装目录 `maintenance/uninstall.ps1`，默认保留数据、模型缓存和 previous。明确加 `-RemoveData` 才清理标准专用数据目录；自定义目录需用户另行处理。安装/卸载脚本限制固定用户目录、拒绝目录连接、拒绝运行中替换，不提升权限。已在测试子进程的独立 LOCALAPPDATA 中用 `-NoShortcut` 验证安装、更新保留 previous、卸载保留数据和摘要拒绝；没有在用户真实安装目录执行维护或写快捷方式，真实升级/系统策略兼容仍待验。

## 一致性备份与恢复

暂停后在设置页下载，或关闭程序后运行 `xilogue --data-dir 数据目录 --backup 新备份.zip`。备份持有存储锁，使用 SQLite backup API 和受控图片清单，逐文件 SHA256；不包含模型、日志、进程锁或启动令牌。上限 8 GiB、单文件 2 GiB，HTTP 备份流式发送临时 ZIP 后清理。备份含敏感原文与原图，普通 ZIP 没有加密。

恢复：`xilogue --restore 备份.zip --data-dir 不存在的新目录`。先在同级临时目录检查路径、符号链接、重复条目、总量、摘要、schema、SQLite 完整性、外键和图片引用，全部通过再原子发布新目录。失败不会发布半成品，也不会覆盖原目录。启动恢复目录后仍停止，按当前保留期限和配额执行维护。恢复不重新启用登录启动或恢复扩展令牌。

数据 schema 1–8 自动在同一迁移事务升级，拒绝未来版本；迁移失败不推进 user_version。跨版本回退必须使用升级前备份。模型缓存与本机已有 Ollama 服务属于独立生命周期。

## 资源与可访问性

Web UI 键盘可进入所有按钮、筛选与对话框；原图保留替代文字，结果/错误有状态提示，关闭详情支持 Esc。响应式布局适配窄窗口。自动化、合成浏览器检查不能替代屏幕阅读器、触屏和真实多显示器验收。队列、事件、日志有界；图片配额、数据库、临时文件分别显示。1/8 小时与多日资源曲线仍按测试矩阵待完成，不声明长期稳定性已通过。

官方参考：[pystray](https://pystray.readthedocs.io/en/latest/usage.html)、[PyInstaller](https://pyinstaller.org/en/stable/usage.html)。
