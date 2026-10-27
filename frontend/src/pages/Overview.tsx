import { Badge, Button, Card, Image, MessageBar, MessageBarBody, Spinner, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow } from "@fluentui/react-components";
import { ArrowClockwise20Regular, Database20Regular, DocumentText20Regular, Image20Regular, Pause20Regular, Play20Filled, Power20Regular, Window24Regular } from "@fluentui/react-icons";
import { size, when } from "../api";
import { useUi } from "../context";
import type { Status } from "../types";
import { useImage } from "../useImage";

const reasons: Record<string, string> = {
  not_started:"等待主动开始", starting:"准备采集目标", paused:"已暂停", pausing:"等待停止屏障",
  config_reloaded:"配置已重新加载", idle:"输入空闲，暂缓新采集", excluded:"已排除的应用",
  desktop_unavailable:"桌面不可用", target_invalid:"暂无有效目标", target_unknown:"目标信息不完整",
  target_restricted:"目标受到保护", target_changed:"前台目标已变化", capturing:"等待有效变化帧",
  near_duplicate:"近似重复，跳过保存", first_frame:"首帧", local_change:"局部变化",
  large_change:"大范围变化", global_change:"整体变化", dimensions_changed:"尺寸变化",
  queue_full:"处理队列已满", suspected_black_frame:"疑似黑帧，未保存", candidate_invalidated:"旧候选已失效",
  ocr_only_started:"仅识别已保存图片", ocr_pass_finished:"本轮 OCR 已结束",
  analysis_only_started:"仅分析已保存片段", analysis_pass_finished:"本轮分析已结束",
  index_only_started:"仅建立本地向量", index_pass_finished:"本轮索引已结束",
};
const kinds: Record<string, string> = {state:"状态",save:"保存",skip:"跳过",error:"错误",capture:"捕获",config:"配置"};

export function Overview({events}: {events: Status["events"]}) {
  const {api, status, connected, busy, control, openRecord} = useUi();
  const image = useImage(api, status?.latest?.id);
  const state = status?.state || "stopped", editable = state === "stopped" || state === "paused";
  const ocr = status?.ocr, jobs = ocr?.jobs || {}, analysis = status?.analysis;
  const counts = status?.counts || {}, mode = status?.mode;
  const ocrOnly = state === "running" && mode === "ocr_only";
  const analysisOnly = state === "running" && (mode === "analysis_only" || mode === "index_only");
  const modelActive = !!analysis?.provider_active || !!status?.model_queries;
  return <div className="page-stack">
    <div className="page-head"><div><div className="eyebrow">YOUR LOCAL MEMORY</div><h1>采集概览</h1><p className="lead">采集与存储留在本机，由你决定何时开始及是否使用 API 分析。</p></div>
      <div className="actions"><Button appearance="secondary" icon={<Pause20Regular />} disabled={!connected || state !== "running"} onClick={() => void control("pause")}>暂停</Button>
        <Button appearance="primary" icon={<Play20Filled />} disabled={!connected || !editable || busy || !!status?.config_error} onClick={() => void control("start")}>{state === "paused" && mode === "capture" ? "恢复采集" : "开始采集"}</Button>
        <Button appearance="subtle" icon={<Power20Regular />} disabled={!connected || state === "closing" || state === "closed"} onClick={() => void control("exit")}>退出程序</Button></div></div>
    <div className="stats-grid">
      <Card className="xilogue-card stat-card"><div className="stat-head">已保存观察 <Image20Regular className="stat-icon" /></div><div className="stat-value">{status?.storage.observations ?? "—"}</div><div className="caption">本次新增 {counts.saved || 0} 条 · 本地观察总量</div></Card>
      <Card className="xilogue-card stat-card"><div className="stat-head">待处理 OCR <DocumentText20Regular className="stat-icon" /></div><div className="stat-value">{status ? (jobs.pending || 0) + (jobs.running || 0) : "—"}</div><div className="caption">已完成 {jobs.done || 0} · 失败 {jobs.failed || 0}</div></Card>
      <Card className="xilogue-card stat-card"><div className="stat-head">本地图片存储 <Database20Regular className="stat-icon" /></div><div className="stat-value">{status ? size(status.storage.image_bytes) : "—"}</div><div className="caption">数据库 {size(status?.storage.database_bytes)} · 临时 {size(status?.storage.temporary_bytes)}</div></Card>
    </div>
    <div className="overview-grid">
      <Card className="xilogue-card"><div className="card-heading"><h2>最近的观察</h2><span className="caption">{when(status?.latest?.captured_at_utc)}</span></div>
        <div className="preview">{image.url ? <Image src={image.url} alt="最近保存的窗口原图" fit="contain" /> : image.error ? <p className="empty-state">最近的图片不可用，可能已删除或文件缺失。</p> : status?.latest?.id ? <Spinner label="正在读取图片" /> : <div className="empty-state"><Window24Regular /><strong>一切由你开始</strong><span>开始采集后，记录前台窗口中值得保留的变化。</span></div>}</div>
        <div className="preview-foot"><span className="caption">{reasons[status?.reason || ""] || status?.reason || "等待主动开始"}</span><Button appearance="subtle" disabled={!status?.latest?.id} onClick={() => status?.latest && openRecord(status.latest.id)}>查看文字与原图 →</Button></div></Card>
      <Card className="xilogue-card"><div className="card-heading"><h2>运行状态</h2><Badge appearance="tint" color="informative">实时</Badge></div>
        <dl className="details-list">
          <div><dt>当前应用</dt><dd>{ocrOnly || analysisOnly ? "不采集新窗口" : status?.target?.app_name || "—"}</dd></div>
          <div><dt>窗口标题</dt><dd>{ocrOnly || analysisOnly ? "仅处理已保存内容" : status?.target?.title || "暂无采集目标"}</dd></div>
          <div><dt>处理队列</dt><dd>{status?.queue ?? 0}</dd></div>
          <div><dt>OCR 状态</dt><dd>{({stopped:"尚未处理 / 已停止",waiting:"等待任务",processing:"正在识别",error:"处理异常",closed:"已退出"} as Record<string,string>)[ocr?.state || ""] || "未启用"}</dd></div>
          <div><dt>保存 / 跳过 / 丢弃</dt><dd>{counts.saved || 0} / {counts.skipped || 0} / {(counts.queue_full || 0) + (counts.invalidated || 0)}</dd></div>
          <div><dt>平均耗时</dt><dd>{status?.timings.total ? `${status.timings.total.mean_ms.toFixed(1)} ms` : "—"}</dd></div>
          <div><dt>最后错误</dt><dd>{analysis?.cleanup_error || analysis?.last_error || status?.last_error || status?.config_error || "无"}</dd></div>
        </dl>
        <p className="caption">模型：{modelActive ? "使用中 / 待释放" : "无活动会话"}{analysis?.cleanup_error || status?.model_cleanup_pending ? " · 释放尚未确认" : ""} · 分析待处理 {analysis?.jobs?.pending || 0}</p>
        <p className="caption">摘要服务：{status?.ai_destination || "本机 Ollama"}{status?.ai_provider === "openai_compatible" ? " · 仅按已保存授权发送文字" : ""}</p>
        <div className="caption">变化指标：{Object.entries(status?.metrics || {}).filter(([,v]) => typeof v === "number").map(([k,v]) => `${k}=${v.toFixed(3)}`).join(" · ") || "—"}</div>
        <div className="actions-vertical"><Button disabled={!connected || !editable || busy || !ocr || !!status?.config_error} onClick={() => void control("process-ocr")}>仅识别已保存图片</Button><Button icon={<ArrowClockwise20Regular />} disabled={!connected || !editable || busy} onClick={() => void control("retry-ocr")}>重试失败的 OCR 任务</Button></div>
        <p className="caption">{ocrOnly ? "仅识别本地图片，批次结束后自动暂停。" : ocr?.model_loaded && (state === "paused" || state === "pausing") ? "旧结果已被阻止提交；推理结束后释放会话。" : ocr?.model_loaded ? "OCR 会话已加载。" : "OCR 会话未加载，可单独处理本地图片。"}</p>
      </Card>
    </div>
    <Card className="xilogue-card"><div className="card-heading"><h2>最近事件</h2><span className="caption">有界状态与原因</span></div><div className="event-table"><Table size="small" aria-label="最近事件"><TableHeader><TableRow><TableHeaderCell>时间</TableHeaderCell><TableHeaderCell>类型</TableHeaderCell><TableHeaderCell>原因 / 结果</TableHeaderCell></TableRow></TableHeader><TableBody>{events.length ? events.map((event) => <TableRow key={event.seq}><TableCell>{when(event.at)}</TableCell><TableCell>{kinds[event.kind] || event.kind}</TableCell><TableCell>{reasons[event.reason] || event.reason}</TableCell></TableRow>) : <TableRow><TableCell colSpan={3}>还没有运行事件</TableCell></TableRow>}</TableBody></Table></div></Card>
    <MessageBar intent="warning"><MessageBarBody>仅采集前台窗口。浏览器内的敏感页面无法完全自动排除，请在打开私人内容前暂停并等到“已暂停”。默认无输入 90 秒暂缓采集；长时间阅读与视频可能漏采。</MessageBarBody></MessageBar>
  </div>;
}
