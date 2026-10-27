# 数据模型与本地存储契约

状态：Observation、OCR、活动片段及本地 Insight 已落地。2026-10-27。

## 三类记忆

- Observation：一次被保留的原始窗口观察，承载图像和元数据。
- Session：由规则聚合的连续或相关活动，保留 Observation 关系。
- Insight：由一个或多个来源生成的知识或发现，必须保留证据关系。

SQLite 文件为 memory.sqlite3，当前 PRAGMA user_version=8；正式旧库启动时在单一事务内升级，失败回滚 schema 与版本。observations 保留下表字段及 storage_state（ready/deleting/missing）。普通读取只返回 ready；待删除可重试，缺图记录不冒充完整证据。不自动打开或迁移历史实验数据，经验见 [原型经验](../references/prototype-findings.md)。

## P0 Observation

| 字段 | 已实现含义与约束 |
| --- | --- |
| id | 稳定唯一 ID；不能依赖 HWND |
| captured_at_utc | 帧实际观察时间，带时区 UTC；不能以稍后的保存时间冒充 |
| created_at_utc | 记录建立时间，带时区 UTC |
| source_type | P0 为 desktop_capture |
| app_name | 进程名，按统一方式规范化；进程无法确认时拒绝采集 |
| process_id | 当时 PID；仅用于上下文，不能作为长期身份 |
| window_title | 当时标题，可能含私人信息；不得用于文件名或默认持久日志 |
| capture_backend | 实际后端标识及可追溯版本信息 |
| image_path | 相对于应用数据目录的受控路径，不依赖工作目录 |
| image_bytes | 图片文件字节数，用于图像配额 |
| width / height | 保存图像的实际尺寸 |
| capture_reason | 保存原因 |
| visual_metrics_json | MAE、changed_ratio、max_tile_mae 及参数版本/引用 |

标题缺失或无法取得窗口上下文时保守跳过；标题变化不能单独作为窗口身份变化。HWND 与 generation 是运行时有效性标识，无需当作长期主键。

将像素格式和真实捕获时间保留在候选帧中；如底层只能提供相对时钟，必须说明其与 UTC 的映射。图像哈希、进程路径、OCR 与 Session 字段按实际阶段再增加，不要求 P0 先建所有后续表。

时间统一保存 UTC，UI 转换到用户本地时区。每日回顾的 review_date 按用户本地日界计算，并保存或可还原时区，避免 UTC 日期错分“昨日”。

## 文件、事务与恢复

图片独立存为 WebP，SQLite 保存路径和元数据，不存大块图像二进制。文件名使用时间与随机 ID，不含窗口标题。

推荐提交流程：写同目录临时文件 → 完成文件替换 → 写入并提交数据库 → 确认成功后更新比较基线。任何阶段失败都要补偿清理或留待恢复。原子替换只能保证单文件可见性，不能把文件和数据库变成同一个事务。

P0 必须覆盖：

- 文件写入失败与数据库提交失败，不留下被当作成功的记录。
- 进程中断后的临时文件、孤儿图片、缺失图片记录的核对与恢复。
- 删除或清理中途失败后的可重试状态；不能先删文件、事务回滚后仍留下貌似完整的记录。
- 超过图像配额时按最旧记录回收；单张图超过配额应拒绝并解释，不返回已经被清理掉的成功路径。
- 启动和运行期间检查过期记录，不依赖“下一张新图保存”才清理。用户手动删除不受采集暂停限制。
- 图片、数据库、临时文件分别统计；图像配额不等于整个数据目录的硬上限。

数据目录与初始额度见 [决策记录](decisions.md)。根目录只放配置样例；真实配置与数据不提交版本控制。P0 建立 schema 版本记录，后续迁移失败不能破坏旧数据。

## 后续增量模型

P2 已建立 sessions 与 session_items。按应用、标题相似度、连续观察间隔及跨度规则增量分组；未分配来源本身是可恢复的工作清单，每次最多处理 2000 条。默认间隔 300 秒、跨度 3600 秒、标题相似度 0.65，可在配置高级字段调整；参数仅影响后续分配，可重建自动分组。片段代表图为最新来源，手动拆分/合并后保持固定成员，不将观察跨度称为工作时长。

来源失效/删除在同一事务内删除其整个 Session，使未来 AI、回顾等派生数据能级联清除；剩余有效来源随后重新分组，删除可能使手动分组需要重新调整。OCR 更新增加 Session revision，用于拒绝过期模型结果。读取时核对原图，缺图不展示旧片段元数据。手动拆分/合并事务化，失败回滚；均要求停止/暂停。Session 来源详情分页，不复制 OCR 全文到分组表。

P1 已建立 `ocr_results`（Observation 主键、原文、NFKC/casefold 检索副本、逐行文字/四点像素坐标/置信度 JSON、引擎版本、UTC 创建时间）和 `processing_jobs`（Observation 主键、pending/running/done/failed、attempts、领取 token、下次重试/更新时间、脱敏错误代码）。当前只有 OCR 一种任务，未提前建立 Session 或 AI 空壳。

保存 Observation 的同一数据库事务通过触发器建立待处理任务；迁移时回填现有 ready 来源。单实例重启将 running 任务退回 pending，更新领取 token 防止旧结果提交；暂停取消不消耗失败重试次数，处理异常最多自动尝试 3 次，允许用户显式重试。OCR 结果、FTS5 trigram 索引及 done 状态同一事务提交，写入失败整体回滚。

来源变为 deleting/missing 时，在标记事务内清除文字、任务与索引；物理删除通过外键级联清理。领取中的旧 Worker 必须匹配 token 和 ready 来源才能提交，不复活被删内容。原图不在时搜索和详情均不返回该来源。

检索按字面子串，三字及以上由 FTS5 trigram 缩小候选，一/二字使用规范化文本扫描以覆盖中文短词；SQL 参数绑定且 MATCH 输入转义为字面短语，不解释用户布尔表达式。可按应用及含时区的 UTC 时间边界筛选；返回有界原文片段，详情保留完整原文和坐标。短词大库性能待实测。

原始记录浏览不要求 OCR 完成，支持应用、时间及 OCR 状态筛选；关键词仍只匹配 OCR 原文。按捕获时间与 ID 倒序，以二者组成的游标翻页，每页最多 100 条；新增记录需刷新回第一页，删除前页记录不挤动后页偏移。缺图来源不展示，列表不含磁盘路径。新增复合索引，schema 仍为 2。可按单个 ID 重试失败任务；手动删除复用 deleting 标记与级联清理，物理清理失败返回待清理状态并由维护重试，不把索引失效等同于文件已删除。

| 阶段 | 新增内容 |
| --- | --- |
| P1 | OCR 文本、行/区域坐标、状态与错误；关键词索引；可恢复 processing_jobs |
| P2 | sessions 的起止时间、代表 Observation、标题/应用信息、聚合状态及成员关系 |
| P3 | insights 的摘要、主题、重要度、置信度、模型与提示版本；insight_sources |
| P4 | daily_reviews 的本地日期、时区、状态、摘要、来源与用户反馈 |
| P5 | 经验证需要的检索索引或向量；来源关联与版本 |

Insight 来源关系至少引用一个真实 Session 或 Observation；JSON 中的 ID 要验证存在。OCR 文本变换保留原文及对应截图位置；修改和删除文字同样是增量变化，不能只保存新增行。

后续多种任务可扩展 job_type/entity_id，当前采用 Observation 主键约束确保每个来源只有一个 OCR 任务。任务领取、重试、重启恢复和删除取消保持幂等语义。

## 删除传播

P4 的 daily_reviews/review_cards/review_feedback 连接有效 Insight 和 Session；P5 的 session_vectors/embedding_jobs 记录修订、模型摘要、维度及文字摘要，修订和源删除传播失效；P6 browser_pages 连接浏览器 Observation，URL/标题/截断/视口信息与图片、DOM 原文在同一事务保存。DOM 原文复用 ocr_results/FTS，任务记为完成，不重复运行 OCR。模型权重和备份 ZIP 不属于 SQLite 内容。

P3 analysis_jobs 保留 Session revision、状态、attempts、领取 token、下次重试与脱敏错误；insights 保存有界 JSON、模型、revision 与创建时间，insight_sources 外键引用真实来源。Session revision 变化会清除 Insight 并使旧任务 token 失效；整个 Session 删除级联清除任务和结果。新的分析只能引用当次输入中存在且原文匹配的来源，不能接受模型自造 ID。规则与运行说明见 [本地分析](../development/analysis.md)。

源记录删除必须使 OCR、搜索索引、任务、摘要、回顾和引用关系同步失效或清理。多来源 Insight 不能仅去掉一条关系就继续展示仍包含被删来源的信息；应失效后基于剩余证据重新生成。

删除后仍在运行的 Worker 不得重新写回被删内容。停止、任务取消和删除屏障应一起验证。普通文件删除不构成安全擦除承诺，详见 [隐私规格](privacy.md)。
