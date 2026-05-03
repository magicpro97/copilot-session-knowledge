import { spawnSync } from "node:child_process";

const env = {
  ...process.env,
  NEXT_BASE_PATH: "",
  NEXT_DIST_DIR: "dist-release",
};

function run(command, args) {
  const result = spawnSync(command, args, {
    env,
    stdio: "inherit",
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

const nextCommand = process.platform === "win32" ? "next.cmd" : "next";

run(nextCommand, ["build"]);
run(process.execPath, ["scripts/post-build.mjs"]);
