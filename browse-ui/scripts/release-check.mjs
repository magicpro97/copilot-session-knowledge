import { spawnSync } from "node:child_process";

function run(command, args, env = process.env) {
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

const pnpmCommand = process.platform === "win32" ? "pnpm.cmd" : "pnpm";

run(process.execPath, ["scripts/build-release.mjs"]);
run(pnpmCommand, ["exec", "playwright", "test", "--project", "behavioral"], {
  ...process.env,
  FIREBASE_PROOF: "1",
});
