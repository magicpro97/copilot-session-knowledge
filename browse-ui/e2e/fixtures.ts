import { expect, test as base, type ConsoleMessage } from "@playwright/test";

const IGNORED_CONSOLE_ERRORS = [
  /^Unchecked runtime\.lastError: Could not establish connection\. Receiving end does not exist\.?$/,
];

function isIgnoredConsoleError(message: string): boolean {
  return IGNORED_CONSOLE_ERRORS.some((pattern) => pattern.test(message));
}

export const test = base.extend<{ runtimeErrorGuard: void }>({
  runtimeErrorGuard: [
    async ({ page }, use) => {
      const runtimeErrors: string[] = [];

      const onConsole = (message: ConsoleMessage) => {
        if (message.type() !== "error") return;
        const text = message.text();
        if (isIgnoredConsoleError(text)) return;
        runtimeErrors.push(`console.error: ${text}`);
      };

      const onPageError = (error: Error) => {
        runtimeErrors.push(`pageerror: ${error.stack || error.message}`);
      };

      page.on("console", onConsole);
      page.on("pageerror", onPageError);
      await use();
      page.off("console", onConsole);
      page.off("pageerror", onPageError);

      if (runtimeErrors.length > 0) {
        throw new Error(`Unexpected browser runtime errors:\n${runtimeErrors.join("\n\n")}`);
      }
    },
    { auto: true },
  ],
});

export { expect };
