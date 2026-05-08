"use client";

/**
 * browse-ui/src/components/hosts/qr-pairing-panel.tsx
 *
 * QR / browse:// pairing panel (issue #58).
 *
 * Provides two pairing paths:
 * 1. Camera scan — uses BarcodeDetector API (Chromium/Edge) to scan a QR code
 *    displayed in the operator's terminal.
 * 2. Paste browse:// URL — manual fallback that works everywhere.
 *
 * On successful parse the panel calls onPaired(baseUrl, isOpenAuth) so the
 * parent (HostManagement) can pre-fill and auto-advance the add-host form.
 *
 * Scope: browse-ui/src/components/hosts/**  (in-scope per CONTEXT.md).
 * Out of scope: host-profiles.ts, session-create-dialog.tsx.
 */

import { useState, useRef, useCallback, useEffect } from "react";
import { AlertCircle, Camera, ClipboardPaste, CheckCircle2, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  parsePairingUrl,
  looksLikePairingUrl,
  verifyPairingTicketServer,
} from "@/lib/hosts/pairing";

interface QrPairingPanelProps {
  /** Called when a valid pairing ticket is decoded. */
  onPaired: (baseUrl: string, isOpenAuth: boolean) => void;
  /** Called when the panel is dismissed without pairing. */
  onCancel: () => void;
  className?: string;
}

type ScanMode = "idle" | "camera" | "paste" | "scanning" | "success" | "error";

/** True when the browser exposes a usable BarcodeDetector for QR codes. */
function browserHasBarcodeDetector(): boolean {
  if (typeof window === "undefined") return false;
  // BarcodeDetector is available in Chromium/Edge; not in Firefox/Safari yet.
  return typeof (window as unknown as Record<string, unknown>)["BarcodeDetector"] === "function";
}

function stopMediaStream(stream: MediaStream | null) {
  if (!stream) return;
  stream.getTracks().forEach((track) => track.stop());
}

export function QrPairingPanel({ onPaired, onCancel, className }: QrPairingPanelProps) {
  const [mode, setMode] = useState<ScanMode>("idle");
  const [pasteValue, setPasteValue] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [warningMsg, setWarningMsg] = useState<string | null>(null);
  const [successUrl, setSuccessUrl] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const scanningRef = useRef(false);
  const cameraSessionRef = useRef(0);
  const pairingSessionRef = useRef(0);
  const pairedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasBarcodeDetector = browserHasBarcodeDetector();

  const clearPendingPair = useCallback(() => {
    if (pairedTimeoutRef.current === null) return;
    clearTimeout(pairedTimeoutRef.current);
    pairedTimeoutRef.current = null;
  }, []);

  const cancelPendingPairing = useCallback(() => {
    pairingSessionRef.current += 1;
    clearPendingPair();
  }, [clearPendingPair]);

  // Stop camera stream when unmounting or leaving camera mode.
  const stopCamera = useCallback(() => {
    cameraSessionRef.current += 1;
    scanningRef.current = false;
    stopMediaStream(streamRef.current);
    streamRef.current = null;
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      cancelPendingPairing();
      stopCamera();
    };
  }, [cancelPendingPairing, stopCamera]);

  // ── Camera scanning ──────────────────────────────────────────────────────────

  async function startCameraScanning() {
    setMode("camera");
    setErrorMsg(null);
    setWarningMsg(null);
    const cameraSession = cameraSessionRef.current + 1;
    cameraSessionRef.current = cameraSession;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
      });
      if (cameraSession !== cameraSessionRef.current) {
        stopMediaStream(stream);
        return;
      }
      streamRef.current = stream;
      if (!videoRef.current) {
        stopMediaStream(stream);
        if (streamRef.current === stream) {
          streamRef.current = null;
        }
        return;
      }
      videoRef.current.srcObject = stream;
      await videoRef.current.play();
      if (cameraSession !== cameraSessionRef.current || !videoRef.current) {
        stopMediaStream(stream);
        if (streamRef.current === stream) {
          streamRef.current = null;
        }
        return;
      }
      scanningRef.current = true;
      void runBarcodeLoop(cameraSession);
    } catch (err) {
      if (cameraSession !== cameraSessionRef.current) {
        return;
      }
      setMode("error");
      setErrorMsg(
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "Camera permission denied. Use 'Paste browse:// URL' instead."
          : `Camera unavailable: ${err instanceof Error ? err.message : String(err)}`
      );
    }
  }

  async function runBarcodeLoop(cameraSession: number) {
    const BarcodeDetectorClass = (
      window as unknown as {
        BarcodeDetector: new (opts: { formats: string[] }) => {
          detect: (source: HTMLVideoElement) => Promise<Array<{ rawValue: string }>>;
        };
      }
    )["BarcodeDetector"];

    if (!BarcodeDetectorClass || !videoRef.current || cameraSession !== cameraSessionRef.current) {
      return;
    }

    const detector = new BarcodeDetectorClass({ formats: ["qr_code"] });

    while (scanningRef.current && cameraSession === cameraSessionRef.current) {
      try {
        if (videoRef.current.readyState >= HTMLMediaElement.HAVE_ENOUGH_DATA) {
          const barcodes = await detector.detect(videoRef.current);
          if (!scanningRef.current || cameraSession !== cameraSessionRef.current) return;
          for (const barcode of barcodes) {
            if (barcode.rawValue && looksLikePairingUrl(barcode.rawValue)) {
              void handlePairingInput(barcode.rawValue);
              stopCamera();
              return;
            }
          }
        }
      } catch {
        // BarcodeDetector may throw on empty frames — continue scanning.
      }
      await new Promise<void>((r) => setTimeout(r, 200));
    }
  }

  // ── Pairing input processing ─────────────────────────────────────────────────

  async function handlePairingInput(input: string) {
    setMode("scanning");
    setErrorMsg(null);
    setWarningMsg(null);
    const pairingSession = pairingSessionRef.current + 1;
    pairingSessionRef.current = pairingSession;
    const result = parsePairingUrl(input);
    if (!result.ok) {
      setMode("error");
      setErrorMsg(result.error);
      return;
    }

    // Attempt authoritative server-side HMAC verification (#58).
    // The /.well-known/browse-host/verify endpoint is open (no auth required),
    // so we can call it before the user has provided a token.
    const serverResult = await verifyPairingTicketServer(result.baseUrl, input.trim());
    if (pairingSession !== pairingSessionRef.current) {
      return;
    }
    if (!serverResult.ok) {
      if (serverResult.serverReachable) {
        // Server rejected the ticket — surface the authoritative error.
        setMode("error");
        setErrorMsg(`Server rejected ticket: ${serverResult.error}`);
        return;
      }
      // Server unreachable (CORS/network/offline) — fall back to client-only validation
      // with a visible warning so the user knows verification was not authoritative.
      setWarningMsg(
        `⚠️ Could not reach server for verification (${serverResult.error}). ` +
          `Proceeding with client-side check only.`
      );
    }

    setSuccessUrl(result.baseUrl);
    setMode("success");
    // Brief success flash, then notify parent.
    clearPendingPair();
    pairedTimeoutRef.current = setTimeout(() => {
      if (pairingSession !== pairingSessionRef.current) {
        pairedTimeoutRef.current = null;
        return;
      }
      pairedTimeoutRef.current = null;
      onPaired(result.baseUrl, result.isOpenAuth);
    }, 800);
  }

  function handlePasteSubmit() {
    if (!pasteValue.trim()) return;
    void handlePairingInput(pasteValue.trim());
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div
      className={cn("space-y-3 rounded-lg border border-dashed p-4", className)}
      data-testid="qr-pairing-panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">Pair via QR / browse:// URL</p>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-6"
          onClick={() => {
            cancelPendingPairing();
            stopCamera();
            onCancel();
          }}
          aria-label="Close QR pairing panel"
          data-testid="qr-pairing-close-btn"
        >
          <X className="size-3.5" />
        </Button>
      </div>

      <p className="text-muted-foreground text-xs">
        In your terminal, run{" "}
        <code className="bg-muted rounded px-1 font-mono">browse.py --print-pairing-qr</code> to
        generate a pairing code. Then scan the QR or paste the{" "}
        <code className="bg-muted rounded px-1 font-mono">browse://</code> URL below.
      </p>

      {/* Mode: idle — show action buttons */}
      {mode === "idle" && (
        <div className="flex flex-wrap gap-2">
          {hasBarcodeDetector && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={() => void startCameraScanning()}
              data-testid="qr-scan-camera-btn"
            >
              <Camera className="mr-1.5 size-3.5" />
              Scan QR with camera
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => {
              setMode("paste");
              setErrorMsg(null);
              setWarningMsg(null);
            }}
            data-testid="qr-paste-url-btn"
          >
            <ClipboardPaste className="mr-1.5 size-3.5" />
            Paste browse:// URL
          </Button>
        </div>
      )}

      {/* Mode: camera */}
      {mode === "camera" && (
        <div className="space-y-2">
          <div className="relative aspect-video max-h-48 overflow-hidden rounded-md bg-black">
            <video
              ref={videoRef}
              className="size-full object-cover"
              playsInline
              muted
              aria-label="Camera viewfinder for QR scanning"
              data-testid="qr-camera-viewfinder"
            />
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="size-32 rounded border-2 border-white/60 opacity-80" />
            </div>
          </div>
          <p className="text-muted-foreground text-xs">
            Point the camera at the QR code in your terminal…
          </p>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 text-xs"
            onClick={() => {
              stopCamera();
              setMode("paste");
              setWarningMsg(null);
            }}
            data-testid="qr-switch-to-paste-btn"
          >
            Switch to paste URL instead
          </Button>
        </div>
      )}

      {/* Mode: paste */}
      {mode === "paste" && (
        <div className="space-y-2">
          <input
            type="text"
            value={pasteValue}
            onChange={(e) => setPasteValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handlePasteSubmit();
            }}
            placeholder="browse://connect?ticket=..."
            aria-label="Paste browse:// pairing URL"
            className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-1.5 font-mono text-xs outline-none focus-visible:ring-2"
            autoFocus
            data-testid="qr-paste-input"
          />
          <div className="flex gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => {
                setMode("idle");
                setPasteValue("");
                setErrorMsg(null);
                setWarningMsg(null);
              }}
              data-testid="qr-back-btn"
            >
              Back
            </Button>
            <Button
              type="button"
              size="sm"
              className="h-7 text-xs"
              onClick={handlePasteSubmit}
              disabled={!pasteValue.trim()}
              data-testid="qr-confirm-paste-btn"
            >
              Use this URL
            </Button>
          </div>
        </div>
      )}

      {/* Mode: scanning — processing */}
      {mode === "scanning" && (
        <div className="flex items-center gap-2 text-xs">
          <Loader2 className="size-3.5 animate-spin" />
          <span>Parsing ticket…</span>
        </div>
      )}

      {/* Mode: success */}
      {mode === "success" && successUrl && (
        <div className="space-y-2">
          <div
            className="flex items-start gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-xs"
            data-testid="qr-pairing-success"
          >
            <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-500" />
            <p>
              Paired with <span className="font-mono font-medium">{successUrl}</span> — pre-filling
              host form…
            </p>
          </div>
          {warningMsg && (
            <div
              className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs"
              data-testid="qr-pairing-warning"
              role="status"
            >
              <AlertCircle className="mt-0.5 size-3.5 shrink-0 text-amber-500" />
              <p className="text-amber-700 dark:text-amber-300">{warningMsg}</p>
            </div>
          )}
        </div>
      )}

      {/* Mode: error */}
      {mode === "error" && errorMsg && (
        <div
          className="border-destructive/30 bg-destructive/5 flex items-start gap-2 rounded-md border px-3 py-2 text-xs"
          data-testid="qr-pairing-error"
          role="alert"
        >
          <AlertCircle className="text-destructive mt-0.5 size-3.5 shrink-0" />
          <div className="space-y-1">
            <p className="text-destructive">{errorMsg}</p>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={() => {
                setMode("idle");
                setErrorMsg(null);
                setPasteValue("");
              }}
              data-testid="qr-retry-btn"
            >
              Try again
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
