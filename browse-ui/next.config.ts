import type { NextConfig } from "next";

// NEXT_BASE_PATH controls the Next.js basePath at build time.
//   - Default (local + hosted): unset → "" (root-relative assets at /_next/…)
//   - Override: NEXT_BASE_PATH="/v2" for legacy Python-server deployments still under /v2/*
const basePath =
  process.env.NEXT_BASE_PATH !== undefined
    ? process.env.NEXT_BASE_PATH
    : "";
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
