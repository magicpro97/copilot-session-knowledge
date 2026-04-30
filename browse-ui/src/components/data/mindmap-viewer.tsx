import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ZoomIn, ZoomOut, Maximize, Plus, Minus } from "lucide-react";
import { Markmap } from "markmap-view";
import { Transformer, type ITransformResult } from "markmap-lib";

import { Banner } from "@/components/data/banner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

type MindmapViewerProps = {
  markdown: string;
  title?: string;
};

const ZOOM_STEP = 1.25;

type MindmapRoot = ITransformResult["root"];
type MarkmapInstance = ReturnType<typeof Markmap.create>;
type SvgWithZoomState = SVGSVGElement & {
  __zoom?: {
    k?: unknown;
    x?: unknown;
    y?: unknown;
  };
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function hasUsableViewport(svg: SVGSVGElement): boolean {
  const { width, height } = svg.getBoundingClientRect();
  return isFiniteNumber(width) && isFiniteNumber(height) && width > 0 && height > 0;
}

function syncSvgViewportAttributes(svg: SVGSVGElement): boolean {
  const { width, height } = svg.getBoundingClientRect();
  if (!isFiniteNumber(width) || !isFiniteNumber(height) || width <= 0 || height <= 0) {
    return false;
  }
  svg.setAttribute("width", String(Math.round(width)));
  svg.setAttribute("height", String(Math.round(height)));
  return true;
}

function zoomTransformIsInvalid(svg: SVGSVGElement): boolean {
  const transform = (svg as SvgWithZoomState).__zoom;
  if (!transform) return false;
  return (
    !isFiniteNumber(transform.k) ||
    transform.k <= 0 ||
    !isFiniteNumber(transform.x) ||
    !isFiniteNumber(transform.y)
  );
}

function waitForFrame(): Promise<void> {
  if (typeof requestAnimationFrame !== "function") {
    return Promise.resolve();
  }
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

async function safeFit(map: MarkmapInstance, svg: SVGSVGElement): Promise<boolean> {
  if (!syncSvgViewportAttributes(svg)) return false;
  await map.fit();
  return true;
}

async function setDataAndFit(
  map: MarkmapInstance,
  svg: SVGSVGElement,
  root: MindmapRoot
): Promise<boolean> {
  await map.setData(root);
  await waitForFrame();
  return safeFit(map, svg);
}

function setFoldState(node: MindmapRoot | null | undefined, folded: boolean) {
  if (!node) return;
  if (node.payload) {
    node.payload.fold = folded ? 2 : 0;
  }
  for (const child of node.children || []) {
    setFoldState(child, folded);
  }
}

export function MindmapViewer({ markdown, title }: MindmapViewerProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const mapRef = useRef<MarkmapInstance | null>(null);
  const rootRef = useRef<MindmapRoot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [renderReady, setRenderReady] = useState(false);

  const sourceMarkdown = useMemo(
    () => markdown.trim() || `# ${title || "Session Mindmap"}`,
    [markdown, title]
  );

  const handleRenderError = useCallback((renderError: unknown) => {
    setRenderReady(false);
    setError(renderError instanceof Error ? renderError.message : "Unable to render mindmap");
  }, []);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    let cancelled = false;
    setError(null);
    setRenderReady(false);

    try {
      const transformer = new Transformer();
      const transformed = transformer.transform(sourceMarkdown);
      rootRef.current = transformed.root;
      mapRef.current?.destroy();
      syncSvgViewportAttributes(svg);
      const map = Markmap.create(svg, { autoFit: false });
      mapRef.current = map;
      void setDataAndFit(map, svg, transformed.root)
        .then((ready) => {
          if (!cancelled) setRenderReady(ready);
        })
        .catch((renderError: unknown) => {
          if (!cancelled) handleRenderError(renderError);
        });
    } catch (renderError) {
      handleRenderError(renderError);
    }

    return () => {
      cancelled = true;
      mapRef.current?.destroy();
      mapRef.current = null;
    };
  }, [handleRenderError, sourceMarkdown]);

  const fitMap = () => {
    const map = mapRef.current;
    const svg = svgRef.current;
    if (!map || !svg) return;
    void safeFit(map, svg)
      .then((ready) => setRenderReady(ready))
      .catch(handleRenderError);
  };

  const zoomBy = (scale: number) => {
    const map = mapRef.current;
    const svg = svgRef.current;
    if (!map || !svg || !renderReady) return;
    if (!hasUsableViewport(svg)) {
      setRenderReady(false);
      return;
    }
    syncSvgViewportAttributes(svg);
    if (zoomTransformIsInvalid(svg)) {
      void safeFit(map, svg).catch(handleRenderError);
      return;
    }
    void map.rescale(scale).catch(handleRenderError);
  };

  const zoomIn = () => {
    zoomBy(ZOOM_STEP);
  };

  const zoomOut = () => {
    zoomBy(1 / ZOOM_STEP);
  };

  const expandMap = () => {
    const map = mapRef.current;
    const svg = svgRef.current;
    if (!rootRef.current || !map || !svg) return;
    setFoldState(rootRef.current, false);
    void setDataAndFit(map, svg, rootRef.current)
      .then((ready) => setRenderReady(ready))
      .catch(handleRenderError);
  };

  const collapseMap = () => {
    const map = mapRef.current;
    const svg = svgRef.current;
    if (!rootRef.current || !map || !svg) return;
    setFoldState(rootRef.current, true);
    if (rootRef.current.payload) rootRef.current.payload.fold = 0;
    void setDataAndFit(map, svg, rootRef.current)
      .then((ready) => setRenderReady(ready))
      .catch(handleRenderError);
  };

  return (
    <Card>
      <CardContent className="space-y-3 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={zoomIn} disabled={!renderReady}>
            <Plus className="size-4" />
            Zoom In
          </Button>
          <Button variant="outline" onClick={zoomOut} disabled={!renderReady}>
            <Minus className="size-4" />
            Zoom Out
          </Button>
          <Button variant="outline" onClick={fitMap} disabled={!renderReady}>
            <Maximize className="size-4" />
            Fit
          </Button>
          <Button variant="outline" onClick={expandMap} disabled={!renderReady}>
            <ZoomIn className="size-4" />
            Expand
          </Button>
          <Button variant="outline" onClick={collapseMap} disabled={!renderReady}>
            <ZoomOut className="size-4" />
            Collapse
          </Button>
        </div>

        {error ? <Banner tone="danger" title="Mindmap render error" description={error} /> : null}

        <div className="border-border bg-muted/20 overflow-hidden rounded-lg border">
          <svg ref={svgRef} className="h-[540px] w-full" role="img" aria-label="Session mindmap" />
        </div>
      </CardContent>
    </Card>
  );
}
