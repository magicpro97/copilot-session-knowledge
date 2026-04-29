import { useEffect, useMemo, useRef, useState } from "react";
import { ZoomIn, ZoomOut, Maximize } from "lucide-react";
import { Markmap } from "markmap-view";
import { Transformer } from "markmap-lib";

import { Banner } from "@/components/data/banner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

type MindmapViewerProps = {
  markdown: string;
  title?: string;
};

function setFoldState(node: any, folded: boolean) {
  if (!node) return;
  if (node.payload) {
    node.payload.fold = folded;
  }
  for (const child of node.children || []) {
    setFoldState(child, folded);
  }
}

export function MindmapViewer({ markdown, title }: MindmapViewerProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const mapRef = useRef<any>(null);
  const rootRef = useRef<any>(null);
  const [error, setError] = useState<string | null>(null);

  const sourceMarkdown = useMemo(
    () => markdown.trim() || `# ${title || "Session Mindmap"}`,
    [markdown, title]
  );

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    setError(null);

    try {
      const transformer = new Transformer();
      const transformed = transformer.transform(sourceMarkdown);
      rootRef.current = transformed.root;
      mapRef.current?.destroy?.();
      mapRef.current = Markmap.create(svg, { autoFit: true }, transformed.root);
      mapRef.current.fit?.();
    } catch (renderError) {
      setError(renderError instanceof Error ? renderError.message : "Unable to render mindmap");
    }

    return () => {
      mapRef.current?.destroy?.();
    };
  }, [sourceMarkdown]);

  const fitMap = () => {
    mapRef.current?.fit?.();
  };

  const expandMap = () => {
    if (!rootRef.current || !mapRef.current) return;
    setFoldState(rootRef.current, false);
    mapRef.current.setData?.(rootRef.current);
    mapRef.current.fit?.();
  };

  const collapseMap = () => {
    if (!rootRef.current || !mapRef.current) return;
    setFoldState(rootRef.current, true);
    if (rootRef.current.payload) rootRef.current.payload.fold = false;
    mapRef.current.setData?.(rootRef.current);
    mapRef.current.fit?.();
  };

  return (
    <Card>
      <CardContent className="space-y-3 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={fitMap}>
            <Maximize className="size-4" />
            Fit
          </Button>
          <Button variant="outline" onClick={expandMap}>
            <ZoomIn className="size-4" />
            Expand
          </Button>
          <Button variant="outline" onClick={collapseMap}>
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
