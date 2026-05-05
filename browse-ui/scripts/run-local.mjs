import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const uiDir = resolve(scriptDir, "..");
const repoRoot = resolve(uiDir, "..");
const args = process.argv.slice(2);
const skipBuild = args.includes("--skip-build");
const browseArgs = args.filter((arg) => arg !== "--skip-build");
const pnpmCommand = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const pythonCommand = process.platform === "win32" ? "python" : "python3";

function run(command, commandArgs, options = {}) {
  const result = spawnSync(command, commandArgs, {
    cwd: options.cwd ?? process.cwd(),
    env: process.env,
    stdio: "inherit",
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

if (!skipBuild) {
  run(pnpmCommand, ["build"], { cwd: uiDir });
}

run(pythonCommand, ["browse.py", ...browseArgs], { cwd: repoRoot });
