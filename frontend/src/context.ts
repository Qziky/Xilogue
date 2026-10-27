import { createContext, useContext } from "react";
import { createApi } from "./api";
import type { Status } from "./types";

export type Api = ReturnType<typeof createApi>;
export interface UiContextValue {
  api: Api; status: Status | null; connected: boolean; busy: boolean;
  notify: (message: string, error?: boolean) => void;
  refresh: () => Promise<void>;
  control: (action: string) => Promise<void>;
  openRecord: (id: string) => void;
  recordsVersion: number; invalidateRecords: () => void;
}
export const UiContext = createContext<UiContextValue | null>(null);
export function useUi(): UiContextValue {
  const value = useContext(UiContext);
  if (!value) throw new Error("UI context is not available");
  return value;
}
