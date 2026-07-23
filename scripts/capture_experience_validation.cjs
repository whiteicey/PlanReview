"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const baseUrl = process.env.PLANREVIEW_UI_BASE_URL || "http://127.0.0.1:8891";
const caseId = process.env.PLANREVIEW_UI_CASE_ID;
const runId = process.env.PLANREVIEW_UI_RUN_ID;
const outputDir = path.resolve(process.env.PLANREVIEW_UI_OUTPUT_DIR || "artifacts/expert_experience_validation/screenshots");
const executablePath = process.env.PLANREVIEW_UI_BROWSER_EXECUTABLE || undefined;
if (!caseId || !runId) throw new Error("PLANREVIEW_UI_CASE_ID and PLANREVIEW_UI_RUN_ID are required");
fs.mkdirSync(outputDir, { recursive: true });

let browser;
(async () => {
  browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.route("**/*", async (route) => {
    const request = route.request();
    if (/api\.deepseek\.com|anthropic\.com|api\.openai\.com/i.test(request.url())) {
      throw new Error("provider request detected during screenshot validation");
    }
    if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method().toUpperCase())) {
      return route.abort("blockedbyclient");
    }
    return route.continue();
  });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  await page.addInitScript(({ caseId, runId }) => {
    localStorage.removeItem("planreview.activeRun.v1");
    localStorage.setItem("planreview.layoutMode", "desktop");
  }, { caseId, runId });
  await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.evaluate(
    ({ caseId, runId }) => loadCompletedRun(
      { runStatus: "READY_FOR_HUMAN_REVIEW", metrics: { applicable_rule_count: 18 } }, caseId, runId,
    ),
    { caseId, runId },
  );
  const findingCount = await page.locator(".finding").count();
  if (!findingCount) {
    const resultText = await page.locator("#result").innerText().catch(() => "missing #result");
    throw new Error(`findings did not render: ${resultText}; console=${consoleErrors.join(" | ")}`);
  }
  await page.locator('[data-workflow-tab="results"]').click();
  await page.locator('.finding[data-finding-id="rule-VERSION-001-6"] [data-role="experience-flow"]').scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(outputDir, "finding-experience-status.png"), fullPage: true });

  await page.locator('[data-role="experience-library-nav"]').click();
  await page.waitForFunction(() => document.querySelectorAll(".experience-library-item").length > 0, null, { timeout: 30000 });
  const separation = await page.locator(".experience-library-item").first().evaluate((node) => ({
    expert: Boolean(node.querySelector(".experience-judgment")),
    model: Boolean(node.querySelector("details")),
  }));
  if (!separation.expert || !separation.model) throw new Error("expert/model sections are not separated");
  await page.locator(".experience-library-item details").first().evaluate((node) => { node.open = true; });
  await page.screenshot({ path: path.join(outputDir, "experience-library-desktop.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(outputDir, "experience-library-compact.png"), fullPage: true });
  await browser.close();
  browser = null;
  if (consoleErrors.length) throw new Error(`browser console errors: ${consoleErrors.join(" | ")}`);
  process.stdout.write(JSON.stringify({ screenshots: 3, expert_model_separated: true }));
})().catch(async (error) => {
  if (browser) await browser.close().catch(() => {});
  console.error(error);
  process.exitCode = 1;
});
