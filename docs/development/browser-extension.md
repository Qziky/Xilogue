# 浏览器主动采集（P6）

设置页下载 ZIP 并解压，在 `edge://extensions` 或 `chrome://extensions` 开启开发者模式，加载解压缩目录。暂停本地应用，复制扩展 ID（32 位 a–p 字符）到设置页生成配对，将 JSON 复制到扩展弹窗。配对使用独立令牌、精确扩展 Origin、回环 Host，1 小时有效；只存于扩展的浏览器会话存储，重启应用/浏览器后重新配对。设置页可立即撤销，不将主 UI 令牌交给扩展。

弹窗采用 Fluent UI React v9 和项目图标，默认跟随系统明暗模式；资源均随扩展 ZIP 提供。更换弹窗界面没有增加常驻脚本或浏览器权限。弹窗使用浏览器的 Manifest V3 默认扩展页面 CSP，不声明 `unsafe-inline` 或 `unsafe-eval`；逐次生成的样式 nonce 用于由本地服务响应的主界面。

手动开始采集后，在非无痕 HTTP(S) 页面点击扩展“保存可见区域与文字”。服务端要求同一前台浏览器进程/窗口身份、标题、采集 generation、桌面状态；20 秒单次票据，最多 8 个，每秒最多保存一项。开始后需等待窗口监控稳定再点击。扩展没有常驻脚本、后台采集、全站访问或历史记录权限。无需扩展即可使用原生采集。

保存当前视口截图、URL、标题、可见 DOM 文字和当前显示的视频字幕线索，文字和截图在同一事务入库，直接进入原文检索。DOM 文字不包含表单/编辑区域、隐藏元素、屏幕外文本；跨域 iframe、canvas 文本、受保护视频等可能缺失，超过时间/长度预算标记截断。字幕仅 activeCues，不下载完整字幕。DOM 区域的置信度 1 表示几何来源，不表示 OCR 准确率。截图**仍包含视口中所有可见内容**，表单和私人内容需要用户主动避免；不能承诺跨动画、网页覆盖层和 DOM 到截图之间完全同步。

服务端过滤常见 token/secret/password/auth/key 查询参数、片段和 URL 凭据，仍不能自动识别所有敏感路径/查询值。仅窗口标题及前台检查不能证明某个标签页的身份，配对扩展属于本地可信组件。原生与扩展记录各保留独立原图；仅把同进程、应用、标题且 10 秒内的原生记录标为“相近桌面记录”，这是待核对的关联，不证明标签页身份或把原生图片改称已验证 URL。删除原生记录只清除关联，独立浏览器证据仍有效；删除各来源传播至文字、活动、摘要、回顾与索引。

自动化覆盖跨 Origin、主令牌隔离、CORS、停止拒绝、票据重放、暂停竞态、事务失败与删除传播。真实 Edge/Chrome 安装、字幕、DPI、多窗口、受保护页面和浏览器版本兼容性待用户安装后验证；没有自动安装扩展或读取现有标签页。

DOM 提取按祖先样式排除透明/隐藏节点，并要求字符中心命中自身文本父元素，避免把被裁剪或覆盖的文本误当可见；这会保守漏掉复杂布局中的部分文字。字幕同样要求视频可见且未被覆盖。像素截图仍是回源依据，复杂页面可见性不是已通过的完整浏览器兼容声明。

官方参考：[activeTab](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab)、[截图 API](https://developer.chrome.com/docs/extensions/reference/api/tabs#method-captureVisibleTab)、[跨源请求](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)、[无痕模式](https://developer.chrome.com/docs/extensions/reference/manifest/incognito)。
