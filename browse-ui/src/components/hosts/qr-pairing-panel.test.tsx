import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const parsePairingUrl = vi.hoisted(() => vi.fn());
const looksLikePairingUrl = vi.hoisted(() => vi.fn(() => true));
const verifyPairingTicketServer = vi.hoisted(() => vi.fn());

vi.mock("@/lib/hosts/pairing", () => ({
  parsePairingUrl,
  looksLikePairingUrl,
  verifyPairingTicketServer,
}));

const { QrPairingPanel } = await import("@/components/hosts/qr-pairing-panel");

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("QrPairingPanel", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("shows a visible warning when server verification is unreachable", async () => {
    parsePairingUrl.mockReturnValue({
      ok: true,
      baseUrl: "http://127.0.0.1:8765",
      isOpenAuth: true,
    });
    verifyPairingTicketServer.mockResolvedValue({
      ok: false,
      error: "Failed to fetch",
      serverReachable: false,
    });

    render(<QrPairingPanel onPaired={vi.fn()} onCancel={vi.fn()} />);

    fireEvent.click(screen.getByTestId("qr-paste-url-btn"));
    fireEvent.change(screen.getByTestId("qr-paste-input"), {
      target: { value: "browse://connect?ticket=dummy-ticket" },
    });
    fireEvent.click(screen.getByTestId("qr-confirm-paste-btn"));

    await waitFor(() => expect(screen.getByTestId("qr-pairing-success")).toBeInTheDocument());
    expect(verifyPairingTicketServer).toHaveBeenCalledWith(
      "http://127.0.0.1:8765",
      "browse://connect?ticket=dummy-ticket"
    );
    expect(screen.getByTestId("qr-pairing-warning")).toHaveTextContent(
      "Proceeding with client-side check only"
    );
  });

  it("ignores stale camera results after switching to paste mode", async () => {
    const pendingDetect = deferred<Array<{ rawValue: string }>>();
    const detect = vi.fn(() => pendingDetect.promise);
    const stopTrack = vi.fn();
    const mockTrack = { stop: stopTrack } as unknown as MediaStreamTrack;
    const mockStream = { getTracks: () => [mockTrack] } as unknown as MediaStream;

    class MockBarcodeDetector {
      detect = detect;
    }

    vi.stubGlobal("BarcodeDetector", MockBarcodeDetector);
    Object.defineProperty(navigator, "mediaDevices", {
      value: {
        getUserMedia: vi.fn().mockResolvedValue(mockStream),
      } satisfies Pick<MediaDevices, "getUserMedia">,
      configurable: true,
    });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(
      HTMLMediaElement.HAVE_ENOUGH_DATA
    );

    const onPaired = vi.fn();
    render(<QrPairingPanel onPaired={onPaired} onCancel={vi.fn()} />);

    fireEvent.click(screen.getByTestId("qr-scan-camera-btn"));
    await waitFor(() => expect(screen.getByTestId("qr-switch-to-paste-btn")).toBeInTheDocument());
    expect(detect).toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("qr-switch-to-paste-btn"));
    await waitFor(() => expect(screen.getByTestId("qr-paste-input")).toBeInTheDocument());

    pendingDetect.resolve([{ rawValue: "browse://connect?ticket=late-ticket" }]);
    await Promise.resolve();
    await Promise.resolve();

    expect(parsePairingUrl).not.toHaveBeenCalled();
    expect(onPaired).not.toHaveBeenCalled();
    expect(screen.getByTestId("qr-paste-input")).toBeInTheDocument();
    expect(stopTrack).toHaveBeenCalled();
  });

  it("does not call onPaired after the user closes the panel during the success delay", async () => {
    parsePairingUrl.mockReturnValue({
      ok: true,
      baseUrl: "http://127.0.0.1:8765",
      isOpenAuth: true,
    });
    verifyPairingTicketServer.mockResolvedValue({
      ok: true,
      baseUrl: "http://127.0.0.1:8765",
      serverVerified: true,
    });

    const onPaired = vi.fn();
    const onCancel = vi.fn();
    render(<QrPairingPanel onPaired={onPaired} onCancel={onCancel} />);

    fireEvent.click(screen.getByTestId("qr-paste-url-btn"));
    fireEvent.change(screen.getByTestId("qr-paste-input"), {
      target: { value: "browse://connect?ticket=dummy-ticket" },
    });
    fireEvent.click(screen.getByTestId("qr-confirm-paste-btn"));

    await waitFor(() => expect(screen.getByTestId("qr-pairing-success")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("qr-pairing-close-btn"));
    await new Promise((resolve) => setTimeout(resolve, 900));

    expect(onCancel).toHaveBeenCalled();
    expect(onPaired).not.toHaveBeenCalled();
  });

  it("does not call onPaired if the panel closes while server verification is still pending", async () => {
    const pendingVerify = deferred<{
      ok: true;
      baseUrl: string;
      serverVerified: true;
    }>();
    parsePairingUrl.mockReturnValue({
      ok: true,
      baseUrl: "http://127.0.0.1:8765",
      isOpenAuth: true,
    });
    verifyPairingTicketServer.mockReturnValue(pendingVerify.promise);

    const onPaired = vi.fn();
    const onCancel = vi.fn();
    render(<QrPairingPanel onPaired={onPaired} onCancel={onCancel} />);

    fireEvent.click(screen.getByTestId("qr-paste-url-btn"));
    fireEvent.change(screen.getByTestId("qr-paste-input"), {
      target: { value: "browse://connect?ticket=dummy-ticket" },
    });
    fireEvent.click(screen.getByTestId("qr-confirm-paste-btn"));
    await waitFor(() => expect(verifyPairingTicketServer).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("qr-pairing-close-btn"));
    await act(async () => {
      pendingVerify.resolve({
        ok: true,
        baseUrl: "http://127.0.0.1:8765",
        serverVerified: true,
      });
      await Promise.resolve();
      await Promise.resolve();
    });
    await new Promise((resolve) => setTimeout(resolve, 900));

    expect(onCancel).toHaveBeenCalled();
    expect(onPaired).not.toHaveBeenCalled();
    expect(screen.queryByTestId("qr-pairing-success")).not.toBeInTheDocument();
  });
});
