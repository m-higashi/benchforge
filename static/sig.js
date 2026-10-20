// Benchforge 有意差マトリクス: 同一version内の全構成を最良構成とのMcNemarでランク付け。IIFEで閉じる。
(function () {
"use strict";

const el = id => document.getElementById(id);
function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
function pct(x) { return (x * 100).toFixed(1) + "%"; }

let VERSIONS = [];   // {dataset_epoch, wiki_version, split, label}

async function loadVersions() {
  // 成績一覧の全構成から、データセット(epoch×wiki)の一覧を組み立てる。
  // ★2026-08-14 修正: ここだけ削除済みの /api/benchmarks/latest を呼んでいた。
  //   404 が JSON で返るため例外にならず VERSIONS が空のまま進み、
  //   **データセットの選択肢が1つも出ない**状態だった（Win/Mac の実機検証が独立に検出）。
  //   ⚠ 単に URL を best へ差し替えるだけでは直らない。best は {entries:[…]} を返し
  //     versions を持たないため、ここで entries をまとめ直す必要がある。
  const d = await fetch("/api/benchmarks/best").then(r => r.json());
  const seen = new Map();
  (d.entries || []).forEach(e => {
    const key = e.version || `${e.dataset_epoch || ""} / ${e.wiki_version || ""}`;
    if (!seen.has(key)) {
      seen.set(key, {
        dataset_epoch: e.dataset_epoch || "", wiki_version: e.wiki_version || "",
        split: e.split || "", label: key.trim() === "/" ? "（既定）" : key,
      });
    }
  });
  VERSIONS = [...seen.values()];
  el("sig-version").innerHTML = '<option value="-1">（最大構成数のデータセットを自動選択）</option>'
    + VERSIONS.map((v, i) => `<option value="${i}">${esc(v.label)}</option>`).join("");
}

async function run() {
  el("sig-status").textContent = "計算中…";
  const i = +el("sig-version").value;
  let url = "/api/significance";
  if (i >= 0 && VERSIONS[i]) {
    const v = VERSIONS[i];
    url += `?dataset_epoch=${encodeURIComponent(v.dataset_epoch || "")}`
      + `&wiki_version=${encodeURIComponent(v.wiki_version || "")}`
      + `&split=${encodeURIComponent(v.split || "")}`;
  }
  let d;
  try { d = await fetch(url).then(r => r.json()); }
  catch (e) {
    el("sig-status").textContent = "取得失敗";
    if (window.BF_NETFAIL) window.BF_NETFAIL(e && (e.message || e)); return;
  }
  render(d);
  el("sig-status").textContent = "完了";
}

function render(d) {
  el("sig-meta").textContent = d.version ? `（データセット: ${d.version}）` : "";
  const rows = d.rows || [];
  if (!rows.length) { el("sig-table").innerHTML = '<div class="empty">この version に構成がありません。</div>'; return; }
  let h = '<table><thead><tr><th>順位</th><th>構成</th><th class="num">正答率</th>'
    + '<th class="num">95%CI</th><th class="num">採点</th><th>最良との差(McNemar)</th></tr></thead><tbody>';
  rows.forEach((r, i) => {
    const ref = r.is_ref;
    const cls = ref ? "ok" : (r.vs_ref_sig === "有意" ? "ng" : (r.vs_ref_sig === "傾向" ? "" : "muted"));
    const pStr = r.vs_ref_p < 0.001 ? "<0.001" : r.vs_ref_p.toFixed(3);
    const verdict = ref ? '<b class="ok">基準(最良)</b>'
      : `<span class="${cls}">p=${pStr} → ${esc(r.vs_ref_sig)}${r.vs_ref_sig === "有意" ? "に劣る" : ""}</span>`;
    // ★基準と別の実行で測られた構成には印を付ける（差に実行間のばらつきが混ざる）
    const runMark = r.same_run_as_ref === false
      ? ' <span class="muted" title="基準構成とは別の実行で測定されています。差には実行間のばらつきが混ざります">◇別の実行</span>' : "";
    h += `<tr><td>${i + 1}</td><td>${esc(r.config_id)}${runMark}</td>`
      + `<td class="num acc">${pct(r.acc)}</td>`
      + `<td class="num muted">${(r.ci[0] * 100).toFixed(0)}–${(r.ci[1] * 100).toFixed(0)}</td>`
      + `<td class="num">${r.correct}/${r.scored}</td><td>${verdict}</td></tr>`;
  });
  // ★実行をまたいだ比較がどれだけ混ざっているかを明示する（今週の実測にもとづく注意喚起）
  const nDiff = d.n_diff_run || 0;
  const runNote = nDiff
    ? `<p class="note" style="color:var(--warn,#e0a050)">⚠ ${nDiff} 構成が基準（${esc(d.ref || "")}）とは`
      + `<b>別の実行</b>で測定されています（◇印）。同じ設定でも実行を分けると予測が入れ替わることが`
      + `実測されており、<b>多段の合議構成では約10%が変化</b>しました。ここでの差には手法の効果と`
      + `実行間のばらつきが混ざっているため、順位をそのまま実力差として読まないでください。`
      + `厳密に比べたい構成は<b>同じ実行の中で一緒に走らせて</b>測り直すのが確実です。</p>`
    : '<p class="note" style="color:var(--ok,#6fc79a)">✓ すべての構成が同じ実行で測定されています（実行間のばらつきは混ざりません）。</p>';
  el("sig-table").innerHTML = h + "</tbody></table>" + runNote
    + '<p class="note">「劣る」＝最良構成に有意(p<0.05)に負けている。誤差圏＝最良と統計的に区別できない（実質同等）。</p>';
}

async function initSig() {
  await loadVersions();
  el("sig-go").onclick = run;
  const tabBtn = document.querySelector('#toptabs .tab[data-tab="sig"]');
  if (tabBtn) tabBtn.addEventListener("click", () => { if (!el("sig-table").innerHTML) run(); });
}
initSig();
})();
