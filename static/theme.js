/* 配色の切替（明るい / 暗い / OSに合わせる）。IIFE 必須（同一グローバル共有のため）。 */
(function () {
  "use strict";
  const KEY = "benchforge-theme";           // 端末ごとに覚える
  const ORDER = ["auto", "light", "dark"];  // 押すたびに巡回
  const LABEL = { auto: "配色: OSに合わせる", light: "配色: 明るい", dark: "配色: 暗い" };

  function apply(mode) {
    const root = document.documentElement;
    // auto は属性を外して @media(prefers-color-scheme) に委ねる
    if (mode === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);
    const b = document.getElementById("theme-btn");
    if (b) b.textContent = LABEL[mode] || LABEL.auto;
  }

  function current() {
    const v = localStorage.getItem(KEY);
    return ORDER.includes(v) ? v : "auto";
  }

  // ★ちらつき防止のため、DOM 構築を待たずに即適用する
  apply(current());

  document.addEventListener("DOMContentLoaded", () => apply(current()));
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#theme-btn")) return;
    const next = ORDER[(ORDER.indexOf(current()) + 1) % ORDER.length];
    localStorage.setItem(KEY, next);
    apply(next);
  });
})();
