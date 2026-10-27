// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import { UiContext, type UiContextValue } from "../src/context";
import { Settings } from "../src/pages/Settings";
import { createApi } from "../src/api";

const defaults = {
  sample_interval_seconds:5, idle_policy:"pause", idle_seconds:90,
  image_quota_mib:512, retention_days:7, ocr_enabled:true, ocr_threads:2, excluded_apps:[],
  ai_enabled:false, ai_provider:"openai_compatible", ai_base_url:"https://fixture.example/v1",
  ai_model:"test/chat", ai_remote_consent:true, ai_response_format:"json_object",
  ai_max_tokens:2048, ai_token_parameter:"max_tokens", ai_api_key_env:"OLD_KEY_ENV",
  semantic_enabled:false, embedding_provider:"ollama", embedding_model:"embeddinggemma:latest",
  ai_stable_seconds:60, review_timezone:"Asia/Shanghai", embedding_models_dir:"",
};
function setup(catalog?:(body:Record<string,unknown>) => Promise<unknown>) {
  let config = {...defaults}, configured = true;
  const request = vi.fn().mockImplementation((path:string, options?:{body?:Record<string,unknown>}) => {
    if (path === "/api/ai/test") return Promise.resolve({ok:true});
    if (path === "/api/ai/models") return catalog ? catalog(options?.body || {}) : Promise.resolve({ok:true,models:["test/chat","test/embed"]});
    if (path === "/api/config" && options?.body) {
      const {ai_api_key:key,...next} = options.body;
      if (key !== undefined) configured = key !== null;
      config = next as typeof defaults;
      return Promise.resolve(config);
    }
    return Promise.resolve({config,data_dir:"synthetic fixture",api_credentials:{analysis:configured,embedding:false}});
  });
  const context = {api:{request,blob:vi.fn(),pauseOnPageHide:vi.fn()},
    status:{state:"stopped"},connected:true,busy:false,notify:vi.fn(),refresh:vi.fn(),
    control:vi.fn(),openRecord:vi.fn(),recordsVersion:0,invalidateRecords:vi.fn()} as unknown as UiContextValue;
  render(<FluentProvider theme={webLightTheme}><UiContext.Provider value={context}><Settings /></UiContext.Provider></FluentProvider>);
  return {request,context,user:userEvent.setup()};
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver",class {observe() {} unobserve() {} disconnect() {}});
});
afterEach(() => {cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();});

describe("user-selected AI services", () => {
  it.each(["/api/ai/models","/api/ai/test"])("explains a stale backend when %s is missing",async (path) => {
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:false,status:404,json:async () => ({detail:"Not Found"})}));
    await expect(createApi("synthetic-token").request(path,{body:{role:"analysis"}})).rejects.toThrow("仅刷新网页不会更新后台");
  });
  it("clears consent, typed credentials and environment reference when the destination changes", async () => {
    const {user,request} = setup();
    const key = await screen.findByLabelText("摘要 API Key");
    await user.type(key,"synthetic-new-key");
    await user.selectOptions(screen.getByLabelText("摘要提供商"),"https://api.deepseek.com");
    expect((key as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("摘要密钥环境变量（可选）") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("允许将上述摘要文字发送到此 API 服务") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText("向量提供商") as HTMLSelectElement).value).toBe("ollama");
    expect(request.mock.calls.some(([path,options]) => path === "/api/config" && options?.body)).toBe(false);
  });

  it("persists keys only on explicit save and clears the password field after saving", async () => {
    const {user,request} = setup();
    const key = await screen.findByLabelText("摘要 API Key");
    expect((key as HTMLInputElement).value).toBe("");
    await user.type(key,"synthetic-new-key");
    expect(request.mock.calls.some(([path,options]) => path === "/api/config" && options?.body)).toBe(false);
    await user.click(screen.getByRole("button",{name:"保存并应用"}));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/api/config",{body:expect.objectContaining({ai_api_key:"synthetic-new-key",embedding_provider:"ollama"})}));
    await waitFor(() => expect((screen.getByLabelText("摘要 API Key") as HTMLInputElement).value).toBe(""));
    expect((screen.getByLabelText("摘要 API Key") as HTMLInputElement).placeholder).toContain("已配置");
  });

  it("removes a saved key only through the explicit clear choice", async () => {
    const {user,request} = setup();
    await user.click(await screen.findByLabelText("清除已保存的摘要密钥"));
    await user.click(screen.getByRole("button",{name:"保存并应用"}));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/api/config",{body:expect.objectContaining({ai_api_key:null})}));
  });

  it("tests only after a click and uses the selected role without sending browser credentials", async () => {
    const {user,request,context} = setup();
    await screen.findByLabelText("摘要 API Key");
    expect(request.mock.calls.some(([path]) => path === "/api/ai/test")).toBe(false);
    await user.click(screen.getByRole("button",{name:"测试摘要连接"}));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/api/ai/test",{body:{role:"analysis"}}));
    expect(context.notify).toHaveBeenCalledWith("连接测试通过，合成结果校验成功。",false);
  });

  it("offers Ollama alongside the other providers without a separate connection-mode selector", async () => {
    setup();
    const provider = await screen.findByLabelText("摘要提供商");
    expect(Array.from((provider as HTMLSelectElement).options).map((item) => item.text)).toEqual(["Ollama","OpenAI","DeepSeek","硅基流动","自定义服务"]);
    expect(screen.queryByLabelText("摘要接入方式")).toBeNull();
    expect(screen.queryByLabelText("摘要 API 提供商")).toBeNull();
  });

  it("automatically reads available models and lets the user select one without saving first", async () => {
    const {user,request} = setup();
    await waitFor(() => expect(request).toHaveBeenCalledWith("/api/ai/models",expect.objectContaining({body:expect.objectContaining({role:"analysis",provider:"openai_compatible",base_url:"https://fixture.example/v1"})})));
    const model = screen.getByLabelText("摘要模型");
    await user.clear(model); await user.click(model); await user.keyboard("{ArrowDown}");
    await user.click(await screen.findByRole("option",{name:"test/embed"}));
    expect((model as HTMLInputElement).value).toBe("test/embed");
    expect(request.mock.calls.some(([path,options]) => path === "/api/config" && options?.body)).toBe(false);
  });

  it("preserves a manual model when discovery fails and offers a refresh", async () => {
    const {user,request} = setup(async () => ({ok:false,models:[],error:"model_list_unavailable"}));
    await screen.findAllByText(/服务未提供模型目录接口/);
    const model = screen.getByLabelText("摘要模型");
    expect((model as HTMLInputElement).value).toBe("test/chat");
    await user.clear(model); await user.type(model,"manual-model");
    const before = request.mock.calls.filter(([path]) => path === "/api/ai/models").length;
    await user.click(screen.getAllByRole("button",{name:"刷新模型列表"})[0]);
    await waitFor(() => expect(request.mock.calls.filter(([path]) => path === "/api/ai/models").length).toBeGreaterThan(before));
    expect((model as HTMLInputElement).value).toBe("manual-model");
  });

  it("ignores a delayed model list after the user switches providers", async () => {
    let finishOld:((value:unknown) => void) | undefined;
    const {user} = setup((body) => {
      if (body.role === "embedding") return Promise.resolve({ok:true,models:[]});
      if (body.base_url === "https://fixture.example/v1") return new Promise((resolve) => {finishOld = resolve;});
      return Promise.resolve({ok:true,models:["new-provider-model"]});
    });
    await waitFor(() => expect(finishOld).toBeDefined());
    await user.selectOptions(screen.getByLabelText("摘要提供商"),"https://api.deepseek.com");
    await user.type(screen.getByLabelText("摘要 API Key"),"new-fixture-key");
    await screen.findByText(/已读取 1 个模型/);
    finishOld?.({ok:true,models:["old-provider-model"]});
    const model = screen.getByLabelText("摘要模型");
    await user.click(model); await user.keyboard("{ArrowDown}");
    expect(await screen.findByRole("option",{name:"new-provider-model"})).toBeTruthy();
    expect(screen.queryByRole("option",{name:"old-provider-model"})).toBeNull();
  });
});
