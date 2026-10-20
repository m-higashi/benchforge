// Benchforge プラン提案: ローカルLLMが統計+総論を読み次プランを提案→良ければ保存。IIFEで閉じる。
(function () {
"use strict";

const el = id => document.getElementById(id);
function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

let LAST_PLAN = null, LAST_TEXT = "";

function fillSummaries(list) {
  const sel = el("sg-summary");
  const cur = sel.value;
  sel.innerHTML = '<option value="">（最新総論を使用）</option>'
    + (list || []).map(s => `<option value="${esc(s.name)}">${esc(s.period || "?")} ${esc(s.date || "")} (${s.n_configs ?? "?"}構成)</option>`).join("");
  if (window.BF_PICK_SUMMARY) { sel.value = window.BF_PICK_SUMMARY; window.BF_PICK_SUMMARY = ""; }  // まとめタブからの引き継ぎ
  else if (cur) sel.value = cur;
}

async function loadContext() {
  const period = el("sg-period").value;
  let d;
  try { d = await fetch("/api/strategy/context?period=" + encodeURIComponent(period)).then(r => r.json()); }
  catch (e) { return; }
  if (!el("sg-model").options.length) {   // モデルは初回のみ設定（期間変更で選択を消さない）
    el("sg-model").innerHTML = (d.models || []).map(m => `<option>${esc(m)}</option>`).join("");
    const pref = (d.models || []).find(m => /gemma4:12b/.test(m));
    if (pref) el("sg-model").value = pref;
  }
  el("sg-stats").textContent = d.stats || "(統計なし)";
  el("sg-meta").textContent = `（${d.n_configs}構成・${period}${d.has_summary ? "・最新総論あり" : ""}）`;
  fillSummaries(d.summaries);
}

async function propose() {
  el("sg-status").textContent = "生成中…（GPU使用）";
  el("sg-save").disabled = true; el("sg-planbox").style.display = "none";
  let d;
  try {
    d = await fetch("/api/strategy/propose", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: el("sg-model").value, note: el("sg-note").value,
                             period: el("sg-period").value, summary_name: el("sg-summary").value })
    }).then(r => r.json());
  } catch (e) {
    el("sg-status").textContent = "通信エラー";
    if (window.BF_NETFAIL) window.BF_NETFAIL(e && (e.message || e)); return;
  }
  if (d.error) { el("sg-status").textContent = "エラー: " + d.error; return; }

  LAST_TEXT = d.text || "";
  el("sg-text").innerHTML = `<div style="white-space:pre-wrap">${esc(LAST_TEXT)}</div>`;
  el("sg-planbox").style.display = "";
  if (d.plan) {
    LAST_PLAN = d.plan;
    el("sg-plan").textContent = JSON.stringify(d.plan, null, 2);
    el("sg-planok").innerHTML = '<span class="ok">✓ 有効なプラン</span>';
    el("sg-save").disabled = false;
    if (!el("sg-name").value) {
      const c0 = (d.plan.configs && d.plan.configs[0] && d.plan.configs[0].config_id) || "proposal";
      el("sg-name").value = String(c0).slice(0, 40);
    }
  } else {
    LAST_PLAN = null;
    el("sg-plan").textContent = "(プランJSONを抽出できませんでした)";
    el("sg-planok").innerHTML = `<span class="ng">⚠ ${esc(d.plan_error || "抽出失敗")}</span>`;
    el("sg-save").disabled = true;
  }
  el("sg-status").textContent = `完了（${esc(d.model || "")}）`;
}

async function save() {
  if (!LAST_PLAN) return;
  const name = (el("sg-name").value || "proposal").trim();
  el("sg-saved").textContent = "保存中…";
  let d;
  try {
    d = await fetch("/api/strategy/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, plan: LAST_PLAN, rationale: LAST_TEXT })
    }).then(r => r.json());
  } catch (e) {
    el("sg-saved").textContent = "通信エラー";
    if (window.BF_NETFAIL) window.BF_NETFAIL(e && (e.message || e)); return;
  }
  el("sg-saved").textContent = d.error ? ("エラー: " + d.error)
    : `保存しました → data/plans/${d.saved}.json（プランエディタで開けます）`;
}

function initStrategy() {
  el("sg-gen").onclick = propose;
  el("sg-save").onclick = save;
  el("sg-period").onchange = loadContext;
  const tab = document.querySelector('#toptabs .tab[data-tab="strategy"]');
  if (tab) tab.addEventListener("click", () => loadContext());   // 毎回: 最新まとめ一覧+引き継ぎ反映
  loadContext();
}
initStrategy();
})();
