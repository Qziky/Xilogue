import { readFile, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { Resvg } from "@resvg/resvg-js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const svg = await readFile(resolve(root, "frontend/branding/icon.svg"));
const assets = resolve(root, "src/xilogue/assets");
const extension = resolve(root, "src/xilogue/browser_extension/icons");
await mkdir(assets, { recursive: true });
await mkdir(extension, { recursive: true });
await writeFile(resolve(assets, "icon.svg"), svg);
await writeFile(resolve(root, "src/xilogue/static/logo.svg"), svg);
const sizes = [16, 24, 32, 48, 64, 128, 256], images = [];
for (const size of sizes) {
  const png = new Resvg(svg, { fitTo: { mode: "width", value: size } }).render().asPng();
  await writeFile(resolve(assets, `icon-${size}.png`), png);
  if ([16, 32, 48, 128].includes(size)) await writeFile(resolve(extension, `${size}.png`), png);
  images.push(png);
}
// Windows ICO entries store the PNG buffers directly, preserving transparent edges.
const header = Buffer.alloc(6 + sizes.length * 16);
header.writeUInt16LE(1, 2); header.writeUInt16LE(sizes.length, 4);
let offset = header.length;
for (const [index, size] of sizes.entries()) {
  const at = 6 + index * 16, png = images[index];
  header[at] = size === 256 ? 0 : size; header[at + 1] = header[at];
  header.writeUInt16LE(1, at + 4); header.writeUInt16LE(32, at + 6);
  header.writeUInt32LE(png.length, at + 8); header.writeUInt32LE(offset, at + 12);
  offset += png.length;
}
await writeFile(resolve(assets, "icon.ico"), Buffer.concat([header, ...images]));
