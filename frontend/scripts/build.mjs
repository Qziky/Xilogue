import { build } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
for (const [entry, output, name] of [
  ["frontend/src/main.tsx", "src/xilogue/static", "app"],
  ["frontend/src/extension/popup.tsx", "src/xilogue/browser_extension", "popup"],
]) {
  if (process.argv[2] && process.argv[2] !== name) continue;
  await build({
    configFile: false, root: resolve(root, "frontend"), publicDir: false,
    plugins: [react()],
    build: {
      outDir: resolve(root, output), emptyOutDir: false, sourcemap: false,
      rollupOptions: {
        input: resolve(root, entry),
        output: {
          inlineDynamicImports: true, entryFileNames: `${name}.js`,
          assetFileNames: (asset) => asset.names.some((n) => n.endsWith(".css")) ? `${name}.css` : "[name][extname]",
        },
      },
    },
  });
}
