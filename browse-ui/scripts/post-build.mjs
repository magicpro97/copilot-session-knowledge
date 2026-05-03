import { execSync } from "child_process";
import { writeFileSync, existsSync, mkdirSync, readFileSync } from "fs";
import { resolve } from "path";

const distDirName = process.env.NEXT_DIST_DIR || "dist";
const distDir = resolve(distDirName);

// Ensure the output directory exists (created by next build via distDir config)
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

const pkg = JSON.parse(readFileSync(resolve("package.json"), "utf-8"));

function detectBasePath() {
  if (process.env.NEXT_BASE_PATH !== undefined) {
    return process.env.NEXT_BASE_PATH;
  }

  for (const htmlPath of [resolve(distDir, "chat", "index.html"), resolve(distDir, "index.html")]) {
    if (!existsSync(htmlPath)) {
      continue;
    }
    const html = readFileSync(htmlPath, "utf-8");
    if (html.includes("/v2/_next/")) {
      return "/v2";
    }
    if (html.includes("/_next/")) {
      return "";
    }
  }

  return "/v2";
}

writeFileSync(
  resolve(distDir, "version.json"),
  JSON.stringify(
    {
      version: pkg.version,
      buildHash: hash,
      builtAt: new Date().toISOString(),
      nodeVersion: process.version,
      basePath: detectBasePath(),
    },
    null,
    2
  )
);

console.log(`✅ ${distDirName}/ ready — hash: ${hash}`);
