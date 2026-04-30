import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MindmapViewer } from "@/components/data/mindmap-viewer";

const mockRescale = vi.fn().mockResolvedValue(undefined);
const mockFit = vi.fn().mockResolvedValue(undefined);
const mockSetData = vi.fn().mockResolvedValue(undefined);
const mockDestroy = vi.fn();

const mockInstance = {
  rescale: mockRescale,
  fit: mockFit,
  setData: mockSetData,
  destroy: mockDestroy,
};

vi.mock("markmap-view", () => ({
  Markmap: {
    create: vi.fn(() => mockInstance),
  },
}));

vi.mock("markmap-lib", () => ({
  Transformer: class {
    transform = vi.fn(() => ({ root: { payload: {}, children: [] } }));
  },
}));

function rect(width: number, height: number): DOMRect {
  return {
    width,
    height,
    x: 0,
    y: 0,
    top: 0,
    right: width,
    bottom: height,
    left: 0,
    toJSON: () => ({}),
  } as DOMRect;
}

function mockViewport(width: number, height: number) {
  vi.spyOn(SVGElement.prototype, "getBoundingClientRect").mockReturnValue(rect(width, height));
}

async function renderReady() {
  render(<MindmapViewer markdown="# Test" />);
  await waitFor(() => expect(mockSetData).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByRole("button", { name: /zoom in/i })).not.toBeDisabled());
  mockFit.mockClear();
  mockRescale.mockClear();
  mockSetData.mockClear();
}

describe("MindmapViewer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockViewport(800, 540);
    // Restore default mock implementations after clearAllMocks
    mockRescale.mockResolvedValue(undefined);
    mockFit.mockResolvedValue(undefined);
    mockSetData.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders Zoom In, Zoom Out, Fit, Expand, and Collapse buttons", () => {
    render(<MindmapViewer markdown="# Test" />);

    expect(screen.getByRole("button", { name: /zoom in/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /zoom out/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^fit$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^expand$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^collapse$/i })).toBeInTheDocument();
  });

  it("clicking Zoom In calls rescale with the relative zoom factor", async () => {
    await renderReady();

    fireEvent.click(screen.getByRole("button", { name: /zoom in/i }));

    expect(mockRescale).toHaveBeenCalledTimes(1);
    expect(mockRescale).toHaveBeenCalledWith(expect.closeTo(1.25, 5));
  });

  it("clicking Zoom Out calls rescale with the inverse relative zoom factor", async () => {
    await renderReady();

    fireEvent.click(screen.getByRole("button", { name: /zoom out/i }));

    expect(mockRescale).toHaveBeenCalledTimes(1);
    expect(mockRescale).toHaveBeenCalledWith(expect.closeTo(0.8, 5));
  });

  it("clicking Zoom In does not call fit or setData", async () => {
    await renderReady();

    fireEvent.click(screen.getByRole("button", { name: /zoom in/i }));

    expect(mockFit).not.toHaveBeenCalled();
    expect(mockSetData).not.toHaveBeenCalled();
  });

  it("clicking Zoom Out does not call fit or setData", async () => {
    await renderReady();

    fireEvent.click(screen.getByRole("button", { name: /zoom out/i }));

    expect(mockFit).not.toHaveBeenCalled();
    expect(mockSetData).not.toHaveBeenCalled();
  });

  it("clicking Fit calls fit and does not call rescale or setData", async () => {
    await renderReady();

    fireEvent.click(screen.getByRole("button", { name: /^fit$/i }));

    await waitFor(() => expect(mockFit).toHaveBeenCalledTimes(1));
    expect(mockRescale).not.toHaveBeenCalled();
    expect(mockSetData).not.toHaveBeenCalled();
  });

  it("clicking Expand calls setData and fit, not rescale", async () => {
    await renderReady();

    fireEvent.click(screen.getByRole("button", { name: /^expand$/i }));

    expect(mockSetData).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(mockFit).toHaveBeenCalledTimes(1));
    expect(mockRescale).not.toHaveBeenCalled();
  });

  it("clicking Collapse calls setData and fit, not rescale", async () => {
    await renderReady();

    fireEvent.click(screen.getByRole("button", { name: /^collapse$/i }));

    expect(mockSetData).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(mockFit).toHaveBeenCalledTimes(1));
    expect(mockRescale).not.toHaveBeenCalled();
  });

  it("resets instead of rescaling when the stored zoom transform is invalid", async () => {
    await renderReady();
    const svg = document.querySelector<SVGSVGElement>('svg[aria-label="Session mindmap"]');
    expect(svg).not.toBeNull();
    const svgWithZoom = svg as SVGSVGElement & { __zoom?: { k: number; x: number; y: number } };
    svgWithZoom.__zoom = { k: Number.NaN, x: 0, y: 0 };

    fireEvent.click(screen.getByRole("button", { name: /zoom in/i }));

    expect(mockRescale).not.toHaveBeenCalled();
    expect(mockFit).toHaveBeenCalledTimes(1);
  });

  it("keeps zoom controls disabled when the SVG has no viewport", async () => {
    mockViewport(0, 0);

    render(<MindmapViewer markdown="# Test" />);

    await waitFor(() => expect(mockSetData).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: /zoom in/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /zoom out/i })).toBeDisabled();
    expect(mockFit).not.toHaveBeenCalled();
  });
});
