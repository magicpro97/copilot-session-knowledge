#!/usr/bin/env node
/**
 * verify-deploy.mjs
 *
 * Live verification of a deployed hosted browse-UI:
 *  1. Fetches `<origin>/version.json` and prints buildHash / builtAt / basePath.
 *     If --expected-sha (or env EXPECTED_SHA) is provided, asserts that the
 *     hosted buildHash starts with that value (short-SHA tolerant).
 *  2. Fetches `<origin>/` HEAD and asserts the HTML cache-control directive
 *     forces revalidation (no-cache | no-store | must-revalidate | max-age=0).
 *     This prevents the "stale app-shell after deploy" failure mode where
 *     users keep a pre-fix HTML referencing old hashed chunks for an hour.
 *  3. Fetches a representative `/_next/static/...` chunk URL discovered from
 *     the HTML and asserts immutable caching is still in place.
 *
 * This script performs LIVE network calls. It is intentionally not wired into
 * the default `release:check` build gate. Run it after a deploy:
 *
 *   pnpm verify:deploy                              # against agents.linhngo.dev
 *   pnpm verify:deploy --origin https://example.app
 *   EXPECTED_SHA=abcdef1 pnpm verify:deploy
 *
 * Exits non-zero with an actionable message on any failure.
 */
import { argv, env, exit } from "node:process";

const DEFAULT_ORIGIN = "https://agents.linhngo.dev";

function parseArgs(args) {
  const out = { origin: DEFAULT_ORIGIN, expectedSha: env.EXPECTED_SHA || "" };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--origin" && args[i + 1]) {
      out.origin = args[++i];
    } else if (a.startsWith("--origin=")) {
      out.origin = a.slice("--origin=".length);
    } else if (a === "--expected-sha" && args[i + 1]) {
      out.expectedSha = args[++i];
    } else if (a.startsWith("--expected-sha=")) {
      out.expectedSha = a.slice("--expected-sha=".length);
    } else if (a === "-h" || a === "--help") {
      console.log(
        "Usage: verify-deploy.mjs [--origin https://host] [--expected-sha <short-sha>] [--self-check]"
      );
      exit(0);
    }
  }
  out.origin = out.origin.replace(/\/+$/, "");
  return out;
}

const failures = [];
function fail(msg) {
  failures.push(msg);
  console.error(`❌ ${msg}`);
}
function ok(msg) {
  console.log(`✅ ${msg}`);
}
function info(msg) {
  console.log(`ℹ️  ${msg}`);
}

function revalidates(cacheControl) {
  if (!cacheControl) return false;
  const v = cacheControl.toLowerCase();
  // Per HTTP semantics, `must-revalidate` alone only forces revalidation AFTER
  // the response becomes stale; with a non-zero max-age (or none) the browser
  // can still serve the cached app-shell HTML without contacting origin. To
  // guarantee the browser revalidates on every navigation, require one of:
  //   - no-store
  //   - no-cache
  //   - explicit max-age=0
  return v.includes("no-store") || v.includes("no-cache") || /\bmax-age\s*=\s*0\b/.test(v);
}

async function fetchHead(url) {
  // Some CDNs collapse HEAD; fall back to GET if HEAD fails or returns >=400.
  try {
    const r = await fetch(url, { method: "HEAD", redirect: "follow" });
    if (r.status >= 200 && r.status < 400) return r;
  } catch {
    /* fallthrough to GET */
  }
  const g = await fetch(url, { method: "GET", redirect: "follow" });
  if (g.status >= 400) {
    throw new Error(
      `${url} returned HTTP ${g.status} on both HEAD and GET; cache headers from an error response are not production proof.`
    );
  }
  return g;
}

async function main() {
  const args = argv.slice(2);
  if (args.includes("--self-check")) {
    return selfCheck();
  }
  const { origin, expectedSha } = parseArgs(args);
  info(`origin = ${origin}`);
  if (expectedSha) info(`expected buildHash prefix = ${expectedSha}`);

  // 1. version.json
  let version = null;
  try {
    const r = await fetch(`${origin}/version.json`, { redirect: "follow" });
    if (!r.ok) {
      fail(`GET /version.json -> HTTP ${r.status}`);
    } else {
      version = await r.json();
      info(
        `version.json: buildHash=${version.buildHash} builtAt=${version.builtAt} basePath=${JSON.stringify(version.basePath ?? "")}`
      );
      const vcc = r.headers.get("cache-control") || "";
      if (revalidates(vcc)) {
        ok(`version.json Cache-Control revalidates ("${vcc}")`);
      } else {
        fail(
          `version.json Cache-Control does not revalidate ("${vcc}"). ` +
            `Operators may see a stale buildHash. Fix: add a no-cache rule for /version.json in firebase.json.`
        );
      }
      if (expectedSha) {
        const got = String(version.buildHash || "");
        if (got.startsWith(expectedSha)) {
          ok(`hosted buildHash matches expected (${got} starts with ${expectedSha})`);
        } else {
          fail(
            `hosted buildHash ${got} does NOT start with expected ${expectedSha}. ` +
              `Deploy is stale or pointed at a different commit. ` +
              `Fix: re-run \`pnpm release:check\` and \`firebase deploy --only hosting:agents\` from the hosting repo.`
          );
        }
      }
    }
  } catch (e) {
    fail(`GET /version.json failed: ${e.message}`);
  }

  // 2. App-shell HTML cache-control. Probe the root AND a representative
  //    rewrite/cleanUrls path (e.g. /sessions/<id>) because Firebase header
  //    rules are source-pattern-scoped: a rule that only matches "/" or
  //    "**/*.html" can leave rewrite targets with the default CDN max-age,
  //    keeping a stale app-shell HTML for that path even after a deploy.
  const htmlProbePaths = [
    "/",
    "/chat/",
    "/sessions/_verify",
    "/search/",
    "/insights/",
    "/graph/",
    "/settings/",
    "/diagnostics/",
  ];
  let html = "";
  for (const path of htmlProbePaths) {
    try {
      const r = await fetchHead(`${origin}${path}`);
      const cc = r.headers.get("cache-control") || "";
      if (revalidates(cc)) {
        ok(`${path} Cache-Control revalidates ("${cc}")`);
      } else {
        fail(
          `${path} Cache-Control does not revalidate ("${cc}"). ` +
            `Users will keep a stale app-shell HTML for the full max-age, even after a successful deploy. ` +
            `Fix: ensure firebase.json sets no-cache/max-age=0 for "${path}" (HTML routes including cleanUrls/rewrites).`
        );
      }
      if (path === "/") {
        // Pull the root body so we can sample a chunk URL below.
        const bodyResp = await fetch(`${origin}/`, { redirect: "follow" });
        html = await bodyResp.text();
      }
    } catch (e) {
      fail(`HEAD ${path} failed: ${e.message}`);
    }
  }

  // 3. Sample a hashed JS/CSS chunk and verify immutable caching is preserved.
  //    Fonts/images intentionally use a shorter cache (firebase.json), so probe
  //    a .js/.css chunk under /_next/static/ which is the immutable-by-design path.
  try {
    const matches = [...html.matchAll(/\/_next\/static\/[^"'\s)]+\.(?:js|css)/g)];
    const pick = matches[0]?.[0];
    if (!pick) {
      info(
        "No hashed /_next/static/**.{js,css} reference found in / HTML — skipping immutable-cache probe."
      );
    } else {
      const chunkUrl = `${origin}${pick}`;
      info(`probing static chunk: ${pick}`);
      const r = await fetchHead(chunkUrl);
      const cc = r.headers.get("cache-control") || "";
      if (/immutable/.test(cc) && /max-age\s*=\s*\d{5,}/.test(cc)) {
        ok(`/_next/static/** Cache-Control is long-lived & immutable ("${cc}")`);
      } else {
        fail(
          `/_next/static/** Cache-Control is NOT immutable ("${cc}"). ` +
            `Long-cache for hashed chunks is required; verify firebase.json /_next/static/** rule.`
        );
      }
    }
  } catch (e) {
    fail(`static chunk probe failed: ${e.message}`);
  }

  if (failures.length) {
    console.error(`\nverify-deploy: ${failures.length} check(s) failed against ${origin}.`);
    exit(1);
  }
  console.log(`\nverify-deploy: all checks passed against ${origin}.`);
}

main().catch((e) => {
  console.error(`verify-deploy: unexpected error: ${e?.stack || e}`);
  exit(2);
});

function selfCheck() {
  // Offline self-check of the revalidates() predicate so CI/operators can run
  // `node scripts/verify-deploy.mjs --self-check` without a live origin.
  const cases = [
    // [input, expectedResult, label]
    ["no-store", true, "no-store"],
    ["no-cache", true, "no-cache"],
    ["public, max-age=0", true, "max-age=0"],
    ["no-cache, must-revalidate, max-age=0", true, "combined"],
    ["must-revalidate", false, "must-revalidate alone must FAIL"],
    ["public, max-age=3600, must-revalidate", false, "must-revalidate + max-age>0 must FAIL"],
    ["public, max-age=31536000, immutable", false, "immutable long max-age must FAIL"],
    ["", false, "empty must FAIL"],
  ];
  let bad = 0;
  for (const [input, expected, label] of cases) {
    const got = revalidates(input);
    if (got === expected) {
      console.log(`✅ self-check: ${label} -> ${got}`);
    } else {
      console.error(`❌ self-check: ${label} expected ${expected} got ${got} for "${input}"`);
      bad++;
    }
  }
  if (bad) {
    console.error(`\nverify-deploy --self-check: ${bad} predicate case(s) failed.`);
    exit(1);
  }
  console.log("\nverify-deploy --self-check: revalidates() predicate OK.");
  exit(0);
}
