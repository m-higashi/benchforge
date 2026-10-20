// Benchforge アンサンブル内訳: vote/合議構成の個体別正誤を生ログから可視化。IIFEで閉じる。
// メンバー数は応答(members)から動的に列生成＝モデル数が変わっても耐える。
(function () {
"use strict";

const el = id => document.getElementById(id);
function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
function mk(c) { return c ? '<span class="ok">○</span>' : '<span class="ng">×</span>'; }
function pct(x) { return (x * 100).toFixed(1); }
function pred(a) { return esc((a || []).join("|")) || "—"; }

let ENS = [];     // アンサンブル構成一覧

async function loadList() {
  const d = await fetch("/api/ensemble/list").then(r => r.json());
  ENS = d.entries || [];
  el("ens-sel").innerHTML = ENS.length
    ? ENS.map((e, i) => `<option value="${i}">${esc(e.config_id)} ・ ${e.models.length}モデル ・ `
        + `${(e.accuracy * 100).toFixed(0)}%${e.version ? " (" + esc(e.version) + ")" : ""}</option>`).join("")
    : '<option value="">（アンサンブル構成がまだありません）</option>';
  el("ens-meta").textContent = `${ENS.length}構成`;
}

async function analyze() {
  const i = el("ens-sel").value;
  if (i === "" || !ENS[+i]) { el("ens-status").textContent = "構成を選んでください"; return; }
  const e = ENS[+i];
  el("ens-status").textContent = "解析中…";
  const u = `/api/ensemble/breakdown?run_id=${encodeURIComponent(e.run_id)}`
    + `&config_id=${encodeURIComponent(e.config_id)}`;
  const d = await fetch(u).then(r => r.json());
  render(d);
  el("ens-status").textContent = "完了";
}

function summaryHtml(d) {
  const ms = d.member_stats || [];
  const dyn = d.dynamics || {};
  // メンバー別正答率テーブル
  let mt = `<table class="mdt"><tr><th>モデル</th>`
    + `<th class="num">正答率</th><th class="num">正答/採点</th>`
    + `<th class="num">棄権</th><th class="num">vote勝(vote正×自誤)</th><th class="num">自勝(vote誤×自正)</th>`
    + `<th>量子化</th><th>backend</th><th>think</th></tr>`;
  ms.forEach(m => {
    mt += `<tr><td>${esc(m.model)}</td>`
      + `<td class="num acc">${pct(m.accuracy)}%</td>`
      + `<td class="num">${m.correct}/${m.total}</td><td class="num">${m.abstain}</td>`
      + `<td class="num ok">${m.vote_win}</td><td class="num ng">${m.mem_win}</td>`
      + `<td class="muted">${esc(m.quant)}</td><td class="muted">${esc(m.backend)}</td><td class="muted">${esc(m.think)}</td></tr>`;
  });
  mt += `<tr><td><b>[VOTE] 合議結果</b></td>`
    + `<td class="num acc"><b>${pct(d.vote.accuracy)}%</b></td>`
    + `<td class="num">${d.vote.correct}/${d.vote.scored}</td><td class="num">—</td>`
    + `<td class="num">—</td><td class="num">—</td>`
    + `<td class="muted">—</td><td class="muted">—</td><td class="muted">—</td></tr></table>`;
  // 動態
  const dynRow = `<p class="note" style="margin:8px 0 0">アンサンブル動態（${d.scored}問）: `
    + `全員正解 <b>${dyn.all_correct}</b> ／ 全員誤答 <b class="ng">${dyn.all_wrong}</b> ／ `
    + `割れ→<b class="ok">vote救済 ${dyn.rescued}</b> ／ 割れ→<b class="ng">vote破綻 ${dyn.broke}</b>`
    + `<span class="muted">（破綻=正解メンバーがいたのに多数決で潰した数。救済−破綻が合議の実効）</span></p>`;
  return `<div class="tscroll">${mt}</div>` + dynRow;
}

function tableHtml(d, onlyWrong) {
  const members = d.members || [];
  const ansSpec = esc(JSON.stringify([{ run: d.run_id, cfg: d.config_id, label: "この構成" }]))
    .replace(/"/g, "&quot;");
  let h = '<p class="note">問題番号をクリックすると、その問題での解答内容を開けます。</p>'
    + '<table><thead><tr><th>問題</th><th>正解</th>'
    + members.map(m => `<th>${esc(m)}</th>`).join("")
    + '<th>VOTE</th><th>動態</th></tr></thead><tbody>';
  for (const r of d.rows) {
    const oks = members.map(m => (r.members[m] || {}).correct);
    const allOk = oks.every(Boolean), noneOk = !oks.some(Boolean);
    if (onlyWrong && allOk) continue;
    const tag = allOk ? "" : (noneOk ? "全員誤"
      : (r.vote_correct ? "救済" : "破綻"));
    const rowcls = r.vote_correct ? (allOk ? "" : "vw-a") : "vw-n";
    h += `<tr class="${rowcls}"><td class="ansq" data-qid="${esc(r.q_id)}" data-ans="${ansSpec}" `
      + `title="クリックで解答内容" style="cursor:pointer;text-decoration:underline">${esc(r.q_id)}</td>`
      + `<td>${pred(r.gold)}</td>`;
    for (const m of members) {
      const c = r.members[m] || {};
      h += `<td>${pred(c.pred)} ${mk(!!c.correct)}</td>`;
    }
    h += `<td>${pred(r.vote_pred)} ${mk(r.vote_correct)}</td><td class="muted">${tag}</td></tr>`;
  }
  return `<div class="tscroll">${h}</tbody></table></div>`;
}

function render(d) {
  if (!d.scored) {
    el("ens-summary").style.display = "none";
    el("ens-table").innerHTML = '<div class="empty">採点可能な問題がありません（gold突合0／生ログ未保存の可能性）。</div>';
    return;
  }
  el("ens-summary").style.display = "";
  el("ens-summary").innerHTML = summaryHtml(d)
    + `<label class="muted" style="display:inline-block;margin-top:8px">`
    + `<input type="checkbox" id="ens-onlywrong" checked> 誤答メンバーがいる問のみ表示</label>`;
  const draw = () => el("ens-table").innerHTML = tableHtml(d, el("ens-onlywrong").checked);
  draw();
  el("ens-onlywrong").onchange = draw;
}

function init() {
  el("ens-go").onclick = analyze;
  // タブ初回クリックで遅延ロード（sig.js と同じ流儀）
  const tab = document.querySelector('#toptabs .tab[data-tab="ensemble"]');
  if (tab) tab.addEventListener("click", () => { if (!ENS.length) loadList(); });
}
init();
})();
