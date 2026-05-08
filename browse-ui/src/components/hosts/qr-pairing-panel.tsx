"use client";

import { useState } from "react";
import { QrCode, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

export interface QrPairingPanelProps {
  /** Called when a pairing ticket is successfully decoded. */
  onPaired: (baseUrl: string, isOpenAuth: boolean) => void;
  /** Called when the user cancels the pairing flow. */
  onCancel: () => void;
}

/**
 * QR / browse:// deep-link pairing panel (#58).
 *
 * Allows operators to pair a remote backend by scanning a QR code or entering
 * a `browse://` deep-link URL manually.
 */
export function QrPairingPanel({
  onPaired,
  onCancel,
  ...rest
}: QrPairingPanelProps & Record<string, unknown>) {
  const [manualUrl, setManualUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = manualUrl.trim();
    if (!trimmed) return;

    setLoading(true);
    setError(null);

    try {
      // Parse browse:// deep-link or plain HTTPS URL
      let baseUrl: string;
      let isOpenAuth = false;

      if (trimmed.startsWith("browse://")) {
        const parsed = new URL(trimmed.replace("browse://", "https://"));
        baseUrl = `${parsed.protocol}//${parsed.host}`;
        isOpenAuth = parsed.searchParams.get("auth") === "open";
      } else {
        baseUrl = trimmed.replace(/\/+$/, "");
        isOpenAuth = false;
      }

      onPaired(baseUrl, isOpenAuth);
    } catch {
      setError("Invalid URL — enter an HTTPS URL or browse:// deep-link.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border px-4 py-3" data-testid="qr-pairing-panel" {...rest}>
      <div className="flex items-center gap-2 text-sm font-medium">
        <QrCode className="size-4" />
        QR / Deep-link pairing
      </div>

      <form onSubmit={handleSubmit} className="space-y-2">
        <input
          type="text"
          value={manualUrl}
          onChange={(e) => setManualUrl(e.target.value)}
          placeholder="browse://host.example.com or https://..."
          className="border-input bg-background w-full rounded-md border px-3 py-1.5 text-sm"
          data-testid="qr-manual-url-input"
        />

        {error ? (
          <p className="text-xs text-red-500" data-testid="qr-pairing-error">
            {error}
          </p>
        ) : null}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={loading || !manualUrl.trim()}>
            {loading ? <Loader2 className="mr-1 size-3 animate-spin" /> : null}
            Pair
          </Button>
        </div>
      </form>
    </div>
  );
}
