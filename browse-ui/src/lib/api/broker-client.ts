/**
 * broker-client.ts — Telegram broker helpers.
 *
 * Provides URL construction for the Telegram bot deep-link pairing flow (#65).
 */

/**
 * Constructs a Telegram bot deep-link URL for the given bot name and pairing token.
 *
 * Example: `getTelegramBotUrl("my_bot", "abc123")` → `"https://t.me/my_bot?start=abc123"`
 */
export function getTelegramBotUrl(botName: string, token: string): string {
  const encoded = encodeURIComponent(token);
  return `https://t.me/${encodeURIComponent(botName)}?start=${encoded}`;
}
