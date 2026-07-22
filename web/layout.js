"use strict";

(function (root) {
  const STORAGE_KEY = "planreview.layoutMode";
  const MODES = Object.freeze(["auto", "desktop", "compact"]);

  function normalizeLayoutMode(value) {
    return MODES.includes(value) ? value : "auto";
  }

  function resolveLayoutMode(mode, width) {
    const normalized = normalizeLayoutMode(mode);
    if (normalized !== "auto") return normalized;
    return Number(width) >= 900 ? "desktop" : "compact";
  }

  function desktopDensity(width) {
    if (Number(width) >= 1280) return "full";
    if (Number(width) >= 900) return "simplified";
    return "compact";
  }

  function createLayoutController(options = {}) {
    const windowRef = options.window || root;
    const documentRef = options.document || root.document;
    const storage = options.storage || root.localStorage;
    let mode = normalizeLayoutMode(storage?.getItem?.(STORAGE_KEY));

    function apply() {
      const width = Number(windowRef.innerWidth || documentRef?.documentElement?.clientWidth || 0);
      const resolved = resolveLayoutMode(mode, width);
      if (documentRef?.body) {
        documentRef.body.dataset.layoutMode = mode;
        documentRef.body.dataset.layoutResolved = resolved;
        documentRef.body.dataset.desktopDensity = desktopDensity(width);
      }
      const control = documentRef?.querySelector?.("#layout-mode");
      if (control) control.value = mode;
      documentRef?.dispatchEvent?.(new CustomEvent("planreview:layout-change", {
        detail: { mode, resolved, width },
      }));
      return { mode, resolved, width };
    }

    function setMode(value) {
      mode = normalizeLayoutMode(value);
      storage?.setItem?.(STORAGE_KEY, mode);
      return apply();
    }

    function onResize() {
      apply();
    }

    windowRef?.addEventListener?.("resize", onResize);
    return {
      apply,
      setMode,
      getMode: () => mode,
      destroy: () => windowRef?.removeEventListener?.("resize", onResize),
    };
  }

  const api = { STORAGE_KEY, MODES, normalizeLayoutMode, resolveLayoutMode, desktopDensity, createLayoutController };
  Object.assign(root, api);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
