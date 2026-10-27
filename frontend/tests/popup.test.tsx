// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Popup, parsePair } from "../src/extension/popup";

beforeEach(() => {vi.stubGlobal("matchMedia",vi.fn().mockReturnValue({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()})); vi.stubGlobal("ResizeObserver",class {observe() {} unobserve() {} disconnect() {}});});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});
it("accepts only local pairing data", () => {
  expect(parsePair(JSON.stringify({endpoint:"http://127.0.0.1:1234",token:"a".repeat(43)})).endpoint).toBe("http://127.0.0.1:1234");
  expect(() => parsePair(JSON.stringify({endpoint:"https://example.com",token:"a".repeat(43)}))).toThrow();
});
it("stores only the session pair and rejects an incognito tab before capture", async () => {
  const pair = {endpoint:"http://127.0.0.1:1234",token:"a".repeat(43)};
  const set = vi.fn().mockResolvedValue(undefined), get = vi.fn().mockResolvedValue({pair}), query = vi.fn().mockResolvedValue([{incognito:true,url:"https://example.com",id:1}]);
  vi.stubGlobal("chrome",{storage:{session:{set,get}},tabs:{query}});
  const user = userEvent.setup(); render(<Popup />);
  fireEvent.change(screen.getByLabelText("本地界面生成的配对 JSON"),{target:{value:JSON.stringify(pair)}});
  await user.click(screen.getByRole("button",{name:"保存本次会话配对"}));
  await waitFor(() => expect(set).toHaveBeenCalledWith({pair}));
  await user.click(screen.getByRole("button",{name:"保存可见区域与文字"}));
  await screen.findByText("仅支持非无痕 HTTP(S) 页面");
});
