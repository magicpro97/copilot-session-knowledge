import type { NextConfig } from "next";

const config: NextConfig = {
  output: "export",
  basePath: "/v2",
  distDir: "dist",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default config;
