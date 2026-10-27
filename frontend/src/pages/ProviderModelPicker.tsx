import { useEffect, useMemo, useState } from "react";
import { Button, Combobox, Field, Option } from "@fluentui/react-components";
import { useUi } from "../context";
import type { AiRole, SettingsConfig } from "./AiProviderSettings";

type Catalog = {ok:boolean; models:string[]; source?:string; truncated?:boolean; error?:string};
type Props = {
  role:AiRole; config:SettingsConfig | null; configured:boolean;
  apiKey:string; clearKey:boolean; onModel:(value:string) => void;
};
const errors:Record<string,string> = {
  api_authentication_failed:"鉴权失败，请核对密钥",
  api_key_missing:"请先填写密钥或指定密钥环境变量",
  api_unavailable:"服务暂时无法连接",
  api_rate_limited:"服务限流，请稍后刷新",
  api_redirect_rejected:"服务返回了重定向，请核对基址",
  model_list_unavailable:"服务未提供模型目录接口",
  api_response_invalid:"服务返回的模型目录格式无效",
  api_request_failed:"服务未能返回模型目录",
};

function validEndpoint(value:string) {
  try {
    const url = new URL(value);
    return !url.username && !url.password && !url.search && !url.hash &&
      (url.protocol === "https:" || url.protocol === "http:" &&
       ["127.0.0.1","localhost","[::1]"].includes(url.hostname));
  } catch {return false;}
}

export function ProviderModelPicker({role,config,configured,apiKey,clearKey,onModel}:Props) {
  const {api} = useUi();
  const prefix = role === "analysis" ? "ai" : "embedding";
  const provider = String(config?.[`${prefix}_provider`] || "ollama");
  const base = provider === "ollama" ? "" : String(config?.[`${prefix}_base_url`] || "");
  const env = provider === "ollama" ? "" : String(config?.[`${prefix}_api_key_env`] || "");
  const port = role === "embedding" && provider === "ollama" ? config?.embedding_port ?? 0 : 0;
  const directory = role === "embedding" && provider === "ollama" ? config?.embedding_models_dir ?? "" : "";
  const model = String(config?.[`${prefix}_model`] || "");
  const [revision,setRevision] = useState(0);
  const [filter,setFilter] = useState("");
  const body = useMemo(() => {
    const value:Record<string,unknown> = {role,provider,base_url:base,api_key_env:env};
    if (provider !== "ollama") {
      if (apiKey) value.api_key = apiKey;
      else if (clearKey) value.api_key = null;
    } else if (role === "embedding") {
      value.embedding_port = port;
      value.embedding_models_dir = directory;
    }
    return value;
  },[role,provider,base,env,apiKey,clearKey,port,directory]);
  const local = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(?=[:/]|$)/.test(base);
  const ready = !!config && (provider === "ollama" || validEndpoint(base) &&
    (!!apiKey || configured && !clearKey || !!env || local));
  const [state,setState] = useState<{
    request:typeof body | null; loading:boolean; result:Catalog | null; error:string;
  }>({request:null,loading:false,result:null,error:""});

  useEffect(() => {
    if (!ready) {
      setState({request:null,loading:false,result:null,error:""});
      return;
    }
    const controller = new AbortController();
    let active = true;
    setState({request:body,loading:true,result:null,error:""});
    const timer = setTimeout(() => {
      void api.request<Catalog>("/api/ai/models",{body,signal:controller.signal}).then((result) => {
        if (!active) return;
        if (!result.ok || !Array.isArray(result.models)) {
          setState({request:body,loading:false,result:null,
            error:errors[result.error || ""] || "暂时无法读取模型目录"});
        } else {
          setState({request:body,loading:false,result,error:""});
        }
      }).catch((error:Error) => {
        if (active) setState({request:body,loading:false,result:null,error:error.message});
      });
    },400);
    return () => {active = false; clearTimeout(timer); controller.abort();};
  },[api,body,ready,revision]);

  const current = state.request === body ? state : null;
  const models = ready ? current?.result?.models || [] : [];
  const options = models.filter((id) => id.toLowerCase().includes(filter.toLowerCase())).slice(0,100);
  const loading = ready && (!current || current.loading);
  return <>
    <Field label={role === "analysis" ? "摘要模型" : "嵌入模型"}>
      <Combobox freeform value={model} selectedOptions={model ? [model] : []}
        maxLength={120} required placeholder="选择或输入模型 ID"
        onChange={(event) => {setFilter(event.target.value); onModel(event.target.value);}}
        onOpenChange={(_,data) => {if (!data.open) setFilter("");}}
        onOptionSelect={(_,data) => {if (data.optionValue) {setFilter(""); onModel(data.optionValue);}}}>
        {options.map((id) => <Option key={id} value={id} text={id}>{id}</Option>)}
      </Combobox>
    </Field>
    <div><Button type="button" disabled={!ready || loading} onClick={() => setRevision((value) => value + 1)}>刷新模型列表</Button></div>
    <span className="caption" role="status">
      {!ready ? "填写服务地址和凭据后自动读取模型列表，也可手动输入模型 ID。" :
        loading ? "正在读取模型列表…" : current?.error ? `${current.error}；仍可手动输入模型 ID。` :
        models.length ? `已读取 ${models.length} 个模型${current?.result?.source === "project_cache" ? "（当前本机模型目录）" : ""}${current?.result?.truncated ? "，目录结果已截断" : ""}。列表不保证每个模型都支持当前用途，可输入筛选。` :
        "目录中还没有可选模型，可以手动输入模型 ID。"}
    </span>
    <span className="caption">自动读取仅请求模型目录，不运行模型或发送记录。密钥暂用于目录请求，保存设置后才加密保留。</span>
  </>;
}
