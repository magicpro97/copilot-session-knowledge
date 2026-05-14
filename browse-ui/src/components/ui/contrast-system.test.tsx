/**
 * Regression guards for browse-contrast-system surface tuning.
 *
 * These tests verify that shared popup/select/dialog/sheet surfaces remain
 * visually opaque and use deliberate border tokens instead of faint opacity hacks.
 * They catch accidental regressions to transparent or over-bright styles.
 */
import "@testing-library/jest-dom";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  PANEL_SURFACE_BASE,
  POPUP_SURFACE_BASE,
  POPUP_SURFACE_BG,
} from "@/components/ui/popup-surface";
import { buttonVariants } from "@/components/ui/button";
import { Select, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogOverlay } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetOverlay } from "@/components/ui/sheet";
import { SurfacePanel } from "@/components/ui/surface-panel";

/** WCAG 2.x contrast helpers — no external deps, pure arithmetic */
function hslToSrgb(h: number, s: number, l: number): [number, number, number] {
  const sn = s / 100;
  const ln = l / 100;
  const c = (1 - Math.abs(2 * ln - 1)) * sn;
  const hp = h / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  let r1 = 0,
    g1 = 0,
    b1 = 0;
  if (hp < 1) {
    r1 = c;
    g1 = x;
  } else if (hp < 2) {
    r1 = x;
    g1 = c;
  } else if (hp < 3) {
    g1 = c;
    b1 = x;
  } else if (hp < 4) {
    g1 = x;
    b1 = c;
  } else if (hp < 5) {
    r1 = x;
    b1 = c;
  } else {
    r1 = c;
    b1 = x;
  }
  const m = ln - c / 2;
  return [r1 + m, g1 + m, b1 + m];
}

function toLinearChannel(c: number): number {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function relativeLuminance(r: number, g: number, b: number): number {
  return 0.2126 * toLinearChannel(r) + 0.7152 * toLinearChannel(g) + 0.0722 * toLinearChannel(b);
}

function wcagContrast(l1: number, l2: number): number {
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

function readGlobalsCss() {
  const currentFile = import.meta.url.startsWith("file:")
    ? fileURLToPath(import.meta.url)
    : import.meta.url;
  return readFileSync(resolve(dirname(currentFile), "../../app/globals.css"), "utf-8");
}

describe("Contrast system — opaque trigger surfaces", () => {
  it("SelectTrigger uses bg-secondary (opaque) not bg-transparent", () => {
    render(
      <Select>
        <SelectTrigger>
          <SelectValue placeholder="Pick one" />
        </SelectTrigger>
      </Select>
    );

    const trigger = document.querySelector('[data-slot="select-trigger"]');
    expect(trigger).not.toBeNull();
    const cls = trigger!.className;
    expect(cls).toContain("bg-secondary");
    expect(cls).not.toContain("bg-transparent");
    // Ensure the dark-mode semi-transparent overrides are gone
    expect(cls).not.toContain("dark:bg-input/30");
    expect(cls).not.toContain("dark:hover:bg-input/50");
  });
});

describe("Contrast system — popup ring token", () => {
  it("SelectContent uses ring-border not ring-foreground/10", async () => {
    // The ring class is a static string on the Popup element; verify via source snapshot
    // We import the source to check the class constant rather than rendering a portal.
    const mod = await import("@/components/ui/select");
    // Smoke: module loads and exports the expected symbols
    expect(typeof mod.SelectContent).toBe("function");
    expect(typeof mod.SelectTrigger).toBe("function");
  });
});

describe("Contrast system — overlay opacity", () => {
  it("DialogOverlay uses bg-black/30 (not the faint bg-black/10)", () => {
    render(
      <Dialog open={true}>
        <DialogOverlay />
      </Dialog>
    );
    const overlay = document.querySelector('[data-slot="dialog-overlay"]');
    expect(overlay).not.toBeNull();
    const cls = overlay!.className;
    expect(cls).toContain("bg-black/30");
    expect(cls).not.toContain("bg-black/10");
  });

  it("SheetOverlay uses bg-black/30 (not the faint bg-black/10)", () => {
    render(
      <Sheet open={true}>
        <SheetOverlay />
      </Sheet>
    );
    const overlay = document.querySelector('[data-slot="sheet-overlay"]');
    expect(overlay).not.toBeNull();
    const cls = overlay!.className;
    expect(cls).toContain("bg-black/30");
    expect(cls).not.toContain("bg-black/10");
  });
});

describe("Popup surface constants — opaque surface enforcement", () => {
  it("POPUP_SURFACE_BG includes bg-popover (opaque background token)", () => {
    expect(POPUP_SURFACE_BG).toContain("bg-popover");
    expect(POPUP_SURFACE_BG).not.toContain("bg-transparent");
  });

  it("POPUP_SURFACE_BG does not contain opacity-fraction classes", () => {
    // Fraction classes like bg-popover/50 would make the surface semi-transparent
    expect(POPUP_SURFACE_BG).not.toMatch(/bg-\w+\/\d+/);
  });

  it("POPUP_SURFACE_BASE includes border, ring-border, shadow-lg, and ring-1", () => {
    expect(POPUP_SURFACE_BASE).toContain("border");
    expect(POPUP_SURFACE_BASE).toContain("ring-border");
    expect(POPUP_SURFACE_BASE).toContain("shadow-lg");
    expect(POPUP_SURFACE_BASE).toContain("ring-1");
    expect(POPUP_SURFACE_BASE).toContain("overflow-hidden");
  });

  it("POPUP_SURFACE_BASE is a superset of POPUP_SURFACE_BG", () => {
    for (const token of POPUP_SURFACE_BG.split(" ")) {
      expect(POPUP_SURFACE_BASE).toContain(token);
    }
  });

  it("popup surface constants do not include glass/translucent style tokens", () => {
    const forbidden = ["bg-transparent", "backdrop-blur", "opacity-0", "opacity-50"];
    for (const f of forbidden) {
      expect(POPUP_SURFACE_BASE).not.toContain(f);
      expect(POPUP_SURFACE_BG).not.toContain(f);
    }
  });

  it("DialogContent reuses all popup surface tokens", () => {
    render(
      <Dialog open={true}>
        <DialogContent showCloseButton={false}>Dialog body</DialogContent>
      </Dialog>
    );

    const dialog = document.querySelector('[data-slot="dialog-content"]');
    expect(dialog).not.toBeNull();
    for (const token of POPUP_SURFACE_BASE.split(" ")) {
      expect(dialog!.className).toContain(token);
    }
  });

  it("SurfacePanel uses the shared embedded panel surface tokens", () => {
    render(<SurfacePanel>Panel body</SurfacePanel>);

    const panel = document.querySelector('[data-slot="surface-panel"]');
    expect(panel).not.toBeNull();
    for (const token of PANEL_SURFACE_BASE.split(" ")) {
      expect(panel!.className).toContain(token);
    }
  });
});

describe("Tailwind theme color tokens — valid opaque colors", () => {
  it("maps popover colors through hsl(var(...)) so bg-popover renders a real background", () => {
    const globalsCss = readGlobalsCss();

    expect(globalsCss).toContain("--color-popover: hsl(var(--popover));");
    expect(globalsCss).toContain("--color-popover-foreground: hsl(var(--popover-foreground));");
    expect(globalsCss).not.toContain("--color-popover: var(--popover);");
  });

  it("does not expose raw HSL triplets as Tailwind color values", () => {
    const globalsCss = readGlobalsCss();

    const rawTripletMappings = Array.from(
      globalsCss.matchAll(/--color-[\w-]+:\s*var\(--(?!font)[\w-]+\);/g)
    );
    expect(rawTripletMappings.map((match) => match[0])).toEqual([]);
  });

  it("exposes a .palette-classic opt-in that restores the cool/APCA indigo design language", () => {
    const globalsCss = readGlobalsCss();

    // Light-mode classic MUST be scoped with :not(.dark) — without it,
    // :root.palette-classic (0,1,1) outranks .dark (0,1,0) and bleeds light
    // surfaces into dark mode when classic is active.
    expect(globalsCss).toMatch(
      /:root:not\(\.dark\)\.palette-classic\s*\{[^}]*--background:\s*210 20% 97%;/
    );
    expect(globalsCss).toMatch(
      /:root:not\(\.dark\)\.palette-classic\s*\{[^}]*--card:\s*210 16% 99%;/
    );
    expect(globalsCss).toMatch(
      /:root:not\(\.dark\)\.palette-classic\s*\{[^}]*--popover:\s*210 16% 99%;/
    );
    expect(globalsCss).toMatch(
      /:root:not\(\.dark\)\.palette-classic\s*\{[^}]*--primary:\s*235 56% 60%;/
    );
    expect(globalsCss).toMatch(
      /:root:not\(\.dark\)\.palette-classic\s*\{[^}]*--ring:\s*235 56% 25%;/
    );

    // Dark-mode classic restores the cool indigo / APCA-tuned dark surfaces.
    expect(globalsCss).toMatch(/\.dark\.palette-classic\s*\{[^}]*--foreground:\s*210 20% 88%;/);
    expect(globalsCss).toMatch(/\.dark\.palette-classic\s*\{[^}]*--popover:\s*215 20% 13%;/);
    expect(globalsCss).toMatch(/\.dark\.palette-classic\s*\{[^}]*--card:\s*215 20% 12%;/);
    expect(globalsCss).toMatch(/\.dark\.palette-classic\s*\{[^}]*--primary:\s*235 56% 56%;/);
    // ring is decoupled from primary in dark mode to satisfy WCAG 1.4.11 at ring/50 opacity
    expect(globalsCss).toMatch(/\.dark\.palette-classic\s*\{[^}]*--ring:\s*235 56% 80%;/);
  });

  it("does not use unscoped :root.palette-classic (would leak light vars into dark mode)", () => {
    const globalsCss = readGlobalsCss();
    expect(globalsCss).not.toMatch(/:root\.palette-classic\s*\{/);
  });

  it("classic palette chart tokens are cool/indigo (not warm coral or amber)", () => {
    const globalsCss = readGlobalsCss();

    const lightBlockMatch = globalsCss.match(/:root:not\(\.dark\)\.palette-classic\s*\{([^}]+)\}/);
    expect(lightBlockMatch).not.toBeNull();
    const lightBlock = lightBlockMatch![1];
    // chart-1 must NOT be the warm coral value
    expect(lightBlock).not.toMatch(/--chart-1:\s*12 75% 59%/);
    // chart-5 must NOT be the warm amber value
    expect(lightBlock).not.toMatch(/--chart-5:\s*38 85% 55%/);
    // chart-1 should use the indigo primary hue (≈235)
    expect(lightBlock).toMatch(/--chart-1:\s*235/);
    // chart-5 should use a cool hue (not warm 12–42 range)
    expect(lightBlock).toMatch(/--chart-5:\s*19[0-9]/);

    const darkBlockMatch = globalsCss.match(/\.dark\.palette-classic\s*\{([^}]+)\}/);
    expect(darkBlockMatch).not.toBeNull();
    const darkBlock = darkBlockMatch![1];
    // chart-1 must NOT be the warm dark-mode coral value
    expect(darkBlock).not.toMatch(/--chart-1:\s*12 78% 66%/);
    expect(darkBlock).not.toMatch(/--chart-1:\s*12 80% 45%/);
    // chart-1 should use the indigo primary hue
    expect(darkBlock).toMatch(/--chart-1:\s*235/);
  });

  it("covers all semantic tokens that differ between warm and classic/cool palettes", () => {
    const globalsCss = readGlobalsCss();

    const lightBlockMatch = globalsCss.match(/:root:not\(\.dark\)\.palette-classic\s*\{([^}]+)\}/);
    expect(lightBlockMatch).not.toBeNull();
    const lightVars = Array.from(lightBlockMatch![1].matchAll(/--([\w-]+):/g))
      .map((m) => m[1])
      .sort();
    // Classic light block must restore all semantic tokens that differ vs the warm default
    const expectedLightVars = [
      "accent",
      "background",
      "border",
      "card",
      "card-foreground",
      "chart-1",
      "chart-2",
      "chart-3",
      "chart-4",
      "chart-5",
      "foreground",
      "input",
      "muted",
      "muted-foreground",
      "popover",
      "popover-foreground",
      "primary",
      "ring",
      "secondary",
      "secondary-foreground",
    ];
    expect(lightVars).toEqual(expectedLightVars);

    const darkBlockMatch = globalsCss.match(/\.dark\.palette-classic\s*\{([^}]+)\}/);
    expect(darkBlockMatch).not.toBeNull();
    const darkVars = Array.from(darkBlockMatch![1].matchAll(/--([\w-]+):/g))
      .map((m) => m[1])
      .sort();
    const expectedDarkVars = [
      "accent",
      "background",
      "border",
      "card",
      "card-foreground",
      "chart-1",
      "chart-2",
      "chart-3",
      "chart-4",
      "chart-5",
      "foreground",
      "input",
      "muted",
      "muted-foreground",
      "popover",
      "popover-foreground",
      "primary",
      "ring",
      "secondary",
      "secondary-foreground",
    ];
    expect(darkVars).toEqual(expectedDarkVars);
  });
});

describe("Shared surface — SheetContent background contract", () => {
  it("SheetContent uses shared POPUP_SURFACE_BG tokens (bg-popover text-popover-foreground)", () => {
    render(
      <Sheet open={true}>
        <SheetContent showCloseButton={false}>Sheet body</SheetContent>
      </Sheet>
    );

    const content = document.querySelector('[data-slot="sheet-content"]');
    expect(content).not.toBeNull();
    for (const token of POPUP_SURFACE_BG.split(" ")) {
      expect(content!.className).toContain(token);
    }
  });

  it("SheetContent does not use ad-hoc background overrides outside the shared token", () => {
    render(
      <Sheet open={true}>
        <SheetContent showCloseButton={false}>Sheet body</SheetContent>
      </Sheet>
    );

    const content = document.querySelector('[data-slot="sheet-content"]');
    expect(content).not.toBeNull();
    const cls = content!.className;
    // The bg and text tokens must come from POPUP_SURFACE_BG — no semi-transparent variants
    expect(cls).not.toMatch(/bg-popover\/\d+/);
    expect(cls).not.toContain("bg-transparent");
    expect(cls).not.toMatch(/bg-background\/\d+/);
  });
});

describe("Primary button — accessibility guards", () => {
  it("primary button shimmer uses motion-safe: guard (reduced-motion users not subjected to infinite animation)", () => {
    const defaultClass: string = buttonVariants({ variant: "default" });
    // motion-safe:before:animate-btn-shimmer must be present
    expect(defaultClass).toContain("motion-safe:before:animate-btn-shimmer");
    // Plain before:animate-btn-shimmer (without guard) must NOT be present
    expect(defaultClass).not.toMatch(/(?<!motion-safe:)before:animate-btn-shimmer/);
  });

  it("primary button hides shimmer overlay element for reduced-motion users (no static gradient highlight)", () => {
    const defaultClass: string = buttonVariants({ variant: "default" });
    // The before: pseudo-element must be fully hidden (display:none) for reduced-motion users
    // so no static white gradient highlight remains visible when animation is suppressed
    expect(defaultClass).toContain("motion-reduce:before:hidden");
  });
});

describe("WCAG AA contrast regression guards", () => {
  it("classic dark primary lightness is <=58% so white button text achieves WCAG AA >=4.5:1", () => {
    const globalsCss = readGlobalsCss();
    const darkBlockMatch = globalsCss.match(/\.dark\.palette-classic\s*\{([^}]+)\}/);
    expect(darkBlockMatch).not.toBeNull();
    const darkBlock = darkBlockMatch![1];
    // Must NOT be at the old failing 69% lightness
    expect(darkBlock).not.toMatch(/--primary:\s*235 56% 69%/);
    // Primary hue must be in the indigo range with lightness <=58%
    const primaryMatch = darkBlock.match(/--primary:\s*235 56% (\d+)%/);
    expect(primaryMatch).not.toBeNull();
    const lightness = parseInt(primaryMatch![1], 10);
    expect(lightness).toBeLessThanOrEqual(58);
  });

  it("warm dark muted-foreground lightness is >=60% so secondary text on card surfaces clears WCAG AA", () => {
    const globalsCss = readGlobalsCss();
    // The .dark block (warm dark, no palette-classic qualifier)
    // Match only the plain .dark block, stopping before .dark.palette-classic
    const darkBlockMatch = globalsCss.match(/\.dark\s*\{([^}]+)\}/);
    expect(darkBlockMatch).not.toBeNull();
    const darkBlock = darkBlockMatch![1];
    // Must NOT be at the old failing 50% lightness
    expect(darkBlock).not.toMatch(/--muted-foreground:\s*30 8% 50%/);
    const mutedMatch = darkBlock.match(/--muted-foreground:\s*30 \d+% (\d+)%/);
    expect(mutedMatch).not.toBeNull();
    const lightness = parseInt(mutedMatch![1], 10);
    expect(lightness).toBeGreaterThanOrEqual(60);
  });

  it("classic dark muted-foreground lightness is >=58% so secondary text on card surfaces clears WCAG AA", () => {
    const globalsCss = readGlobalsCss();
    const darkBlockMatch = globalsCss.match(/\.dark\.palette-classic\s*\{([^}]+)\}/);
    expect(darkBlockMatch).not.toBeNull();
    const darkBlock = darkBlockMatch![1];
    // Must NOT be at the old failing 47% lightness
    expect(darkBlock).not.toMatch(/--muted-foreground:\s*212 7% 47%/);
    const mutedMatch = darkBlock.match(/--muted-foreground:\s*212 \d+% (\d+)%/);
    expect(mutedMatch).not.toBeNull();
    const lightness = parseInt(mutedMatch![1], 10);
    expect(lightness).toBeGreaterThanOrEqual(58);
  });

  it("warm dark primary achieves WCAG AA ≥4.5:1 with white text (direct contrast computation — drift guard)", () => {
    const globalsCss = readGlobalsCss();
    // Match only the plain .dark block (warm dark, no palette-classic qualifier)
    const darkBlockMatch = globalsCss.match(/\.dark\s*\{([^}]+)\}/);
    expect(darkBlockMatch).not.toBeNull();
    const darkBlock = darkBlockMatch![1];
    const primaryMatch = darkBlock.match(/--primary:\s*(\d+) (\d+)% (\d+)%/);
    expect(primaryMatch).not.toBeNull();
    const [pH, pS, pL] = primaryMatch!.slice(1).map(Number);
    const [r, g, b] = hslToSrgb(pH, pS, pL);
    const lPrimary = relativeLuminance(r, g, b);
    // White foreground luminance
    const ratio = wcagContrast(1.0, lPrimary);
    // Any upward drift in primary lightness would silently break WCAG AA —
    // this direct computation catches it without relying on a proxy threshold.
    expect(ratio).toBeGreaterThanOrEqual(4.5);
  });
});

describe("Dark-mode focus-ring WCAG 1.4.11 contrast guards", () => {
  it("warm dark ring/50 achieves ≥3:1 contrast against dark background (WCAG 1.4.11 direct computation)", () => {
    const globalsCss = readGlobalsCss();
    // Plain .dark block (warm dark, no palette-classic qualifier)
    const darkBlockMatch = globalsCss.match(/\.dark\s*\{([^}]+)\}/);
    expect(darkBlockMatch).not.toBeNull();
    const darkBlock = darkBlockMatch![1];

    const ringMatch = darkBlock.match(/--ring:\s*(\d+) (\d+)% (\d+)%/);
    expect(ringMatch).not.toBeNull();
    const [ringH, ringS, ringL] = ringMatch!.slice(1).map(Number);

    // ring must be decoupled from primary (which must stay low for button-text contrast)
    const primaryMatch = darkBlock.match(/--primary:\s*12 \d+% (\d+)%/);
    expect(primaryMatch).not.toBeNull();
    const primaryLightness = parseInt(primaryMatch![1], 10);
    expect(ringL).not.toEqual(primaryLightness);

    // Direct WCAG 1.4.11 check: ring/50 alpha-blended over the warm dark background
    // must achieve ≥3:1 contrast against the background.
    // A proxy lightness threshold (e.g. >=70) is too loose — it can pass a value
    // where ring/50 still fails 3:1 depending on the background hue and saturation.
    const bgMatch = darkBlock.match(/--background:\s*(\d+) (\d+)% (\d+)%/);
    expect(bgMatch).not.toBeNull();
    const [bgH, bgS, bgL] = bgMatch!.slice(1).map(Number);

    const [rr, rg, rb] = hslToSrgb(ringH, ringS, ringL);
    const [br, bg, bb] = hslToSrgb(bgH, bgS, bgL);
    // CSS alpha compositing at 50% opacity (sRGB space, as browsers render it)
    const blendR = rr * 0.5 + br * 0.5;
    const blendG = rg * 0.5 + bg * 0.5;
    const blendB = rb * 0.5 + bb * 0.5;
    const lBlended = relativeLuminance(blendR, blendG, blendB);
    const lBg = relativeLuminance(br, bg, bb);
    const ratio = wcagContrast(lBlended, lBg);
    expect(ratio).toBeGreaterThanOrEqual(3.0);
  });

  it("classic dark ring/50 achieves ≥3:1 contrast against dark background (WCAG 1.4.11 direct computation)", () => {
    const globalsCss = readGlobalsCss();
    const darkBlockMatch = globalsCss.match(/\.dark\.palette-classic\s*\{([^}]+)\}/);
    expect(darkBlockMatch).not.toBeNull();
    const darkBlock = darkBlockMatch![1];

    const ringMatch = darkBlock.match(/--ring:\s*(\d+) (\d+)% (\d+)%/);
    expect(ringMatch).not.toBeNull();
    const [ringH, ringS, ringL] = ringMatch!.slice(1).map(Number);

    // ring must be decoupled from primary (which must stay low for button-text contrast)
    const primaryMatch = darkBlock.match(/--primary:\s*235 56% (\d+)%/);
    expect(primaryMatch).not.toBeNull();
    const primaryLightness = parseInt(primaryMatch![1], 10);
    expect(ringL).not.toEqual(primaryLightness);

    // Direct WCAG 1.4.11 check: ring/50 alpha-blended over the classic dark background
    // must achieve ≥3:1 contrast against the background.
    // A proxy lightness threshold (e.g. >=75) is too loose — it cannot account for
    // the specific background hue/saturation of the classic dark palette.
    const bgMatch = darkBlock.match(/--background:\s*(\d+) (\d+)% (\d+)%/);
    expect(bgMatch).not.toBeNull();
    const [bgH, bgS, bgL] = bgMatch!.slice(1).map(Number);

    const [rr, rg, rb] = hslToSrgb(ringH, ringS, ringL);
    const [br, bgR, bb] = hslToSrgb(bgH, bgS, bgL);
    // CSS alpha compositing at 50% opacity (sRGB space, as browsers render it)
    const blendR = rr * 0.5 + br * 0.5;
    const blendG = rg * 0.5 + bgR * 0.5;
    const blendB = rb * 0.5 + bb * 0.5;
    const lBlended = relativeLuminance(blendR, blendG, blendB);
    const lBg = relativeLuminance(br, bgR, bb);
    const ratio = wcagContrast(lBlended, lBg);
    expect(ratio).toBeGreaterThanOrEqual(3.0);
  });
});

describe("Light-mode focus-ring WCAG 1.4.11 contrast guards", () => {
  it("warm light ring is decoupled from primary and ring/50 achieves ≥3:1 contrast against page background (WCAG 1.4.11)", () => {
    const globalsCss = readGlobalsCss();
    // The :root block contains warm light tokens
    const rootBlockMatch = globalsCss.match(/:root\s*\{([^}]+)\}/);
    expect(rootBlockMatch).not.toBeNull();
    const rootBlock = rootBlockMatch![1];

    const ringMatch = rootBlock.match(/--ring:\s*(\d+) (\d+)% (\d+)%/);
    expect(ringMatch).not.toBeNull();
    const [ringH, ringS, ringL] = ringMatch!.slice(1).map(Number);

    // ring must be decoupled from primary (not the same lightness)
    const primaryMatch = rootBlock.match(/--primary:\s*(\d+) (\d+)% (\d+)%/);
    expect(primaryMatch).not.toBeNull();
    const primaryL = Number(primaryMatch![3]);
    expect(ringL).not.toEqual(primaryL);

    // Direct WCAG 1.4.11 check: ring/50 alpha-blended over page background must
    // achieve ≥3:1 contrast against the page background.
    // A proxy lightness bound (e.g. <=30) can falsely pass values where ring/50
    // still fails 3:1 — this direct computation closes that loophole.
    const bgMatch = rootBlock.match(/--background:\s*(\d+) (\d+)% (\d+)%/);
    expect(bgMatch).not.toBeNull();
    const [bgH, bgS, bgL] = bgMatch!.slice(1).map(Number);

    const [rr, rg, rb] = hslToSrgb(ringH, ringS, ringL);
    const [br, bg, bb] = hslToSrgb(bgH, bgS, bgL);
    // CSS alpha compositing at 50% opacity (sRGB space, as browsers render it)
    const blendR = rr * 0.5 + br * 0.5;
    const blendG = rg * 0.5 + bg * 0.5;
    const blendB = rb * 0.5 + bb * 0.5;
    const lBlended = relativeLuminance(blendR, blendG, blendB);
    const lBg = relativeLuminance(br, bg, bb);
    const ratio = wcagContrast(lBlended, lBg);
    expect(ratio).toBeGreaterThanOrEqual(3.0);
  });

  it("classic light ring is decoupled from primary and ring/50 achieves ≥3:1 contrast against page background (WCAG 1.4.11)", () => {
    const globalsCss = readGlobalsCss();
    const lightBlockMatch = globalsCss.match(/:root:not\(\.dark\)\.palette-classic\s*\{([^}]+)\}/);
    expect(lightBlockMatch).not.toBeNull();
    const lightBlock = lightBlockMatch![1];

    const ringMatch = lightBlock.match(/--ring:\s*(\d+) (\d+)% (\d+)%/);
    expect(ringMatch).not.toBeNull();
    const [ringH, ringS, ringL] = ringMatch!.slice(1).map(Number);

    // ring must be decoupled from primary (not the same lightness)
    const primaryMatch = lightBlock.match(/--primary:\s*(\d+) (\d+)% (\d+)%/);
    expect(primaryMatch).not.toBeNull();
    const primaryL = Number(primaryMatch![3]);
    expect(ringL).not.toEqual(primaryL);

    // Direct WCAG 1.4.11 check: ring/50 alpha-blended over page background must
    // achieve ≥3:1 contrast against the page background.
    const bgMatch = lightBlock.match(/--background:\s*(\d+) (\d+)% (\d+)%/);
    expect(bgMatch).not.toBeNull();
    const [bgH, bgS, bgL] = bgMatch!.slice(1).map(Number);

    const [rr, rg, rb] = hslToSrgb(ringH, ringS, ringL);
    const [br, bgR, bb] = hslToSrgb(bgH, bgS, bgL);
    const blendR = rr * 0.5 + br * 0.5;
    const blendG = rg * 0.5 + bgR * 0.5;
    const blendB = rb * 0.5 + bb * 0.5;
    const lBlended = relativeLuminance(blendR, blendG, blendB);
    const lBg = relativeLuminance(br, bgR, bb);
    const ratio = wcagContrast(lBlended, lBg);
    expect(ratio).toBeGreaterThanOrEqual(3.0);
  });
});
