import { expect, test as base, type ConsoleMessage, type Response } from "@playwright/test";

const IGNORED_CONSOLE_ERRORS = [
  /^Unchecked runtime\.lastError: Could not establish connection\. Receiving end does not exist\.?$/,
];

function isIgnoredConsoleError(message: string): boolean {
  return IGNORED_CONSOLE_ERRORS.some((pattern) => pattern.test(message));
}

function isLocalApiResponse(response: Response): boolean {
  return /^https?:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?\/api\//.test(response.url());
}

export const test = base.extend<{ runtimeErrorGuard: void }>({
  runtimeErrorGuard: [
    async ({ page }, use) => {
      const runtimeErrors: string[] = [];
      const seenApiErrors = new Set<string>();

      const onConsole = (message: ConsoleMessage) => {
        if (message.type() !== "error") return;
        const text = message.text();
        if (isIgnoredConsoleError(text)) return;
        runtimeErrors.push(`console.error: ${text}`);
      };

      const onPageError = (error: Error) => {
        runtimeErrors.push(`pageerror: ${error.stack || error.message}`);
      };

      // Browser devtools network 4xx/5xx lines do not surface through console.error,
      // so track same-origin API responses explicitly.
      const onResponse = (response: Response) => {
        if (!isLocalApiResponse(response) || response.status() < 400) return;

        const signature = `${response.status()} ${response.request().method()} ${response.url()}`;
        if (seenApiErrors.has(signature)) return;
        seenApiErrors.add(signature);
        runtimeErrors.push(`api.error: ${signature}`);
      };

      page.on("console", onConsole);
      page.on("pageerror", onPageError);
      page.on("response", onResponse);
      await use();
      page.off("console", onConsole);
      page.off("pageerror", onPageError);
      page.off("response", onResponse);

      if (runtimeErrors.length > 0) {
        throw new Error(`Unexpected browser runtime errors:\n${runtimeErrors.join("\n\n")}`);
      }
    },
    { auto: true },
  ],
});

export { expect };
