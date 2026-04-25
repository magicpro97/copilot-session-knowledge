import { execSync } from "child_process";
import { writeFileSync, existsSync, mkdirSync } from "fs";
import { resolve } from "path";

const distDir = resolve("dist");

// Ensure dist/ exists (created by next build via distDir config)
if (!existsSync(distDir)) {
  mkdirSync(distDir, { recursive: true });
}

// Write version.json
let hash = "unknown";
try {
  hash = execSync("git rev-parse --short HEAD", { cwd: resolve("..") })
    .toString()
    .trim();
} catch {
  console.warn("Could not get git hash — not in a git repo?");
}

const pkg = JSON.parse(execSync("cat package.json").toString());

writeFileSync(
  resolve(distDir, "version.json"),
  JSON.stringify(
    {
      version: pkg.version,
      buildHash: hash,
      builtAt: new Date().toISOString(),
      nodeVersion: process.version,
    },
    null,
    2
  )
);

console.log(`✅ dist/ ready — hash: ${hash}`);

