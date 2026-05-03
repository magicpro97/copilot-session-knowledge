import type { NextConfig } from "next";

// NEXT_BASE_PATH controls the Next.js basePath at build time.
//   - Local browse (default): unset → "/v2"  (served by browse/routes/serve_v2.py under /v2/*)
//   - Firebase release build: NEXT_BASE_PATH="" → "" (root-relative assets at /_next/…)
const basePath =
  process.env.NEXT_BASE_PATH !== undefined
    ? process.env.NEXT_BASE_PATH
    : "/v2";
const distDir = process.env.NEXT_DIST_DIR || "dist";

const config: NextConfig = {
  output: "export",
  basePath,
  distDir,
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default config;
