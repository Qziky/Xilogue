import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card, Field, Input, Select, Spinner } from "@fluentui/react-components";
import { Search20Regular } from "@fluentui/react-icons";
import { when } from "../api";
import { useUi } from "../context";
import type { Observation } from "../types";

type Filters = {q: string; app: string; after: string; before: string; status: string};
type Result = {results: Observation[]; next_cursor: string | null};
const initial: Filters = {q: "", app: "", after: "", before: "", status: ""};
const labels: Record<string,string> = {pending:"等待识别", running:"正在识别", done:"已完成", failed:"识别失败"};

export function Records() {
  const {api, notify, openRecord, recordsVersion} = useUi();
  const [form, setForm] = useState(initial), [filters, setFilters] = useState(initial);
  const [cursors, setCursors] = useState<string[]>([]), [data, setData] = useState<Result | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {setCursors([]);}, [recordsVersion]);
  const set = (key: keyof Filters, value: string) => setForm((previous) => ({...previous, [key]: value}));
  const search = useCallback(() => {
    if (form.after && form.before && new Date(form.after) >= new Date(form.before)) { notify("开始时间必须早于结束时间。", true); return; }
    setCursors([]); setFilters({...form});
  }, [form, notify]);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({limit: "30"});
    for (const [key, value] of Object.entries(filters)) {
      if (value) params.set(key, key === "after" || key === "before" ? new Date(value).toISOString() : value.trim());
    }
    if (cursors.length) params.set("cursor", cursors[cursors.length - 1]);
    setLoading(true);
    void api.request<Result>(`/api/observations?${params}`, {signal: controller.signal})
      .then(setData).catch((error: Error) => {if (!controller.signal.aborted) notify(error.message, true);})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [api, filters, cursors, recordsVersion, notify]);
  return <div className="page-stack">
    <div className="page-head"><div><div className="eyebrow">FIND YOUR MOMENTS</div><h1>记忆检索</h1><p className="lead">从保存在本机的记录中，找回当时的画面与文字。</p></div><Button disabled={loading} onClick={search}>刷新最新记录</Button></div>
    <Card className="xilogue-card"><form onSubmit={(event) => {event.preventDefault(); search();}} className="page-stack">
      <div className="search-line"><Field label="搜索文字"><Input value={form.q} onChange={(_, d) => set("q", d.value)} placeholder="搜索中文、英文或代码片段；留空浏览全部" maxLength={512} contentBefore={<Search20Regular />} /></Field><Button appearance="primary" type="submit" disabled={loading}>查找记录</Button></div>
      <div className="filters"><Field label="应用"><Input value={form.app} onChange={(_,d) => set("app", d.value)} placeholder="例如 notepad.exe" /></Field>
        <Field label="开始时间"><Input type="datetime-local" value={form.after} onChange={(_,d) => set("after", d.value)} /></Field>
        <Field label="结束时间"><Input type="datetime-local" value={form.before} onChange={(_,d) => set("before", d.value)} /></Field>
        <Field label="OCR 状态"><Select value={form.status} onChange={(_,d) => set("status", d.value)}><option value="">全部</option><option value="pending">等待识别</option><option value="running">正在识别</option><option value="done">已完成</option><option value="failed">识别失败</option></Select></Field></div>
    </form></Card>
    <div className="section-heading"><h2>{data ? `本页 ${data.results.length} 条原始记录` : "已保存的原始记录"}</h2><span className="caption">可查看原图、文字与来源</span></div>
    <div className="results" aria-busy={loading}>{loading && !data ? <Spinner label="正在查找" /> : data?.results.length ? data.results.map((row) =>
      <Card key={row.id} className="xilogue-card record-card" onClick={() => openRecord(row.id)} onKeyDown={(e) => {if (e.key === "Enter" || e.key === " ") {e.preventDefault(); openRecord(row.id);}}} role="button" tabIndex={0}>
        <div className="record-top"><span className="caption">{row.app_name} · {when(row.captured_at_utc)}</span><Badge appearance="tint">{labels[row.ocr_status || ""] || "尚无识别任务"}</Badge></div>
        <h3>{row.window_title || "未命名窗口"}</h3><p className="caption">{row.preview || "查看原图与识别文字"}</p>
      </Card>) : <Card className="xilogue-card empty-state">{filters.q ? "没有匹配文字。可清空关键词浏览尚未识别的原图。" : "此筛选下没有可用记录，可以调整应用、时间或 OCR 状态。"}</Card>}</div>
    <div className="pagination"><Button disabled={!cursors.length || loading} onClick={() => setCursors((previous) => previous.slice(0,-1))}>上一页</Button><span className="caption">第 {cursors.length + 1} 页</span><Button disabled={!data?.next_cursor || loading} onClick={() => data?.next_cursor && setCursors((previous) => [...previous, data.next_cursor!])}>下一页</Button></div>
  </div>;
}
