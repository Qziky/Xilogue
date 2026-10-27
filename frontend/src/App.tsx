import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Badge, Button, FluentProvider, Menu, MenuItemRadio, MenuList, MenuPopover, MenuTrigger,
  MessageBar, MessageBarBody, Tab, TabList, webDarkTheme, webLightTheme,
} from "@fluentui/react-components";
import {
  CalendarLtr24Regular, Color24Regular, Dismiss20Regular, Home24Regular,
  Search24Regular, Settings24Regular, ShieldCheckmark20Regular, Sparkle24Regular,
  Window24Regular,
} from "@fluentui/react-icons";
import { createApi, loadToken } from "./api";
import { UiContext } from "./context";
import { readTheme, saveTheme, type ThemeMode } from "./theme";
import type { Status } from "./types";
import { DetailDialog } from "./pages/DetailDialog";
import { Overview } from "./pages/Overview";
import { Records } from "./pages/Records";
import { Sessions } from "./pages/Sessions";
import { Reviews } from "./pages/Reviews";
import { Topics } from "./pages/Topics";
import { Settings } from "./pages/Settings";
import "./styles.css";

const token = loadToken();
const api = createApi(token);
type View = "overview" | "search" | "sessions" | "reviews" | "topics" | "settings";
const labels: Record<View, string> = {overview: "采集概览", search: "记忆检索", sessions: "活动片段", reviews: "每日回顾", topics: "相关检索", settings: "采集设置"};
const navigation = [
  ["overview", <Home24Regular />], ["search", <Search24Regular />],
  ["sessions", <Window24Regular />], ["reviews", <CalendarLtr24Regular />],
  ["topics", <Sparkle24Regular />], ["settings", <Settings24Regular />],
] as const;
const states: Record<string, string> = {stopped: "未开始", running: "采集中", pausing: "正在暂停", paused: "已暂停", closing: "正在退出", closed: "已退出"};

export function App() {
  const [view, setView] = useState<View>("overview");
  const [mode, setMode] = useState<ThemeMode>(readTheme);
  const [systemDark, setSystemDark] = useState(() => matchMedia("(prefers-color-scheme: dark)").matches);
  const [status, setStatus] = useState<Status | null>(null);
  const [connected, setConnected] = useState(false);
  const [closing, setClosing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [recordId, setRecordId] = useState<string | null>(null);
  const [recordsVersion, setRecordsVersion] = useState(0);
  const [sessionFocus, setSessionFocus] = useState<string | null>(null);
  const [events, setEvents] = useState<Status["events"]>([]);
  const [message, setMessage] = useState<{text: string; error: boolean} | null>(null);
  const lastSeq = useRef(0), pending = useRef(new Set<string>()), commandBusy = useRef(false);
  const stopped = useRef(false), pollBusy = useRef(false);
  const recordTrigger = useRef<HTMLElement | null>(null), recordOpen = useRef(false);

  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const changed = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  const dark = mode === "dark" || (mode === "system" && systemDark);
  const setTheme = (value: ThemeMode) => { setMode(value); saveTheme(value); };
  const notify = useCallback((text: string, error = false) => setMessage({text, error}), []);

  const refresh = useCallback(async () => {
    if (pollBusy.current || stopped.current) return;
    pollBusy.current = true;
    try {
      const result = await api.request<Status>(`/api/status?after=${lastSeq.current}`);
      if (stopped.current) return;
      if (result.events.length) {
        lastSeq.current = Math.max(lastSeq.current, ...result.events.map((event) => event.seq));
        setEvents((previous) => [...result.events, ...previous].slice(0, 100));
      }
      setStatus(result); setConnected(true);
    } catch { if (!stopped.current) setConnected(false); }
    finally { pollBusy.current = false; }
  }, []);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>, disposed = false;
    const loop = async () => { await refresh(); if (!disposed && !stopped.current) timer = setTimeout(loop, 1000); };
    void loop();
    const onHide = () => { if (!stopped.current) api.pauseOnPageHide(); };
    window.addEventListener("pagehide", onHide);
    return () => { disposed = true; clearTimeout(timer); window.removeEventListener("pagehide", onHide); };
  }, [refresh]);

  const control = useCallback(async (action: string) => {
    const urgent = action === "pause" || action === "exit";
    if (pending.current.has(action) || (!urgent && commandBusy.current)) return;
    pending.current.add(action);
    if (!urgent) { commandBusy.current = true; setBusy(true); }
    try {
      await api.request("/api/control", {body: {action}});
      if (action === "exit") { stopped.current = true; setClosing(true); setConnected(false); }
      else {
        if (action === "retry-ocr") notify("已重置失败任务，可单独处理已保存图片。");
        await refresh();
      }
    } catch (error) { notify((error as Error).message, true); }
    finally { pending.current.delete(action); if (!urgent) { commandBusy.current = false; setBusy(false); } }
  }, [notify, refresh]);

  const invalidateRecords = useCallback(() => setRecordsVersion((version) => version + 1), []);
  const openRecord = useCallback((id: string) => {
    if (!recordOpen.current) {
      recordTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      recordOpen.current = true;
    }
    setRecordId(id);
  }, []);
  const closeRecord = useCallback((deleted = false) => {
    setRecordId(null); recordOpen.current = false;
    const trigger = recordTrigger.current; recordTrigger.current = null;
    setTimeout(() => {
      if (!deleted && trigger?.isConnected && trigger !== document.body) trigger.focus();
      else (document.querySelector<HTMLElement>(".content button:not(:disabled), .content input:not(:disabled)") || document.querySelector<HTMLElement>(".content"))?.focus();
    }, 0);
  }, []);
  const context = useMemo(() => ({api, status, connected, busy, notify, refresh, control, openRecord, recordsVersion, invalidateRecords}), [status, connected, busy, notify, refresh, control, openRecord, recordsVersion, invalidateRecords]);
  const stateLabel = status?.state === "running" && status.mode !== "capture"
    ? ({ocr_only: "仅识别 · 不采集", analysis_only: "仅分析 · 不采集", index_only: "建立索引 · 不采集"}[status.mode] || "处理中")
    : states[status?.state || "stopped"];
  const selected = !connected && !closing ? "连接不可用" : closing ? "正在退出" : stateLabel;
  const active = connected && status?.state === "running";

  return <FluentProvider theme={dark ? webDarkTheme : webLightTheme} className="app-root" data-mode={dark ? "dark" : "light"}>
    <UiContext.Provider value={context}>
      <div className="shell">
        <aside className="sidebar">
          <div className="brand"><img src="/assets/logo.svg" alt="昔录 · Xilogue 项目图标" /><div><div className="brand-name">昔录 · Xilogue</div><div className="brand-sub">你的本地记忆空间</div></div></div>
          <div className="section-label">工作空间</div>
          <TabList vertical selectedValue={view} onTabSelect={(_, data) => setView(data.value as View)} className="nav-tabs" aria-label="主导航">
            {navigation.map(([value, icon]) => <Tab key={value} value={value} icon={icon}>{labels[value]}</Tab>)}
          </TabList>
          <div className="side-bottom"><strong><ShieldCheckmark20Regular /> 数据留在本机</strong><span>由你决定何时开始、暂停和清理。</span></div>
        </aside>
        <main className="main">
          <header className="topbar"><div className="top-title">工作空间&nbsp; / &nbsp;<strong>{labels[view]}</strong></div><div className="top-actions">
            <Badge appearance="tint" color={active ? "success" : connected ? "informative" : "warning"}>{selected}</Badge>
            <Menu inline checkedValues={{theme: [mode]}} onCheckedValueChange={(_, data) => setTheme(data.checkedItems[0] as ThemeMode)}>
              <MenuTrigger disableButtonEnhancement><Button appearance="subtle" icon={<Color24Regular />} aria-label="外观主题" title="外观主题" /></MenuTrigger>
              <MenuPopover><MenuList><MenuItemRadio name="theme" value="system">跟随系统</MenuItemRadio><MenuItemRadio name="theme" value="light">浅色</MenuItemRadio><MenuItemRadio name="theme" value="dark">深色</MenuItemRadio></MenuList></MenuPopover>
            </Menu>
          </div></header>
          <div className="content" tabIndex={-1}>
            {!connected && <MessageBar intent={closing ? "info" : "warning"}><MessageBarBody>{closing ? "退出请求已发送，等待后台释放资源。" : token ? "连接不可用。若程序已关闭，请重新启动并打开新的链接。" : "请使用终端输出的完整启动链接打开本页面。"}</MessageBarBody></MessageBar>}
            {status?.notice && <MessageBar intent="info"><MessageBarBody>日常使用长测 · 已运行 {(status.notice.elapsed_seconds / 60).toFixed(1)} 分钟 · 样本 {status.notice.samples} · 记录错误 {status.notice.write_errors}</MessageBarBody></MessageBar>}
            {view === "overview" && <Overview events={events} />}
            {view === "search" && <Records />}
            {view === "sessions" && <Sessions focusId={sessionFocus} clearFocus={() => setSessionFocus(null)} />}
            {view === "reviews" && <Reviews />}
            {view === "topics" && <Topics onSession={(id) => {setSessionFocus(id); setView("sessions");}} />}
            {view === "settings" && <Settings />}
            <footer className="footer"><span>昔录 · Xilogue · 本地采集，可回溯</span><span>关闭网页不等于退出程序，请使用暂停或退出。</span></footer>
          </div>
        </main>
      </div>
      <DetailDialog id={recordId} onClose={closeRecord} />
      {message && <div className="toast-host"><MessageBar intent={message.error ? "error" : "success"} role="status"><MessageBarBody>{message.text}</MessageBarBody><Button appearance="subtle" size="small" icon={<Dismiss20Regular />} aria-label="关闭提示" onClick={() => setMessage(null)} /></MessageBar></div>}
    </UiContext.Provider>
  </FluentProvider>;
}
