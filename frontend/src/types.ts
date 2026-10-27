export type RunState = "stopped" | "running" | "pausing" | "paused" | "closing" | "closed";
export type JsonObject = Record<string, unknown>;

export interface Status {
  ai_provider?:string; ai_destination?:string;
  state: RunState; mode: string; reason: string; last_error: string; config_error: string;
  target: {app_name: string; title: string} | null; queue: number;
  counts: Record<string, number>; storage: Record<string, number>;
  latest: {id: string; captured_at_utc: string; capture_reason: string} | null;
  metrics: Record<string, number>; timings: Record<string, {count: number; mean_ms: number}>;
  events: Array<{seq: number; at: string; kind: string; reason: string}>;
  notice: {elapsed_seconds: number; samples: number; write_errors: number} | null;
  ocr: {state: string; model_loaded: boolean; pass_finished: boolean; jobs: Record<string, number>} | null;
  analysis: {state: string; provider_active: boolean; cleanup_error: string; last_error: string;
    jobs: Record<string, number>; embeddings: Record<string, number>} | null;
  model_queries: number; model_cleanup_pending: number;
}

export interface Observation {
  id: string; app_name: string; window_title: string; captured_at_utc: string;
  ocr_status: string | null; ocr_attempts: number; preview?: string;
}
export interface Document extends Observation {
  text: string; engine: string | null; ocr_ready: boolean;
  lines: Array<{text: string; confidence: number; box: number[][]}>;
  browser: {url: string; title: string; truncated: boolean; nearest_desktop_id: string | null} | null;
}
export interface Claim {text: string; source_id: string; quote: string}
export interface Insight {
  model: string; payload: {summary: string; topics: string[]; keywords: string[];
    importance: number; confidence: number; uncertainties: string[]; claims: Claim[]};
}
export interface Session {
  id: string; app_name: string; title: string; started_at_utc: string; ended_at_utc: string;
  representative_id: string | null; source_count: number; manual: number;
  sources: Observation[]; next_offset: number | null; insight?: Insight | null;
}
export interface ReviewCard {
  session_id: string; summary: string; claims: Claim[]; topics: string[]; reason: string;
  feedback: string; hidden: boolean; correction: string;
}
export interface Related {
  id: string; title: string; app_name: string; representative_id: string;
  score: number; cosine: number | null; matched_terms: string[];
}
