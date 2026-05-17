const nodeGlobals = {
  Buffer: "readonly",
  __dirname: "readonly",
  clearInterval: "readonly",
  clearTimeout: "readonly",
  console: "readonly",
  global: "readonly",
  module: "readonly",
  process: "readonly",
  require: "readonly",
  setInterval: "readonly",
  setTimeout: "readonly",
};

const browserGlobals = {
  console: "readonly",
  document: "readonly",
  localStorage: "readonly",
  module: "readonly",
  URLSearchParams: "readonly",
  window: "readonly",
};

const advisoryRules = {
  complexity: ["warn", { max: 24 }],
  "max-lines": ["warn", { max: 900, skipBlankLines: true, skipComments: true }],
  "max-lines-per-function": ["warn", { max: 450, skipBlankLines: true, skipComments: true, IIFEs: true }],
  "max-params": ["warn", { max: 6 }],
  "no-unused-vars": [
    "warn",
    {
      argsIgnorePattern: "^_",
      caughtErrorsIgnorePattern: "^_",
      varsIgnorePattern: "^_",
    },
  ],
};

function promoteRulesToError(rules) {
  return Object.fromEntries(
    Object.entries(rules).map(([name, config]) => {
      if (Array.isArray(config)) {
        return [name, ["error", ...config.slice(1)]];
      }
      return [name, "error"];
    }),
  );
}

const cleanZoneRules = promoteRulesToError(advisoryRules);

export default [
  {
    ignores: ["coverage/**", "node_modules/**"],
  },
  {
    files: ["*.js", "test/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      globals: nodeGlobals,
      sourceType: "commonjs",
    },
    linterOptions: {
      reportUnusedDisableDirectives: "warn",
    },
    rules: advisoryRules,
  },
  {
    files: ["pty-daemon.js", "test/client.test.js"],
    rules: cleanZoneRules,
  },
  {
    files: ["public/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      globals: browserGlobals,
      sourceType: "script",
    },
    linterOptions: {
      reportUnusedDisableDirectives: "warn",
    },
    rules: advisoryRules,
  },
];
