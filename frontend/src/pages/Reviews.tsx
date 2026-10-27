import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Card, Checkbox, Field, Input, Textarea } from "@fluentui/react-components";
import { useUi } from "../context";
import type { ReviewCard } from "../types";

type ReviewPage = {generated: boolean; cards: ReviewCard[]; total_visible: number; next_offset: number | null; empty_reason: string};
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
function yesterday() {const day = new Date(); day.setDate(day.getDate() - 1); return `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,"0")}-${String(day.getDate()).padStart(2,"0")}`;}
const feedback = [["useful","有用"],["useless","无用"],["later","稍后看"],["mastered","已掌握"],["never","不再推荐"],["neutral","清除反馈"]] as const;

export function Reviews() {
  const {api, notify, openRecord} = useUi();
  const [day, setDay] = useState(yesterday), [hidden, setHidden] = useState(false), [data, setData] = useState<ReviewPage | null>(null);
  const [working, setWorking] = useState(false), [notes, setNotes] = useState<Record<string,string>>({});
  const generation = useRef(0);
  const load = useCallback(async (offset = 0) => {
    const current = ++generation.current;
    try {
      const params = new URLSearchParams({day, timezone, include_hidden:String(hidden), offset:String(offset)});
      const result = await api.request<ReviewPage>(`/api/reviews?${params}`);
      if (current !== generation.current) return;
      setData((previous) => offset && previous ? {...result, cards:[...previous.cards, ...result.cards]} : result);
      setNotes((previous) => Object.fromEntries(result.cards.map((card) => [card.session_id, previous[card.session_id] ?? card.correction])));
    } catch (error) {if (current === generation.current) notify((error as Error).message, true);}
  }, [api, day, hidden, notify]);
  useEffect(() => {void load(); return () => {generation.current++;};}, [load]);
  async function generate() {if (!day || working) return; setWorking(true); try {await api.request("/api/reviews", {body:{day,timezone}}); await load();} catch (error) {notify((error as Error).message,true);} finally {setWorking(false);}}
  async function send(id: string, change: Record<string, unknown>) {try {await api.request("/api/reviews/feedback", {body:{session_id:id,...change}}); await load();} catch (error) {notify((error as Error).message,true);}}
  return <div className="page-stack"><div className="page-head"><div><div className="eyebrow">RETURN TO WHAT MATTERS</div><h1>每日回顾</h1><p className="lead">从有原文引用的片段中挑选，决定哪些内容值得再看。</p></div><Button appearance="primary" disabled={working || !day} onClick={() => void generate()}>生成 / 刷新回顾</Button></div>
    <Card className="xilogue-card"><div className="filters"><Field label="回顾日期"><Input type="date" value={day} onChange={(_,d) => setDay(d.value)} /></Field><Field label="本地时区"><Input value={timezone} readOnly /></Field><Checkbox label="包含隐藏与已掌握" checked={hidden} onChange={(_,d) => setHidden(d.checked === true)} /></div></Card>
    <p className="caption" role="status">{data?.generated ? `本地日界 · ${timezone} · ${data.total_visible || 0} 条回顾。排序采用模型重要度与置信度，不推测学习成果。` : "这一天尚未生成回顾。请先完成 OCR 和活动摘要；没有内容时不会补造摘要。"}</p>
    <div className="results">{data?.cards.length ? data.cards.map((card) => <Card className="xilogue-card" key={card.session_id}><h2>{card.topics.join(" / ") || "当天可见内容"}</h2><p>{card.summary}</p><p className="caption">{card.reason}</p><div className="review-actions">{feedback.map(([value,label]) => <Button key={value} appearance={card.feedback === value ? "primary" : "secondary"} aria-pressed={card.feedback === value} onClick={() => void send(card.session_id,{kind:value,hidden:false})}>{label}</Button>)}<Button onClick={() => void send(card.session_id,{hidden:!card.hidden})}>{card.hidden ? "取消隐藏" : "隐藏"}</Button></div>
      <Field label="修正这条回顾（作为用户注释保留）"><Textarea value={notes[card.session_id] ?? card.correction} maxLength={2000} rows={2} onChange={(_,d) => setNotes((previous) => ({...previous,[card.session_id]:d.value}))} /></Field><div><Button onClick={() => void send(card.session_id,{correction:notes[card.session_id] ?? card.correction})}>保存修正</Button></div>
      {card.claims.map((claim,i) => <div key={i}><Button appearance="subtle" onClick={() => openRecord(claim.source_id)}>回到原文：{claim.quote}</Button></div>)}
    </Card>) : <Card className="xilogue-card empty-state">{data?.empty_reason || "没有可用的回顾内容。"}</Card>}</div>
    {data?.next_offset !== null && data?.next_offset !== undefined && <Button onClick={() => void load(data.next_offset!)}>更多回顾</Button>}
  </div>;
}
