// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import { createApi } from "../src/api";
import { UiContext, type UiContextValue } from "../src/context";
import { Records } from "../src/pages/Records";
import { DetailDialog } from "../src/pages/DetailDialog";
import { Settings } from "../src/pages/Settings";
import { App } from "../src/App";

function context(request: ReturnType<typeof vi.fn>): UiContextValue {
  return {api:{request,blob:vi.fn().mockResolvedValue(new Blob()),pauseOnPageHide:vi.fn()},
    status:{state:"stopped"} as UiContextValue["status"], connected:true,busy:false,
    notify:vi.fn(),refresh:vi.fn().mockResolvedValue(undefined),control:vi.fn().mockResolvedValue(undefined),
    openRecord:vi.fn(),recordsVersion:0,invalidateRecords:vi.fn()};
}
function show(element: React.ReactNode, value: UiContextValue) {
  return render(<FluentProvider theme={webLightTheme}><UiContext.Provider value={value}>{element}</UiContext.Provider></FluentProvider>);
}
beforeEach(() => {vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()})); vi.stubGlobal("ResizeObserver",class {observe() {} unobserve() {} disconnect() {}}); URL.createObjectURL = vi.fn().mockReturnValue("blob:fixture"); URL.revokeObjectURL = vi.fn();});
afterEach(() => {cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();});

describe("local UI interactions", () => {
  it("renders untrusted titles as text, searches and opens the selected record", async () => {
    const request = vi.fn().mockResolvedValue({results:[{id:"one",app_name:"synthetic.exe",window_title:"<img src=x onerror=alert(1)>",captured_at_utc:"2026-01-01T00:00:00Z",ocr_status:"done",preview:"fixture"}],next_cursor:null});
    const value = context(request), user = userEvent.setup(); const view = show(<Records />,value);
    await screen.findByText("<img src=x onerror=alert(1)>");
    expect(view.container.querySelector("img")).toBeNull();
    await user.type(screen.getByLabelText("搜索文字"),"数据库"); await user.click(screen.getByRole("button",{name:"查找记录"}));
    await waitFor(() => expect(request.mock.calls.some(([path]) => String(path).includes("q=%E6%95%B0%E6%8D%AE%E5%BA%93"))).toBe(true));
    await user.click(screen.getByRole("button",{name:/synthetic.exe/})); expect(value.openRecord).toHaveBeenCalledWith("one");
  });
  it("requires explicit confirmation before deleting a record", async () => {
    const document = {id:"one",app_name:"test",window_title:"Fixture",captured_at_utc:"2026-01-01T00:00:00Z",ocr_status:"done",ocr_attempts:1,ocr_ready:true,text:"safe",lines:[],browser:null};
    const request = vi.fn().mockImplementation((path:string) => Promise.resolve(path.endsWith("/actions") ? {status:"deleted"} : document));
    const value = context(request), onClose = vi.fn(), user = userEvent.setup(); show(<DetailDialog id="one" onClose={onClose} />,value);
    await screen.findByText("Fixture"); await user.click(screen.getByRole("button",{name:"删除这条记录"}));
    expect(request.mock.calls.some(([path]) => String(path).endsWith("/actions"))).toBe(false);
    await user.click(screen.getByRole("button",{name:"保留记录"}));
    expect(screen.queryByRole("button",{name:"确认删除"})).toBeNull();
    await user.click(screen.getByRole("button",{name:"删除这条记录"})); await user.click(screen.getByRole("button",{name:"确认删除"}));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/api/observations/one/actions",{body:{action:"delete"}}));
    expect(value.invalidateRecords).toHaveBeenCalled(); expect(onClose).toHaveBeenCalled();
  });
  it("shows an unavailable image instead of loading forever", async () => {
    const document = {id:"one",app_name:"test",window_title:"Missing image",captured_at_utc:"2026-01-01T00:00:00Z",ocr_status:"done",ocr_attempts:1,ocr_ready:true,text:"safe",lines:[],browser:null};
    const value = context(vi.fn().mockResolvedValue(document));
    value.api.blob = vi.fn().mockRejectedValue(new Error("image_unavailable"));
    show(<DetailDialog id="one" onClose={vi.fn()} />,value);
    await screen.findByText("图片不可用，可能已删除或文件缺失。");
    expect(screen.queryByRole("progressbar",{name:"正在读取图片"})).toBeNull();
  });
  it("keeps unedited configuration fields and normalizes the exclusion list", async () => {
    const config = {sample_interval_seconds:5,idle_policy:"pause",idle_seconds:90,image_quota_mib:1024,retention_days:30,ocr_enabled:true,ocr_threads:2,excluded_apps:["example.exe"],ai_enabled:false,ai_model:"test",semantic_enabled:false,embedding_model:"test",ai_stable_seconds:60,review_timezone:"Asia/Shanghai",embedding_models_dir:"",advanced_field:42};
    const request = vi.fn().mockImplementation((path:string,options?:{body?:unknown}) => Promise.resolve(options?.body || {config,data_dir:"fixture",config_path:"config"}));
    const user = userEvent.setup(); show(<Settings />,context(request));
    const field = await screen.findByLabelText("进程名，每行一个"); await user.clear(field); await user.type(field,"  safe.exe  {enter}{enter}second.exe");
    await user.click(screen.getByRole("button",{name:"保存并应用"}));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/api/config",expect.objectContaining({body:expect.objectContaining({advanced_field:42,excluded_apps:["safe.exe","second.exe"]})})));
  });
  it("sends an authenticated keepalive pause on page hide", () => {
    const fetch = vi.fn().mockResolvedValue({}); vi.stubGlobal("fetch",fetch);
    createApi("test-token").pauseOnPageHide();
    expect(fetch).toHaveBeenCalledWith("/api/control",expect.objectContaining({method:"POST",keepalive:true,body:JSON.stringify({action:"pause"}),headers:expect.objectContaining({Authorization:"Bearer test-token"})}));
  });
  it("returns keyboard focus to the record after closing its dialog", async () => {
    const row = {id:"one",app_name:"fixture",window_title:"Keyboard record",captured_at_utc:"2026-01-01T00:00:00Z",ocr_status:"done",ocr_attempts:1};
    const status = {state:"stopped",mode:"capture",events:[],counts:{},storage:{},metrics:{},timings:{},latest:null,ocr:null,analysis:null};
    vi.stubGlobal("fetch",vi.fn().mockImplementation((path:string) => Promise.resolve({ok:true,
      json:async () => path.startsWith("/api/status") ? status : path.startsWith("/api/observations?") ? {results:[row],next_cursor:null} : {...row,text:"safe",ocr_ready:true,lines:[],browser:null},
      blob:async () => new Blob()})));
    const user = userEvent.setup(); render(<App />);
    await user.click(await screen.findByRole("tab",{name:"记忆检索"}));
    const record = await screen.findByRole("button",{name:/Keyboard record/}); record.focus();
    await user.keyboard("{Enter}"); await screen.findByRole("dialog"); await user.keyboard("{Escape}");
    await waitFor(() => expect(document.activeElement).toBe(record));
  });
  it("moves focus back into the page after deleting the focused record", async () => {
    const row = {id:"one",app_name:"fixture",window_title:"Disposable record",captured_at_utc:"2026-01-01T00:00:00Z",ocr_status:"done",ocr_attempts:1};
    const status = {state:"stopped",mode:"capture",events:[],counts:{},storage:{},metrics:{},timings:{},latest:null,ocr:null,analysis:null};
    let deleted = false;
    vi.stubGlobal("fetch",vi.fn().mockImplementation((path:string) => {
      if (path.endsWith("/actions")) deleted = true;
      return Promise.resolve({ok:true,json:async () => path.startsWith("/api/status") ? status : path.startsWith("/api/observations?") ? {results:deleted ? [] : [row],next_cursor:null} : path.endsWith("/actions") ? {status:"deleted"} : {...row,text:"safe",ocr_ready:true,lines:[],browser:null},blob:async () => new Blob()});
    }));
    const user = userEvent.setup(); render(<App />);
    await user.click(await screen.findByRole("tab",{name:"记忆检索"}));
    const record = await screen.findByRole("button",{name:/Disposable record/}); record.focus(); await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("button",{name:"删除这条记录"})); await user.click(screen.getByRole("button",{name:"确认删除"}));
    await waitFor(() => expect(screen.queryByRole("button",{name:/Disposable record/})).toBeNull());
    await waitFor(() => expect(document.querySelector(".content")?.contains(document.activeElement)).toBe(true));
  });
});
