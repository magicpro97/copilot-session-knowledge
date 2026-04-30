import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

describe("MindmapViewer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Restore default mock implementations after clearAllMocks
    mockRescale.mockResolvedValue(undefined);
    mockFit.mockResolvedValue(undefined);
    mockSetData.mockResolvedValue(undefined);
  });

  it("renders Zoom In, Zoom Out, Fit, Expand, and Collapse buttons", () => {
    render(<MindmapViewer markdown="# Test" />);

    expect(screen.getByRole("button", { name: /zoom in/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /zoom out/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^fit$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^expand$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^collapse$/i })).toBeInTheDocument();
  });

  it("clicking Zoom In calls rescale with scale multiplied by ZOOM_STEP", () => {
    render(<MindmapViewer markdown="# Test" />);
    // Clear the fit call from the initial useEffect render
    mockFit.mockClear();

    fireEvent.click(screen.getByRole("button", { name: /zoom in/i }));

    expect(mockRescale).toHaveBeenCalledTimes(1);
    // __zoom is not set on jsdom SVG element, so currentScale defaults to 1
    expect(mockRescale).toHaveBeenCalledWith(expect.closeTo(1.25, 5));
  });

  it("clicking Zoom Out calls rescale with scale divided by ZOOM_STEP", () => {
    render(<MindmapViewer markdown="# Test" />);
    mockFit.mockClear();

    fireEvent.click(screen.getByRole("button", { name: /zoom out/i }));

    expect(mockRescale).toHaveBeenCalledTimes(1);
    expect(mockRescale).toHaveBeenCalledWith(expect.closeTo(0.8, 5));
  });

  it("clicking Zoom In does not call fit or setData", () => {
    render(<MindmapViewer markdown="# Test" />);
    mockFit.mockClear();

    fireEvent.click(screen.getByRole("button", { name: /zoom in/i }));

    expect(mockFit).not.toHaveBeenCalled();
    expect(mockSetData).not.toHaveBeenCalled();
  });

  it("clicking Zoom Out does not call fit or setData", () => {
    render(<MindmapViewer markdown="# Test" />);
    mockFit.mockClear();

    fireEvent.click(screen.getByRole("button", { name: /zoom out/i }));

    expect(mockFit).not.toHaveBeenCalled();
    expect(mockSetData).not.toHaveBeenCalled();
  });

  it("clicking Fit calls fit and does not call rescale or setData", () => {
    render(<MindmapViewer markdown="# Test" />);
    mockFit.mockClear();

    fireEvent.click(screen.getByRole("button", { name: /^fit$/i }));

    expect(mockFit).toHaveBeenCalledTimes(1);
    expect(mockRescale).not.toHaveBeenCalled();
    expect(mockSetData).not.toHaveBeenCalled();
  });

  it("clicking Expand calls setData and fit, not rescale", () => {
    render(<MindmapViewer markdown="# Test" />);
    mockFit.mockClear();

    fireEvent.click(screen.getByRole("button", { name: /^expand$/i }));

    expect(mockSetData).toHaveBeenCalledTimes(1);
    expect(mockFit).toHaveBeenCalledTimes(1);
    expect(mockRescale).not.toHaveBeenCalled();
  });

  it("clicking Collapse calls setData and fit, not rescale", () => {
    render(<MindmapViewer markdown="# Test" />);
    mockFit.mockClear();

    fireEvent.click(screen.getByRole("button", { name: /^collapse$/i }));

    expect(mockSetData).toHaveBeenCalledTimes(1);
    expect(mockFit).toHaveBeenCalledTimes(1);
    expect(mockRescale).not.toHaveBeenCalled();
  });
});
