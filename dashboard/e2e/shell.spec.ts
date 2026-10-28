import { expect, test } from "@playwright/test";

test("provides an accessible, responsive empty dashboard shell", async ({ page }) => {
  await page.route("**/backend/api/v1/runs?limit=20", async (route) => {
    await route.fulfill({ json: [] });
  });

  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "AgentForge" })).toBeVisible();
  const newRunButton = page.getByRole("button", { name: "New run" });
  await expect(newRunButton).toBeVisible();
  await expect(
    page.getByText("Select a recent run or start a new support report.")
  ).toBeVisible();

  await page.keyboard.press("Tab");
  await expect(newRunButton).toBeFocused();
  const focusOutline = await newRunButton.evaluate(
    (element) => getComputedStyle(element).outlineStyle
  );
  expect(focusOutline).not.toBe("none");

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  expect(hasHorizontalOverflow).toBe(false);
});
