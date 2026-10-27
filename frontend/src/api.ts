const errors: Record<string, string> = {
  authorization_required: "连接凭证已失效，请使用本次启动链接。",
  configuration_invalid: "配置校验失败，请修正设置后重试。",
  configuration_reload_required: "配置需要重新加载。",
  save_requires_pause: "请先暂停并等待“已暂停”。",
  retry_requires_pause: "请先暂停，再重试识别。",
  session_edit_requires_pause: "请先暂停，再编辑活动片段。",
  index_requires_pause: "请先暂停，再建立向量索引。",
  pair_requires_pause: "请先暂停，再配置浏览器扩展。",
  backup_requires_pause: "请先暂停，再下载备份。",
  command_busy: "另一项操作正在进行，请稍后重试。",
  invalid_request: "参数未通过校验，请检查输入。",
  document_unavailable: "来源已删除、过期或缺图。",
  image_unavailable: "原图已删除或不可用。",
  operation_unavailable: "当前状态暂不允许此操作。",
  operation_failed: "操作未完成，请查看运行状态。",
  ocr_not_failed: "这条记录当前没有失败的识别任务。",
};

export function loadToken(): string {
  const key = `xilogue:${location.origin}`;
  const fragment = new URLSearchParams(location.hash.slice(1)).get("token");
  let token = fragment || "";
  try {
    token ||= sessionStorage.getItem(key) || "";
    if (fragment) sessionStorage.setItem(key, fragment);
  } catch { /* A disabled storage API does not disclose or persist the token. */ }
  if (fragment) history.replaceState(null, "", location.pathname + location.search);
  return token;
}

export function createApi(token: string) {
  async function request<T>(path: string, options: {body?: unknown; signal?: AbortSignal; blob?: false} = {}): Promise<T> {
    return requestRaw(path, options) as Promise<T>;
  }
  async function requestRaw(path: string, options: {body?: unknown; signal?: AbortSignal; blob?: boolean} = {}) {
    const headers: Record<string, string> = {Authorization: `Bearer ${token}`};
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {
      method: options.body === undefined ? "GET" : "POST", headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store", signal: options.signal,
    });
    if (!response.ok) {
      if (response.status === 404 && ["/api/ai/models","/api/ai/test"].includes(path.split("?")[0])) {
        throw new Error("当前后台缺少 AI 接口，请点击“退出程序”并重新启动 Xilogue，再刷新页面；仅刷新网页不会更新后台。");
      }
      let info: {detail?: string; error?: string} = {};
      try { info = await response.json(); } catch { /* Avoid showing raw server content. */ }
      const key = typeof info.detail === "string" ? info.detail : info.error || "";
      throw new Error(errors[key] || `请求未完成（${response.status}）`);
    }
    return options.blob ? response.blob() : response.json();
  }
  const blob = (path: string, signal?: AbortSignal) => requestRaw(path, {blob: true, signal}) as Promise<Blob>;
  const pauseOnPageHide = () => {
    if (token) void fetch("/api/control", {method: "POST", keepalive: true,
      headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"},
      body: JSON.stringify({action: "pause"})}).catch(() => {});
  };
  return {request, blob, pauseOnPageHide};
}

export async function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

export const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-CN", {hour12: false}) : "尚未保存";
export const size = (value: number | undefined) => `${((value || 0) / 1048576).toFixed(1)} MiB`;
