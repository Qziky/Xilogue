# Ollama 本地多模态环境

更新：2026-10-27。本文保留模型环境与选型基线；业务 Provider 和带来源摘要已实现，使用与新实测见 [本地分析](analysis.md)、[本地嵌入](semantic.md)。纯采集不调用模型，自动分析默认关闭。

## 选择

已安装 **Qwen3.5 9B Q4_K_M**，通过项目配置注册为 `recorder-vision:balanced`，并完成本机图像验证。目标是在可用延迟内保留图像理解与中文内容识别能力，不把速度作为唯一选择标准。

| 模型 | 本次用途与结论 |
| --- | --- |
| Qwen3.8 27B | 本机原有；用户反馈延迟过高，因此不作为日常默认，保留原安装 |
| Qwen3.5 9B | 选为平衡档，官方支持图像输入；采用官方仓库 GGUF 与视觉投影器 |
| 本机 qwythos-q5:9b | 已有自定义 Q5 模型，视觉投影器标识 Qwen3.5-9B；用于预先验证 9B 档的可用延迟，不将它冒充官方同一权重 |
| Qwen3.5 4B | 安装作轻量对照；简单样本通过不足以证明与 9B 在复杂截图上质量相当 |
| Qwen3-VL 4B/8B | 已调查的备选，当前未安装 |

选择依据为官方 [Qwen3.5 9B](https://ollama.com/library/qwen3.5:9b)、[Qwen3.5 4B](https://ollama.com/library/qwen3.5:4b)、[Qwen3-VL 8B](https://ollama.com/library/qwen3-vl:8b) 型号资料及本机试验。系列页面中大模型的基准不能直接套用到小型号。后续仍需用真实但获授权的截图集验证复杂中文、代码、小字、图表和长文质量。

## 本机环境与作用范围

- RTX 5090 Laptop GPU，显存 24,463 MiB；系统内存约 63.4 GiB。
- Ollama 0.40.3，API 使用 `http://127.0.0.1:11434`。
- 运行中的 Ollama 实际模型目录为 `D:\ollamamodels`，通过现有模型的服务端 Modelfile 路径复核；不是仅依据客户端默认目录推断。
- 权重属于本机 Ollama 模型存储，不进入本项目 Git。未升级 Ollama、修改系统代理、设置全局模型默认值或删除已有模型。
- 项目只保存 [Modelfile](../../config/ollama/Modelfile) 与本文。Python 业务依赖、uv.lock 和程序入口不因本次准备而接入模型。

## 安装与重建

网络正常的机器可在根目录运行：

```powershell
ollama pull qwen3.5:9b
ollama create recorder-vision:balanced -f config/ollama/Modelfile
ollama show recorder-vision:balanced
ollama run recorder-vision:balanced --think=false --keepalive 10m
# 结束交互或验证后主动释放
ollama stop recorder-vision:balanced
```

本机普通 pull 遇到代理 Fake-IP 与 Ollama 重定向检查冲突：CDN 域名被解析到 198.18.0.98，下载被拒绝。本次从同一官方 registry 读取 GGUF manifest，通过正常验证 TLS 的下载器取得各层，逐文件核对 SHA-256 与大小，再经 Ollama 的 `/api/create` 导入模型及视觉投影器。没有关闭 TLS 或放宽重定向安全检查。官方接口参见 [创建模型](https://docs.ollama.com/api/create)。

固定核对的 9B 图像模型文件为：

- 模型：`sha256:02d45dc1cf451ba2475ac33b301c2dd8f985abe4c182ce04a1f2f5bf0260278d`，5,629,109,120 字节。
- 视觉投影器：`sha256:f836f08f921193f4d6b6a6952dba0a6fb116759d26a757bcb395d570d73976ca`，921,704,832 字节。

来源为 [官方 9B manifest](https://registry.ollama.ai/v2/library/qwen3.5/manifests/9b)，同时保留原许可证和参数。标签可能更新；重装时需要重新核对来源，不能把未来版本视为本次同一权重。

## 调用约定

| 设置 | 当前配置 |
| --- | --- |
| 模型名 | recorder-vision:balanced |
| num_ctx | 8192 |
| num_predict | 1024，上限，不要求输出满额 |
| temperature / top_p | 0.2 / 0.9 |
| repeat_penalty / presence_penalty | 1.0 / 0，避免给文字复现额外惩罚 |
| 日常图片请求 | `think: false`，按需开启更深推理 |
| keep_alive | 单次验证用 0；连续验证可临时用 10m，结束后主动卸载 |

`think` 和 `keep_alive` 是调用参数，不因 Modelfile 的注释自动生效。命令行明确传 `--think=false`；API 明确传 `think: false`。`--hidethinking` 只隐藏输出，不能当作关闭思考。

API 图片需要放入 messages 的 images 数组中并使用 Base64。示例请求结构：

```json
{
  "model": "recorder-vision:balanced",
  "messages": [{"role": "user", "content": "概括图片中的可见内容。", "images": ["图片的Base64"]}],
  "think": false,
  "stream": false,
  "keep_alive": 0
}
```

参见 [Vision](https://docs.ollama.com/capabilities/vision)、[Chat API](https://docs.ollama.com/api/chat) 和 [Modelfile](https://docs.ollama.com/modelfile)。截图或活动内容只发到本机回环 API，不使用云端模型。

## 验证结束清理

连续测试为了避免反复加载可临时保温，但必须在结束时显式执行 `ollama stop recorder-vision:balanced`，或发送空生成请求并设置 `keep_alive: 0`。脚本将卸载放在 finally 中，随后核对 `/api/ps`，确认本次加载的模型已经退出，再记录显存。模型文件保留供下次使用。

本次最初漏做结束清理，用户指出后已完成卸载：`/api/ps` 返回空列表，整机显存占用由 8,732 MiB 降至 1,946 MiB。其余显存属于其他桌面/应用开销，不能以总占用未归零认定模型仍驻留。

## 本次验证

输入是两张不同的 1100×700 人工合成图片，各含中文任务表、英文项目名、随机编号、日期和三种彩色形状。提示没有给出图片答案；返回 JSON 后按合成图片的已知内容核对。没有读取或采集真实屏幕。

| 对象 | 首次请求 | 已加载后换一张图 | 结果 |
| --- | --- | --- | --- |
| 已有 qwythos-q5:9b | 51.609 秒 | 2.660 秒 | 中文表格、编号、日期、颜色和形状核对通过 |
| Qwen3.5 4B | 17.755 秒 | 1.175 秒 | 同一组简单检查通过，仅作对照 |
| 项目默认的官方 9B 配置 | 5.691 秒 | 2.290 秒 | 两图各 14 项内容核对通过；实际上下文 8192 |

前两组使用 num_ctx=4096、关闭思考、最多 384 输出 token。首次请求包含加载及首次计算预热；第二张图与第一张不同，第二次仍可能受共享前缀缓存影响。这些延迟不能作为任意新截图的固定保证，也没有实测相对 27B 的加速倍数。

项目默认配置验证保留 num_ctx=8192，调用时关闭思考，测试输出上限 384、temperature=0。官方 9B 首次加载发生在本会话已测试相关架构之后，系统/CUDA 缓存可能已经预热，因此三组首次请求不能作为完全冷机的公平横向对比。

官方 9B 的首次请求加载约 3.22 秒、输入处理约 0.60 秒、生成约 1.84 秒；换图请求输入约 0.38 秒、生成约 1.82 秒。两图的项目名、编号、两行任务/状态/日期及三种形状/颜色均与生成时已知内容一致。全部 6 次请求共 84 项内容核对通过，这是安装验证而非生产精度基准。

`ollama ps` 核对默认模型为 100% GPU、8192 上下文、llamacpp runner，报告模型驻留约 5.8 GB；这一数值不是整机或请求峰值显存。注册模型 digest 为 `d5cea9245178717ca1b8f1132d56916c2219404280730679aae10466c7f08070`。

已有 9B 的首次总耗时中，加载约 25.3 秒、输入处理约 23.3 秒、生成约 3.0 秒；第二次输入约 0.52 秒、生成约 2.03 秒。部署时应区分冷启动和正常连续请求，避免频繁卸载重载。原始安装 manifest、合成图和响应位于被 Git 忽略的 data/ollama-validation/。

当前验证不等于截图采集验收或复杂文档精度评估。正式应用仍等待后续开发指令。
