# 开发环境与工程管理

更新：2026-10-27。用户已授权并推进 P0–P7 开发，未完成验证保留待办；实施与验收状态见测试文档。桌面、离线分发、备份和登录启动见 [分发说明](distribution.md)。

## 已建立的基线

- 项目根目录：`C:\Users\Qziky\Desktop\Recorder`。
- uv 统一管理环境、依赖与锁文件；当前 uv 为 0.12.15。
- Python 首个验证目标为 3.11 x64，.python-version 固定 3.11.15，项目声明 >=3.11,<3.12。后续扩大范围需先验证依赖。
- .venv 位于根目录，复用本机已有 Python；本轮不安装系统级 Python 或修改全局工具配置。uv 的常规下载/构建缓存仍使用其用户缓存目录。
- 使用 uv_build 构建 src 布局的应用包；开发工具为 Ruff、pytest，精确解析结果以 uv.lock 为准。
- P0 运行依赖为 windows-capture 2.0.1（MIT）、NumPy 2.4.6（wheel 声明 BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0）、opencv-python 5.0.0.93（Apache-2.0，随包第三方声明须保留）；开发采样依赖 psutil 7.2.2（BSD-3-Clause）。版本与许可读取本机已安装包元数据，版本由 uv.lock 锁定；Tkinter/SQLite 使用 Python 标准库。Ollama 环境见 [本地视觉模型](ollama.md)，P0 不调用模型。
- P1 使用 `rapidocr-onnxruntime==1.4.4`（Apache-2.0）及其锁定的 ONNX Runtime 1.31.0（MIT）。该发布 wheel 内含三份本地模型，运行时不另下载模型；与目前 RapidOCR 3.x API 不混用。新增间接依赖包括 Pillow 13.0.0（MIT-CMU）、Shapely 2.2.0（BSD-3-Clause）、pyclipper 1.4.0（MIT）等，完整版本见 uv.lock。只安装到项目 .venv；未修改系统 OCR 或全局设置。
- Web UI 使用 FastAPI 0.162.0（MIT）、Uvicorn 0.54.0（BSD-3-Clause）；HTTP 测试使用开发依赖 httpx2 2.16.0（BSD-3-Clause）。React 18、Fluent UI React v9、Fluent 图标、TypeScript 与 Vite 的精确版本由 `frontend/package-lock.json` 锁定。已构建资源随 wheel 分发，普通运行无需 Node.js；修改前端时需要 Node.js 20.19+（20.x）或 22.12+ 与 npm。Tkinter 仅用于 tests/manual 的合成窗口。
- Git 使用 main 分支与初始化提交；没有配置远程或推送。
- P4 时区数据 tzdata 2026.5（Apache-2.0）；P6 Pillow 13.0.0（MIT-CMU）；P7 pystray 0.19.5（LGPLv3），构建开发依赖 PyInstaller 6.22.3（GPLv2+，附分发 bootloader 例外）。分发保留依赖许可证及源文件。52 个锁定包包含跨平台条件依赖，实际安装数量以平台为准。
- 本轮开发时旧程序持有 `.venv/Scripts/personal-memory.exe`，没有结束用户程序；改用忽略目录 `data/development-venv` 验证（设置 UV_PROJECT_ENVIRONMENT）。正常使用请先从旧程序退出，再执行常规 `uv sync --locked`，不要同时启动同一数据目录。

版本号 0.1.0 为开发包元数据，不代表产品已经发布或 P0 已放行。

## 目录

```text
Recorder/
├── .git/
├── .gitignore
├── .gitattributes
├── .python-version
├── .venv/                 # 本地环境，不提交
├── pyproject.toml
├── uv.lock
├── README.md              # 项目介绍与文档入口
├── AGENTS.md              # Agent 入口
├── frontend/              # React/Fluent 源码、npm 锁及构建工具
├── docs/
│   ├── README.md
│   ├── product/
│   ├── technical/
│   ├── development/
│   └── references/
├── src/xilogue/   # app、config、collector、processing、storage
└── tests/                 # unit、integration、manual
```

## 日常命令

在项目根目录执行；无需手动激活虚拟环境：

```powershell
uv sync --locked
uv run --locked xilogue
uv run --locked python -m xilogue
uv run --locked ruff check .
uv run --locked ruff format --check .
uv lock --check
uv build
```

两个启动入口均启动本机 Web 服务，并打开系统默认浏览器，默认停止，点击“开始采集 / 恢复采集”后才采集。启动可初始化配置、空库和生命周期维护，但不自动开始截图。网页的“退出程序”会等待后端和工作线程回收并停止服务；关闭网页不等于退出。完整操作与浏览器限制见 [Web UI 说明](web-ui.md)。

仅在修改前端或图标时，在 `frontend/` 运行以下命令；npm 依赖安装在该目录，构建输出提交到 Python 包的静态资源目录。运行 `npm run brand` 会从 `frontend/branding/icon.svg` 更新网页、托盘、扩展和 ICO 资源，`npm run licenses` 会刷新前端第三方许可汇总。普通用户运行已构建项目无需执行这些命令。

```powershell
cd frontend
npm ci
npm run check
npm test
npm run build
```

P1 增加本地 OCR 与“搜索记忆”；开始/恢复同时允许处理已保存图片，手动暂停会阻断旧 OCR 结果提交。概览的“仅识别已保存图片”独立于屏幕采集，明确发起一个 OCR 批次，结束后释放会话并自动暂停。搜索可在暂停时使用。无需启动采集的显式离线处理与搜索命令见 [OCR 说明](ocr.md)。首次打开正式 v1 数据库会事务升级到 schema v2；请先退出旧程序，同目录仍只允许一个实例。

`--no-browser` 只输出完整启动链接，`--port 8765` 指定本机端口，默认自动分配；没有对外监听参数。离线 OCR/搜索及 --check-config 不启动 Web 服务。启动链接含本次会话令牌，不提交、不外发，HTTP 访问日志关闭。

```powershell
uv run --locked xilogue --data-dir data/try-p0
uv run --locked xilogue --config config/config.example.json --check-config
uv run --locked pytest -q
```

数据目录必须专用于本应用；现有未标记的非空目录拒绝初始化，同一目录只允许一个实例。首次启动生成 version=1 配置；显式 --config 必须指向现有文件。在“采集设置”保存会先校验并原子替换选定配置文件，再应用；仅停止或暂停时允许修改。外部修改配置后点击“从文件重新加载”；无效文件阻止再次开始，修正后重载或保存有效配置即可恢复。

停止状态不阻止保留维护。独立图片以 WebP 保存，SQLite 为 memory.sqlite3，日志最多 3×256KiB，不记录窗口标题和图像内容。图像配额不包含数据库、临时文件和日志。排错先看 UI 最后错误与 logs/events.log；疑似黑帧保守跳过，可能误跳过合法纯黑内容，不承诺识别所有受保护或敏感内容。

删除单条记录：在“记忆检索”打开详情并确认删除，相关原图、OCR、索引与任务一并清理，文件失败时提示待清理。删除全部本地记录：先退出并确认进程结束，再删除选定的应用专用数据目录。只处理该目录，不处理旧 PersonalMemoryP0 或其他应用数据。图片或缺图记录经维护清理后不能依靠“最近图片”入口恢复。普通删除不承诺介质安全擦除。

## 依赖与版本控制

运行依赖使用 `uv add 包名`，开发工具使用 `uv add --dev 包名`，移除用 `uv remove` 或 `uv remove --dev`。不要在 .venv 中手动 pip install 后漏记依赖；变更 pyproject 时同步更新 uv.lock。

按锁文件同步环境使用 `uv sync --locked`；`--locked` 会检查锁文件与声明是否一致。安装和构建会访问软件源，依赖下载不涉及上传用户活动数据。[uv 锁定与同步](https://docs.astral.sh/uv/concepts/projects/sync/)

提交源码、docs、pyproject.toml、uv.lock 和 .python-version；.venv、构建产物、缓存、个人配置、图像、日志和数据库由忽略规则排除。正式数据目录与配置路径已按 [决策记录](../technical/decisions.md) 落实，用户运行时生成的配置与真实数据不纳入版本控制。

P0 按 [任务单](p0-plan.md) 推进。默认数据根目录为 `%LOCALAPPDATA%\Xilogue`，配置为其中的 config.json；`--data-dir` 覆盖数据根目录，`--config` 覆盖配置文件位置。样例见根目录 config/config.example.json，配置版本为 1，未知字段及无效组合拒绝启动采集。

项目已更名为“昔录 · Xilogue”，Python 发行包、导入模块和命令统一使用 `xilogue`。旧 `%LOCALAPPDATA%\PersonalMemory` 数据不会自动读取或迁移，原目录保持不动；旧名称版本的备份不受支持。

pytest 临时数据使用项目 data/pytest/ 下每次唯一的目录，避开本机系统临时目录 ACL 问题；真实与合成测试数据均不提交 Git。

手动验收命令与真实状态统一见 [测试文档](testing.md)。受控场景工具显式启动自己的合成窗口；普通应用入口仍必须手动点击开始。[日常使用长测](real-usage-test.md) 复用正式控制界面，默认停止，另行记录性能与状态，不创建合成窗口或切换用户窗口。Python wheel/sdist 与可选 Windows 免安装包的构建和依赖许可见[分发说明](distribution.md)。
