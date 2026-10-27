import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Button, Card, Field, FluentProvider, MessageBar, MessageBarBody, Textarea, webDarkTheme, webLightTheme } from "@fluentui/react-components";
import { Link20Regular, WindowNew20Regular } from "@fluentui/react-icons";
import { visiblePage } from "./visibility";
import "./popup.css";

type Pair = {endpoint:string; token:string};
export function parsePair(text:string): Pair {
  const value = JSON.parse(text);
  if (!value || !/^http:\/\/127\.0\.0\.1:\d+$/.test(value.endpoint) || !/^[\w-]{40,100}$/.test(value.token)) throw new Error("配对格式无效");
  return {endpoint:value.endpoint,token:value.token};
}
async function post(pair:Pair, path:string, body:unknown) {
  const response = await fetch(pair.endpoint + path, {method:"POST",headers:{Authorization:`Bearer ${pair.token}`,"Content-Type":"application/json"},body:JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || value.error || "本地请求失败");
  return value;
}
export function Popup() {
  const [dark, setDark] = useState(() => matchMedia("(prefers-color-scheme: dark)").matches);
  const [pairText, setPairText] = useState(""), [working, setWorking] = useState(false);
  const [status, setStatus] = useState<{text:string; error:boolean} | null>(null);
  useEffect(() => {const media = matchMedia("(prefers-color-scheme: dark)"); const change = (event:MediaQueryListEvent) => setDark(event.matches); media.addEventListener("change",change); return () => media.removeEventListener("change",change);},[]);
  async function connect() {
    try {const pair = parsePair(pairText); await chrome.storage.session.set({pair}); setPairText(""); setStatus({text:"本次浏览器会话已配对。",error:false});}
    catch(error) {setStatus({text:(error as Error).message,error:true});}
  }
  async function capture() {
    if (working) return; setWorking(true);
    try {
      const {pair} = await chrome.storage.session.get("pair") as {pair?:Pair}; if (!pair) throw new Error("请先配对");
      const [tab] = await chrome.tabs.query({active:true,currentWindow:true});
      if (!tab || tab.incognito || !tab.url || tab.id === undefined || !/^https?:\/\//.test(tab.url)) throw new Error("仅支持非无痕 HTTP(S) 页面");
      setStatus({text:"正在核对前台与采集状态…",error:false});
      const ticket = await post(pair,"/api/extension/ticket",{url:tab.url,title:tab.title});
      const [injection] = await chrome.scripting.executeScript({target:{tabId:tab.id},func:visiblePage});
      const page = injection?.result; if (!page) throw new Error("无法读取当前页面");
      const captured_at = new Date().toISOString();
      const raw = await chrome.tabs.captureVisibleTab(tab.windowId,{format:"jpeg",quality:80});
      const image = new Image(); image.src = raw; await image.decode();
      const ratio = Math.min(1,3200/image.width,2400/image.height);
      const canvas = document.createElement("canvas"); canvas.width = Math.round(image.width*ratio); canvas.height = Math.round(image.height*ratio);
      const context = canvas.getContext("2d"); if (!context) throw new Error("无法处理截图");
      context.drawImage(image,0,0,canvas.width,canvas.height);
      const [current] = await chrome.tabs.query({active:true,currentWindow:true});
      if (!current || current.id!==tab.id || current.url!==page.url || current.title!==page.title || page.url!==tab.url || page.title!==tab.title) throw new Error("页面已切换，请重试");
      const result = await post(pair,"/api/extension/capture",{ticket:ticket.ticket,image:canvas.toDataURL("image/jpeg",.8),blocks:page.blocks,viewport:page.viewport,truncated:page.truncated,captured_at});
      setStatus({text:`已保存在本机。来源 ID：${result.id}${page.truncated ? "（文字已截断）" : ""}`,error:false});
    } catch(error) {setStatus({text:(error as Error).message,error:true});} finally {setWorking(false);}
  }
  return <FluentProvider theme={dark ? webDarkTheme : webLightTheme} className="popup" data-mode={dark ? "dark" : "light"}>
    <header className="popup-header"><img src="icons/48.png" alt="" /><div><strong>昔录 · Xilogue</strong><span>当前可见页面</span></div></header>
    <div className="popup-content"><h1>保存当前页面</h1><p>仅在点击时读取当前页的可见内容。</p>
      <Card className="popup-card"><Field label="本地界面生成的配对 JSON"><Textarea value={pairText} onChange={(_,d) => setPairText(d.value)} rows={3} spellCheck={false} placeholder="粘贴临时配对信息" /></Field><Button icon={<Link20Regular />} disabled={!pairText.trim() || working} onClick={() => void connect()}>保存本次会话配对</Button></Card>
      <Button appearance="primary" size="large" icon={<WindowNew20Regular />} disabled={working} onClick={() => void capture()}>保存可见区域与文字</Button>
      {status && <MessageBar intent={status.error ? "error" : "success"} role="status"><MessageBarBody>{status.text}</MessageBarBody></MessageBar>}
      <p className="popup-note">先在本地应用开始采集。表单、编辑区域、屏幕外文字和无痕窗口不采集，但截图可能包含敏感画面。DOM 文字和画面可能因动画存在差异，请回看原图核对。</p>
    </div>
  </FluentProvider>;
}
const root = document.getElementById("root"); if (root) createRoot(root).render(<Popup />);
