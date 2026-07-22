"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const baseUrl = process.env.PLANREVIEW_UI_BASE_URL || "http://127.0.0.1:8890";
const caseId = process.env.PLANREVIEW_UI_CASE_ID;
const runId = process.env.PLANREVIEW_UI_RUN_ID;
const outputDir = path.resolve(process.env.PLANREVIEW_UI_OUTPUT_DIR || "docs/ui-migration/screenshots");
const requestAuditPath = path.resolve(process.env.PLANREVIEW_UI_REQUEST_AUDIT || "docs/ui-migration/request_audit_replay.json");
const browserExecutable = process.env.PLANREVIEW_UI_BROWSER_EXECUTABLE || undefined;

if (!caseId || !runId) throw new Error("PLANREVIEW_UI_CASE_ID and PLANREVIEW_UI_RUN_ID are required");
fs.mkdirSync(outputDir, { recursive: true });

const viewports = [
  [1440, 900],
  [1280, 800],
  [1024, 768],
  [768, 1024],
  [390, 844],
];

const requests = new Map();
const providerHosts = /api\.deepseek\.com|anthropic\.com|api\.openai\.com/i;
const writeMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const consoleErrors = [];

function audit(method, url, status = null) {
  const redacted = url.replace(caseId, "<case-id>").replace(runId, "<run-id>");
  const key = `${method} ${redacted}`;
  const current = requests.get(key) || { method, url: redacted, status_codes: [], count: 0 };
  current.count += 1;
  if (status !== null) current.status_codes.push(status);
  requests.set(key, current);
}

function recordResponse(method, url, status) {
  const redacted = url.replace(caseId, "<case-id>").replace(runId, "<run-id>");
  const key = `${method} ${redacted}`;
  const current = requests.get(key) || { method, url: redacted, status_codes: [], count: 0 };
  current.status_codes.push(status);
  requests.set(key, current);
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: browserExecutable });
  const context = await browser.newContext();
  await context.route("**/*", async (route) => {
    const request = route.request();
    const method = request.method().toUpperCase();
    const url = request.url();
    audit(method, url);
    if (providerHosts.test(url)) return route.abort("blockedbyclient");
    if (writeMethods.has(method)) return route.abort("blockedbyclient");
    return route.continue();
  });
  const page = await context.newPage();
  page.on("response", (response) => recordResponse(response.request().method().toUpperCase(), response.url(), response.status()));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  await page.addInitScript(({ caseId, runId }) => {
    localStorage.setItem("planreview.activeRun.v1", JSON.stringify({ caseId, runId }));
    localStorage.setItem("planreview.layoutMode", "auto");
  }, { caseId, runId });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelectorAll(".finding").length === 25, null, { timeout: 30000 });
  const initialProgressRoot = await page.locator("#review-progress-root").evaluate((node) => node);
  const initialFindingIds = await page.locator(".finding").evaluateAll((nodes) => nodes.map((node) => node.dataset.findingId));

  await page.selectOption("#layout-mode", "compact");
  await page.selectOption("#layout-mode", "desktop");
  const finalFindingIds = await page.locator(".finding").evaluateAll((nodes) => nodes.map((node) => node.dataset.findingId));
  if (JSON.stringify(initialFindingIds) !== JSON.stringify(finalFindingIds)) throw new Error("finding IDs changed during layout switching");
  if (await page.locator("#review-progress-root").count() !== 1) throw new Error("progress root was duplicated");
  void initialProgressRoot;
  await page.selectOption("#layout-mode", "auto");

  const diagnosticsText = await page.locator(".diagnostics-card").innerText();
  for (const expected of ["29 条", "18 个", "40 条", "5 批", "阶段记录 8 项", "504 条", "119 条"]) {
    if (!diagnosticsText.includes(expected)) throw new Error(`missing diagnostics value: ${expected}`);
  }

  for (const [width, height] of viewports) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => {
      const replacements = [
        ["#summary-run-id", "<脱敏Run>"],
        ["#case-id-display", "<脱敏案例>"],
        ["#file-name-display", "<脱敏文档>"],
      ];
      for (const [selector, text] of replacements) {
        const node = document.querySelector(selector); if (node) node.textContent = text;
      }
      document.querySelectorAll(".finding-title").forEach((node, index) => { node.textContent = `脱敏问题 ${index + 1}`; });
      document.querySelectorAll(".finding .row span:not(.key)").forEach((node) => { node.textContent = "<脱敏内容>"; });
      document.querySelectorAll(".review-progress-event p").forEach((node) => { node.textContent = "<脱敏执行事件>"; });
      document.querySelectorAll(".review-progress-run").forEach((node) => { node.textContent = "Run <脱敏Run>"; });
      document.querySelectorAll(".preview-item strong").forEach((node, index) => { node.textContent = `脱敏问题 ${index + 1}`; });
    });
    await page.screenshot({ path: path.join(outputDir, `workbench-${width}x${height}.png`), fullPage: true });
  }

  await page.reload({ waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelectorAll(".finding").length === 25, null, { timeout: 30000 });
  const refreshedIds = await page.locator(".finding").evaluateAll((nodes) => nodes.map((node) => node.dataset.findingId));
  if (JSON.stringify(initialFindingIds) !== JSON.stringify(refreshedIds)) throw new Error("finding IDs changed after refresh");

  const auditRows = [...requests.values()].sort((a, b) => `${a.method} ${a.url}`.localeCompare(`${b.method} ${b.url}`));
  const businessWrites = auditRows.filter((row) => writeMethods.has(row.method));
  const providerRequests = auditRows.filter((row) => providerHosts.test(row.url));
  fs.writeFileSync(requestAuditPath, JSON.stringify({ base_url: baseUrl, requests: auditRows, business_write_count: businessWrites.length, provider_request_count: providerRequests.length, console_errors: consoleErrors }, null, 2));
  await browser.close();
  if (businessWrites.length) throw new Error("business write request detected");
  if (providerRequests.length) throw new Error("provider request detected");
  if (consoleErrors.length) throw new Error(`browser console errors: ${consoleErrors.join(" | ")}`);
  process.stdout.write(JSON.stringify({ finding_count: initialFindingIds.length, request_count: auditRows.reduce((sum, row) => sum + row.count, 0), screenshots: viewports.length }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
