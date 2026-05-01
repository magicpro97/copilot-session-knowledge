import { expect, test } from "./fixtures";
import { aliasPlaceholderSession } from "./session-detail-alias";

const modKey = process.platform === "darwin" ? "Meta" : "Control";

test("command palette opens and navigates", async ({ page }) => {
  await page.goto("/v2/sessions/");
  await expect(page.getByRole("heading", { level: 1, name: "Sessions" })).toBeVisible({
    timeout: 20_000,
  });

  await page.keyboard.press(`${modKey}+KeyK`);
  await expect(page.getByPlaceholder("Type a command or search history...")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/\/v2\/sessions\/?$/);
});

test("global shortcuts route to shipped pages", async ({ page }) => {
  await page.goto("/v2/sessions/");
  await expect(page.getByRole("heading", { level: 1, name: "Sessions" })).toBeVisible({
    timeout: 20_000,
  });

  await page.keyboard.press("g");
  await page.keyboard.press("i");
  await expect(page).toHaveURL(/\/v2\/insights\/?(#overview)?$/);
  await expect(page.getByRole("heading", { level: 1, name: "Insights" })).toBeVisible();

  await page.keyboard.press("g");
  await page.keyboard.press("/");
  await expect(page).toHaveURL(/\/v2\/search\/?$/);
  await expect(page.getByRole("heading", { level: 1, name: "Search" })).toBeVisible();
  await page.getByRole("heading", { level: 1, name: "Search" }).click();

  await page.keyboard.press("g");
  await page.keyboard.press("g");
  await expect(page).toHaveURL(/\/v2\/graph\/?#insight$/);
  await expect(page.getByRole("heading", { level: 1, name: "Graph" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Insight" })).toHaveAttribute("aria-selected", "true");

  // Legacy alias: #relationships → #evidence (redirect happens on mount)
  await page.goto("/v2/graph/#relationships");
  await expect(page).toHaveURL(/\/v2\/graph\/?#evidence$/);
  await expect(page.getByRole("tab", { name: "Evidence" })).toHaveAttribute(
    "aria-selected",
    "true"
  );

  // Numeric shortcuts on graph page: 1=Insight, 2=Evidence, 3=Similarity, 4=Communities
  await page.keyboard.press("2");
  await expect(page).toHaveURL(/\/v2\/graph\/?#evidence$/);
  await page.keyboard.press("3");
  await expect(page).toHaveURL(/\/v2\/graph\/?#similarity$/);
  await page.keyboard.press("4");
  await expect(page).toHaveURL(/\/v2\/graph\/?#communities$/);
  await page.keyboard.press("1");
  await expect(page).toHaveURL(/\/v2\/graph\/?#insight$/);

  // Legacy alias: #clusters → #similarity
  await page.goto("/v2/graph/#clusters");
  await expect(page).toHaveURL(/\/v2\/graph\/?#similarity$/);
  await expect(page.getByRole("tab", { name: "Similarity" })).toHaveAttribute(
    "aria-selected",
    "true"
  );

  await page.keyboard.press("g");
  await page.keyboard.press(",");
  await expect(page).toHaveURL(/\/v2\/settings\/?$/);
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();

  await page.keyboard.press("?");
  await expect(page).toHaveURL(/\/v2\/settings\/#shortcuts$/);
  await expect(page.getByText("Keyboard shortcuts reference", { exact: true })).toBeVisible();
});

test("insights and session detail keyboard shortcuts switch tabs", async ({ page }) => {
  await page.goto("/v2/insights/");
  const overviewTab = page.getByRole("tab", { name: "Overview" });
  const knowledgeTab = page.getByRole("tab", { name: "Knowledge" });
  const retroTab = page.getByRole("tab", { name: "Retro" });
  const searchQualityTab = page.getByRole("tab", { name: "Search Quality" });
  const liveTab = page.getByRole("tab", { name: "Live feed" });
  const workflowTab = page.getByRole("tab", { name: "Workflow" });

  // 1 = Overview (default), 2 = Knowledge, 3 = Retro, 4 = Search Quality, 5 = Live, 6 = Workflow
  await expect(overviewTab).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("2");
  await expect(knowledgeTab).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("3");
  await expect(retroTab).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("4");
  await expect(searchQualityTab).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("5");
  await expect(liveTab).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("6");
  await expect(workflowTab).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/#workflow$/);
  await page.keyboard.press("1");
  await expect(overviewTab).toHaveAttribute("aria-selected", "true");

  await aliasPlaceholderSession(page);
  await page.goto("/v2/sessions/_placeholder/");
  await expect(page).toHaveURL(/\/v2\/sessions\/_placeholder\/?(#overview)?$/);

  const sessionOverviewTab = page.getByRole("tab", { name: "Overview" });
  const timelineTab = page.getByRole("tab", { name: "Timeline" });
  const mindmapTab = page.getByRole("tab", { name: "Mindmap" });
  const checkpointsTab = page.getByRole("tab", { name: "Checkpoints" });

  await expect(sessionOverviewTab).toHaveAttribute("aria-selected", "true");

  await page.keyboard.press("2");
  await expect(timelineTab).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/#timeline$/);

  await page.keyboard.press("3");
  await expect(mindmapTab).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/#mindmap$/);

  await page.keyboard.press("4");
  await expect(checkpointsTab).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/#checkpoints$/);

  await page.keyboard.press("1");
  await expect(sessionOverviewTab).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/#overview$/);
});
