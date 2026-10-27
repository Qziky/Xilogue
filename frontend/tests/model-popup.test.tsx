// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/App";

let pageStyles:HTMLStyleElement;
beforeEach(() => {
  vi.stubGlobal("matchMedia",vi.fn().mockReturnValue({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
  vi.stubGlobal("ResizeObserver",class {observe() {} unobserve() {} disconnect() {}});
  const status = {state:"stopped",mode:"capture",events:[],counts:{},storage:{},metrics:{},timings:{},latest:null,ocr:null,analysis:null};
  const config = {ai_provider:"ollama",ai_model:"existing-chat",embedding_provider:"ollama",embedding_model:"existing-embed"};
  vi.stubGlobal("fetch",vi.fn().mockImplementation((path:string) => Promise.resolve({ok:true,
    json:async () => path.startsWith("/api/status") ? status : path === "/api/config" ? {config,data_dir:"synthetic"} : {ok:true,models:["synthetic-model"]},
  })));
  pageStyles = document.createElement("style");
  // JSDOM does not load Vite's page CSS; exercise the full-page layout contract explicitly.
  pageStyles.textContent = ".app-root { min-height: 100vh; }";
  document.head.append(pageStyles);
});
afterEach(() => {cleanup(); pageStyles.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks();});

it.each(["摘要模型","嵌入模型"])("opens %s without copying the full-page layout onto its portal",async (label) => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(await screen.findByRole("tab",{name:"采集设置"}));
  const input = await screen.findByRole("combobox",{name:label});
  await user.click(input);
  await screen.findByRole("option",{name:"synthetic-model"});
  const list = screen.getByRole("listbox",{name:label});
  const portal = list.closest<HTMLElement>(".fui-FluentProvider")!;
  const page = document.querySelector<HTMLElement>(".app-root")!;
  expect(portal).not.toBe(page);
  expect(portal.classList.contains("app-root")).toBe(false);
  expect(getComputedStyle(page).minHeight).toBe("100vh");
  expect(getComputedStyle(portal).minHeight).not.toBe("100vh");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("listbox",{name:label})).toBeNull());
});
