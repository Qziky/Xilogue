import { useCallback, useEffect, useState } from "react";
import { Button, Card, Checkbox, Field, Input } from "@fluentui/react-components";
import { useUi } from "../context";
import type { Related } from "../types";

type Topic = {topic: string; days: string[]; sessions: Array<{id:string; day:string; title:string}>};
type RelatedPage = {results: Related[]; scanned_sessions:number; scan_limit:number; notice:string | null};
export function Topics({onSession}: {onSession: (id:string) => void}) {
  const {api, status, connected, busy, notify, refresh, openRecord} = useUi();
  const [query, setQuery] = useState(""), [semantic, setSemantic] = useState(false);
  const [result, setResult] = useState<RelatedPage | null>(null), [topics, setTopics] = useState<Topic[]>([]), [working, setWorking] = useState(false);
  const editable = connected && (status?.state === "paused" || status?.state === "stopped") && !busy && !working;
  const loadTopics = useCallback(async () => {try {const params = new URLSearchParams({timezone:Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"}); const data = await api.request<{topics:Topic[]}>(`/api/topics?${params}`); setTopics(data.topics);} catch (error) {notify((error as Error).message,true);}}, [api,notify]);
  useEffect(() => {void loadTopics();}, [loadTopics]);
  async function search() {if (!query.trim() || working) return; setWorking(true); try {const params = new URLSearchParams({q:query.trim(),semantic:String(semantic)}); setResult(await api.request<RelatedPage>(`/api/related?${params}`));} catch(error) {notify((error as Error).message,true);} finally {setWorking(false);}}
  async function index() {if (!editable) return; setWorking(true); try {const data = await api.request<{queued:number}>("/api/embeddings",{body:{}}); notify(`已排入 ${data.queued} 段向量任务，按已保存的提供商设置处理，不采集窗口。`); await refresh();} catch(error) {notify((error as Error).message,true);} finally {setWorking(false);}}
  const jobs = status?.analysis?.embeddings || {};
  return <div className="page-stack"><div className="page-head"><div><div className="eyebrow">CONNECT WHAT YOU SAW</div><h1>相关检索与主题</h1><p className="lead">查看匹配依据与原始证据，跨日主题由有效摘要关联。</p></div><Button disabled={!editable || !status?.analysis} onClick={() => void index()}>建立 / 重建向量索引</Button></div>
    <Card className="xilogue-card"><form className="page-stack" onSubmit={(event) => {event.preventDefault(); void search();}}><Field label="想找什么？"><Input value={query} onChange={(_,d) => setQuery(d.value)} maxLength={400} required placeholder="例如 数据库恢复、窗口变化或代码采样" /></Field><div className="actions"><Checkbox label="使用向量检索（按已保存的提供商设置）" checked={semantic} onChange={(_,d) => setSemantic(d.checked === true)} /><Button appearance="primary" type="submit" disabled={working}>查找相关证据</Button></div><span className="caption">向量任务：待处理 {jobs.pending || 0} · 运行 {jobs.running || 0} · 完成 {jobs.done || 0} · 失败 {jobs.failed || 0}。文字检索不依赖模型；向量不可用时回退相关词匹配。</span></form></Card>
    {result && <><p className="caption" role="status">{result.results.length} 条相关片段 · 本轮扫描 {result.scanned_sessions} / 最多 {result.scan_limit} 段{result.notice ? ` · 向量未使用：${result.notice}，已回退相关词。` : ""}</p><div className="results">{result.results.length ? result.results.map((row) => <Card className="xilogue-card" key={row.id}><h2>{row.title}</h2><p className="caption">{row.app_name} · 匹配词：{row.matched_terms.join(" / ") || "无字面词"}{row.cosine === null ? " · 相关词匹配" : ` · 向量余弦 ${row.cosine.toFixed(3)}`}。相似不代表事实成立。</p><div className="actions"><Button onClick={() => openRecord(row.representative_id)}>查看原图证据</Button><Button appearance="subtle" onClick={() => onSession(row.id)}>查看活动片段</Button></div></Card>) : <Card className="xilogue-card empty-state">没有可回源的相关内容，可以缩短关键词。</Card>}</div></>}
    <div className="section-heading"><h2>跨日主题</h2><Button appearance="subtle" onClick={() => void loadTopics()}>刷新主题</Button></div><div className="results">{topics.length ? topics.map((group) => <Card className="xilogue-card" key={group.topic}><h2>{group.topic}</h2><p className="caption">{group.days.length} 个本地日期 · {group.days.join(" / ")} · 相同主题标签不代表用户已掌握。</p>{group.sessions.map((source) => <div key={source.id}><Button appearance="subtle" onClick={() => onSession(source.id)}>{source.day} · {source.title}</Button></div>)}</Card>) : <Card className="xilogue-card empty-state">暂无有效主题，完成本地摘要后可在此关联来源。</Card>}</div>
  </div>;
}
