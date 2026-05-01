import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

describe("Tabs primitive wrappers", () => {
  it("uses orientation-aware class selectors for vertical rails", () => {
    render(
      <Tabs orientation="vertical" defaultValue="one">
        <TabsList variant="line">
          <TabsTrigger value="one">One</TabsTrigger>
          <TabsTrigger value="two">Two</TabsTrigger>
        </TabsList>
        <TabsContent value="one">First</TabsContent>
        <TabsContent value="two">Second</TabsContent>
      </Tabs>
    );

    expect(screen.getByRole("tablist")).toHaveAttribute("aria-orientation", "vertical");
    expect(screen.getByRole("tablist").className).toContain(
      "group-data-[orientation=vertical]/tabs:flex-col"
    );
    expect(screen.getByRole("tab", { name: "One" }).className).toContain(
      "group-data-[orientation=vertical]/tabs:w-full"
    );
  });
});
