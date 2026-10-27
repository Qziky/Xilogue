import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Card, Checkbox, Spinner } from "@fluentui/react-components";
import { when } from "../api";
import { useUi } from "../context";
import type { Session } from "../types";

type Page = {results: Session[]; next_cursor: string | null; grouping: {batch_full: boolean}};
export function Sessions({focusId, clearFocus}: {focusId: string | null; clearFocus: () => void}) {
  const {api, status, connected, busy, notify, refresh, openRecord} = useUi();
  const [rows, setRows] = useState<Session[]>([]), [cursor, setCursor] = useState<string | null>(null);
  const [note, setNote] = useState(""), [selected, setSelected] = useState<string[]>([]);
  const [detail, setDetail] = useState<Session | null>(null), [working, setWorking] = useState(false);
  const generation = useRef(0), detailGeneration = useRef(0), activeId = useRef<string | null>(null);
  const editable = connected && (status?.state === "stopped" || status?.state === "paused") && !busy && !working;
  const load = useCallback(async (before?: string) => {
    const current = ++generation.current;
    try {
      const params = new URLSearchParams({limit: "30"}); if (before) params.set("before", before);
      const data = await api.request<Page>(`/api/sessions?${params}`);
      if (current !== generation.current) return;
      setRows((previous) => before ? [...previous, ...data.results] : data.results); setCursor(data.next_cursor);
      if (!before) setSelected([]);
      setNote(data.grouping.batch_full ? "仍有来源等待分组，可再次刷新继续。" : "观察跨度不代表阅读或工作时长。停止或暂停后可拆分、合并。");
    } catch (error) {if (current === generation.current) notify((error as Error).message, true);}
  }, [api, notify]);
  const open = useCallback(async (id: string, offset = 0) => {
    activeId.current = id; const current = ++detailGeneration.current;
    try {
      const data = await api.request<Session>(`/api/sessions/${encodeURIComponent(id)}?offset=${offset}`);
      if (current !== detailGeneration.current || activeId.current !== id) return;
      setDetail((previous) => offset && previous?.id === id ? {...data, sources: [...previous.sources, ...data.sources]} : data);
    } catch (error) {if (current === detailGeneration.current) notify((error as Error).message, true);}
  }, [api, notify]);
  useEffect(() => {void load(); return () => {generation.current++; detailGeneration.current++;};}, [load]);
  useEffect(() => {if (focusId) {void open(focusId); clearFocus();}}, [focusId, clearFocus, open]);
  async function mutate(body: Record<string, unknown>) {
    if (!editable) return;
    setWorking(true);
    try {
      const result = await api.request<{queued?: number; skipped?: number}>("/api/sessions/actions", {body});
      activeId.current = null; detailGeneration.current++; setDetail(null); await load(); await refresh();
      notify(body.action === "analyze" ? `已排入 ${result.queued || 0} 段；跳过 ${result.skipped || 0} 段无文字来源。仅整理本地证据。` : "片段已更新，相关旧分析会随来源关系失效。");
    } catch (error) {notify((error as Error).message, true);} finally {setWorking(false);}
  }
  return <div className="page-stack"><div className="page-head"><div><div className="eyebrow">FOLLOW THE EVIDENCE</div><h1>活动片段</h1><p className="lead">按应用和时间聚合观察，随时回到原始来源。</p></div><div className="actions"><Button onClick={() => void load()}>刷新片段</Button><Button disabled={!editable || selected.length < 2 || selected.length > 20} onClick={() => void mutate({action:"merge", ids:selected})}>合并选中片段</Button><Button appearance="primary" disabled={!editable || !status?.analysis} onClick={() => void mutate({action:"analyze"})}>生成缺失的本地摘要</Button></div></div>
    <p className="caption" role="status">{note}</p>
    {detail && <Card className="xilogue-card session-card"><div className="card-heading"><h2>{detail.title}</h2><Button appearance="subtle" onClick={() => {activeId.current = null; detailGeneration.current++; setDetail(null);}}>收起详情</Button></div>
      <p className="caption">{detail.source_count} 条原始来源 · {detail.app_name} · 拆分点之前和之后分别保留为两个片段。</p>
      {detail.insight ? <><p className="caption">本地 AI 草稿 · {detail.insight.model} · 引文和来源通过校验，推论仍需核对。</p><p>{detail.insight.payload.summary}</p><p className="caption">主题：{detail.insight.payload.topics.join(" / ")} · 关键词：{detail.insight.payload.keywords.join(" / ")} · 模型重要度 {detail.insight.payload.importance.toFixed(2)} / 置信度 {detail.insight.payload.confidence.toFixed(2)}</p>
        {detail.insight.payload.claims.map((claim, i) => <div className="source-row" key={i}><span>{claim.text}（原文：{claim.quote}）</span><Button onClick={() => openRecord(claim.source_id)}>查看证据</Button></div>)}
        {!!detail.insight.payload.uncertainties.length && <p className="caption">不确定：{detail.insight.payload.uncertainties.join("；")}</p>}</> : <p className="caption">尚无有效摘要。完成 OCR 并暂停后，可明确发起本地分析。</p>}
      <div><Button disabled={!editable || !status?.analysis} onClick={() => void mutate({action:"analyze",ids:[detail.id]})}>生成 / 重新生成这段摘要</Button></div>
      <div className="source-list">{detail.sources.map((source, index) => <div className="source-row" key={source.id}><Button appearance="subtle" onClick={() => openRecord(source.id)}>{when(source.captured_at_utc)} · {source.window_title}</Button>{index > 0 && <Button disabled={!editable} onClick={() => void mutate({action:"split",id:detail.id,before_id:source.id})}>在此之前拆分</Button>}</div>)}</div>
      {detail.next_offset !== null && <Button onClick={() => void open(detail.id, detail.next_offset!)}>更多来源</Button>}</Card>}
    <div className="results">{rows.length ? rows.map((row) => <Card className="xilogue-card session-card" key={row.id}>
      <Checkbox checked={selected.includes(row.id)} onChange={(_, d) => setSelected((previous) => d.checked ? [...previous, row.id] : previous.filter((id) => id !== row.id))} label={row.title || "未命名片段"} />
      <p className="caption">{row.app_name} · {when(row.started_at_utc)} — {when(row.ended_at_utc)} · {row.source_count} 条来源{row.manual ? " · 手动分组" : ""}</p><div className="actions"><Button onClick={() => void open(row.id)}>查看来源</Button><Button appearance="subtle" disabled={!row.representative_id} onClick={() => row.representative_id && openRecord(row.representative_id)}>查看代表图</Button></div>
    </Card>) : !note ? <Spinner label="正在读取活动片段" /> : <Card className="xilogue-card empty-state">还没有可用活动片段。保存来源后可在这里查看。</Card>}</div>
    {cursor && <Button onClick={() => void load(cursor)}>更多片段</Button>}
  </div>;
}
