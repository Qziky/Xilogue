import { Button, Card, Checkbox, Field, Input, Select } from "@fluentui/react-components";
import { ProviderModelPicker } from "./ProviderModelPicker";

export type SettingsConfig = Record<string, string | number | boolean | string[]>;
export type AiRole = "analysis" | "embedding";
const presets = [
  {name:"Ollama", url:"ollama"},
  {name:"OpenAI", url:"https://api.openai.com/v1"},
  {name:"DeepSeek", url:"https://api.deepseek.com"},
  {name:"硅基流动", url:"https://api.siliconflow.cn/v1"},
];
type Props = {
  role:AiRole; config:SettingsConfig | null; editable:boolean; configured:boolean;
  apiKey:string; clearKey:boolean; testing:boolean;
  change:(values:Partial<SettingsConfig>) => void;
  onKey:(value:string) => void; onClear:(value:boolean) => void; onTest:() => void;
};

export function AiProviderSettings({role,config,editable,configured,apiKey,clearKey,testing,change,onKey,onClear,onTest}:Props) {
  const prefix = role === "analysis" ? "ai" : "embedding";
  const analysis = role === "analysis";
  const api = config?.[`${prefix}_provider`] === "openai_compatible";
  const str = (name:string) => String(config?.[`${prefix}_${name}`] ?? "");
  const set = (name:string, value:string | boolean) => change({[`${prefix}_${name}`]:value});
  function destination(values:Partial<SettingsConfig>) {
    change({...values,[`${prefix}_remote_consent`]:false,[`${prefix}_api_key_env`]:""});
    onKey(""); onClear(false);
  }
  const preset = api ? presets.find((item) => item.url === str("base_url"))?.url || "custom" : "ollama";
  function selectProvider(value:string) {
    destination({[`${prefix}_provider`]:value === "ollama" ? "ollama" : "openai_compatible",
      [`${prefix}_base_url`]:value === "ollama" || value === "custom" ? "" : value,
      [`${prefix}_model`]:""});
  }
  return <Card className="xilogue-card settings-fields">
    <h2>{analysis ? "AI 摘要提供商" : "向量检索提供商"}</h2>
    <Checkbox label={analysis ? "采集中自动整理已稳定的活动片段" : "采集中自动建立文字向量"} checked={!!config?.[analysis ? "ai_enabled" : "semantic_enabled"]} onChange={(_,d) => change({[analysis ? "ai_enabled" : "semantic_enabled"]:d.checked === true})} />
    <Field label={analysis ? "摘要提供商" : "向量提供商"}><Select value={preset} onChange={(_,d) => selectProvider(d.value)}>{presets.map((item) => <option key={item.url} value={item.url}>{item.name}</option>)}<option value="custom">自定义服务</option></Select></Field>
    {api && <>
      <Field label={analysis ? "摘要 API 基址" : "向量 API 基址"}><Input value={str("base_url")} maxLength={2048} required onChange={(_,d) => destination({[`${prefix}_base_url`]:d.value})} placeholder="https://provider.example/v1" spellCheck={false} /></Field>
      <Field label={analysis ? "摘要 API Key" : "向量 API Key"}><Input type="password" value={apiKey} autoComplete="new-password" maxLength={4096} onChange={(_,d) => {onKey(d.value); onClear(false);}} placeholder={configured ? "已配置；留空保留" : "输入密钥；无鉴权本机服务可留空"} /></Field>
      {configured && <Checkbox label={analysis ? "清除已保存的摘要密钥" : "清除已保存的向量密钥"} checked={clearKey} onChange={(_,d) => {onClear(d.checked === true); onKey("");}} />}
      <Field label={analysis ? "摘要密钥环境变量（可选）" : "向量密钥环境变量（可选）"}><Input value={str("api_key_env")} maxLength={120} onChange={(_,d) => set("api_key_env",d.value)} placeholder={analysis ? "例如 XILOGUE_AI_API_KEY" : "例如 XILOGUE_EMBEDDING_API_KEY"} /></Field>
      <span className="caption">密钥由 Windows 当前用户加密保存，不回显、不进入备份。已保存密钥优先于指定环境变量；更换地址后需重新指定环境变量。</span>
    </>}
    <ProviderModelPicker role={role} config={config} configured={configured} apiKey={apiKey} clearKey={clearKey} onModel={(value) => set("model",value)} />
    {api && analysis && <Field label="API 结构化输出方式"><Select value={str("response_format") || "json_object"} onChange={(_,d) => set("response_format",d.value)}><option value="json_object">JSON 模式</option><option value="json_schema">严格 JSON Schema</option><option value="text">仅提示词约束（兼容接口）</option></Select></Field>}
    {api && analysis && <>
      <Field label="API 输出上限（token）"><Input type="number" value={str("max_tokens") || "2048"} min={256} max={32768} required onChange={(_,d) => change({ai_max_tokens:d.value === "" ? "" : Number(d.value)})} /></Field>
      <Field label="输出上限参数"><Select value={str("token_parameter") || "max_tokens"} onChange={(_,d) => set("token_parameter",d.value)}><option value="max_tokens">max_tokens（通用兼容）</option><option value="max_completion_tokens">max_completion_tokens（OpenAI 推理模型）</option></Select></Field>
    </>}
    {api && <>
      <span className="caption">发送到：{str("base_url") || "请填写服务地址"}。{analysis ? "每段最多发送 8 条来源的 ID、时间、应用名、截断标题及各 600 字 OCR / 网页文字。" : "建索引发送各来源前 450 字文字；语义查询会发送查询词。"}不发送原始截图。服务可能计费并按其政策处理数据。</span>
      <Checkbox label={analysis ? "允许将上述摘要文字发送到此 API 服务" : "允许将上述索引文字和查询词发送到此 API 服务"} checked={!!config?.[`${prefix}_remote_consent`]} onChange={(_,d) => set("remote_consent",d.checked === true)} />
    </>}
    <div><Button type="button" disabled={!editable || testing} onClick={onTest}>{analysis ? "测试摘要连接" : "测试向量连接"}</Button></div>
    <span className="caption">服务和模型需支持{analysis ? " Chat Completions" : " Embeddings"} 接口。测试使用已保存的配置和合成文字，可能计费。更换向量服务或模型后需重新建立索引。</span>
  </Card>;
}
