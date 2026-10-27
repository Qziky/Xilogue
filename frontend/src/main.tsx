import { createRoot } from "react-dom/client";
import { createDOMRenderer, RendererProvider } from "@griffel/react";
import { App } from "./App";

const nonce = document.querySelector<HTMLMetaElement>('meta[name="xilogue-style-nonce"]')?.content;
if (!nonce) throw new Error("Missing local style nonce");
const renderer = createDOMRenderer(document, {styleElementAttributes: {nonce}});
const root = document.getElementById("root");
if (!root) throw new Error("Missing application root");
createRoot(root).render(<RendererProvider renderer={renderer} targetDocument={document}><App /></RendererProvider>);
