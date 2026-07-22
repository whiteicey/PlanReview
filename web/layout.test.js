"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { normalizeLayoutMode, resolveLayoutMode, desktopDensity, createLayoutController } = require("./layout.js");

test("layout modes resolve at the documented breakpoints", () => {
  assert.equal(normalizeLayoutMode("unknown"), "auto");
  assert.equal(resolveLayoutMode("auto", 899), "compact");
  assert.equal(resolveLayoutMode("auto", 900), "desktop");
  assert.equal(resolveLayoutMode("compact", 1440), "compact");
  assert.equal(desktopDensity(1279), "simplified");
  assert.equal(desktopDensity(1280), "full");
});

test("layout controller changes CSS state without moving business DOM", () => {
  const listeners = new Map();
  const body = { dataset: {} };
  const control = { value: "" };
  const document = {
    body,
    documentElement: { clientWidth: 1440 },
    querySelector: (selector) => selector === "#layout-mode" ? control : null,
    dispatchEvent: () => {},
  };
  const window = {
    innerWidth: 1440,
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name) => listeners.delete(name),
  };
  const values = new Map();
  const storage = { getItem: () => "auto", setItem: (key, value) => values.set(key, value) };
  const controller = createLayoutController({ document, window, storage });
  controller.apply();
  assert.equal(body.dataset.layoutResolved, "desktop");
  controller.setMode("compact");
  assert.equal(body.dataset.layoutResolved, "compact");
  assert.equal(values.get("planreview.layoutMode"), "compact");
  assert.equal(document.querySelectorAll, undefined, "controller has no DOM reparenting path");
  controller.destroy();
  assert.equal(listeners.size, 0);
});
