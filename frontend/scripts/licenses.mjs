import { execFileSync } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import { resolve, relative } from "node:path";

const root = process.cwd();
const npmCli = process.env.npm_execpath;
if (!npmCli) throw Error("Run this script with npm run licenses");
const paths = execFileSync(process.execPath, [npmCli, "ls", "--omit=dev", "--all", "--parseable"], { cwd: root, encoding: "utf8" })
  .split(/\r?\n/).filter((path) => path && resolve(path) !== resolve(root)).sort();
const notices = [];
for (const path of paths) {
  const metadata = JSON.parse(await readFile(resolve(path, "package.json"), "utf8"));
  let license = "";
  for (const name of ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"]) {
    try { license = await readFile(resolve(path, name), "utf8"); break; } catch { /* Try common names. */ }
  }
  if (!license && metadata.name === "@fluentui/react-icons") license = await readFile(resolve(root, "licenses/fluentui-system-icons.LICENSE"), "utf8");
  if (!license && metadata.name.startsWith("embla-carousel")) license = await readFile(resolve(root, "licenses/embla-carousel.LICENSE"), "utf8");
  if (!license) throw Error(`Missing license text for ${metadata.name}@${metadata.version}`);
  notices.push(`${metadata.name}@${metadata.version} (${metadata.license || "see text"})\n${relative(root,path)}\n\n${license.trim()}\n`);
}
await writeFile(resolve(root, "../src/xilogue/assets/FRONTEND-LICENSES.txt"), notices.join("\n---\n\n"), "utf8");
