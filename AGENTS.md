# 项目工作约定

- 工作目录为当前项目根目录，使用 uv 管理 Python、依赖和 .venv；提交 pyproject.toml、uv.lock、.python-version。
- 先读 [文档入口](docs/README.md) 与 [开发说明](docs/development/setup.md)。所有产品、技术、开发、测试和参考文档放在 docs/，根目录 README 与本文件仅保留入口和工作约定。
- 用户已授权完整 P0 开发和验收，并于 2026-10-26 要求将暂时无法执行的验证记为待完成、继续后续开发。2026-10-27 明确要求“继续完成所有开发”，已授权按 [完整开发任务单](docs/development/full-plan.md) 推进路线图 P2–P7，不再限于 P1。已有 P0/P1 实机、长测待办不冒充通过。启动默认停止，实机测试使用隔离数据目录；受控场景使用无敏感材料。用户另已选择手动开始、正常使用电脑的长测方式，操作见 [日常使用长测](docs/development/real-usage-test.md)，不得自动开始采集或切换用户窗口。
- 后续开发按 [P0 任务单](docs/development/p0-plan.md) 与对应规格推进；设计不等于已有实现，自动化不等于 Windows 实机验收。
- 用户已要求迁移本地 Web UI；正式入口使用 FastAPI/Uvicorn 与随 Python 包分发的静态页面，见 [Web UI 说明](docs/development/web-ui.md)。仅监听回环地址，数据 API 必须校验启动令牌、Host 和 Origin；默认停止。Tkinter 只保留为合成捕获测试工具，不维护第二套正式界面。
- 正式代码位于 src/xilogue/，测试位于 tests/。旧原型和重复交接材料已清理，必要经验与链接已合入 docs/references/，不要恢复旧项目结构。
- 依赖用 uv add / uv add --dev 管理；同步用 uv sync --locked，执行用 uv run --locked。安装与设置保持项目范围，不修改无关全局配置。
- 只改当前任务所需文件，保留用户无关改动；更新对应文档，避免重复规格。隐私和数据生命周期要求见 [隐私规格](docs/technical/privacy.md)。
- 本地模型验证结束后，主动卸载本次验证加载的模型，并检查 Ollama 加载列表和显存释放结果；多次验证临时使用 keep_alive 时也须执行结束清理。保留用户原本驻留的其他模型。

## Windows 文件修改

优先直接调用 Codex 的 apply_patch 工具。若从 PowerShell 调用，先保存完整 LF 补丁字符串并保留末尾换行，再直接调用 Codex 主程序，将补丁作为一个完整参数传入 --codex-run-as-apply-patch。不要通过管道调用 apply_patch.bat。
