// Benchforge ペア比較: 2構成を問題ごとに突き合わせ、勝ち負け/反転を表示。IIFEでスコープ閉じる。
(function () {
"use strict";

const el = id => document.getElementById(id);
function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
function mk(c) { return c ? '<span class="ok">○</span>' : '<span class="ng">×</span>'; }

let CMP_ENTRIES = [];   // ベンチ entry 一覧(best)

function optionsHtml() {
  return '<option value="">（構成を選択）</option>' + CMP_ENTRIES.map((e, i) =>
    `<option value="${i}">${esc(e.config_id)}  ${(e.accuracy * 100).toFixed(0)}%${e.version ? " (" + esc(e.version) + ")" : ""}</option>`).join("");
}

async function loadEntries() {
  const d = await fetch("/api/benchmarks/best").then(r => r.json());
  CMP_ENTRIES = d.entries || [];
  el("cmp-a").innerHTML = optionsHtml();
  el("cmp-b").innerHTML = optionsHtml();
  el("cmp-meta").textContent = `${CMP_ENTRIES.length}構成から選択`;
}

async function compare() {
  const ai = el("cmp-a").value, bi = el("cmp-b").value;
  if (ai === "" || bi === "") { el("cmp-status").textContent = "構成A/Bを選んでください"; return; }
  const A = CMP_ENTRIES[+ai], B = CMP_ENTRIES[+bi];
  // ★2つの警告を出す: ①データセット版の不一致 ②実行（ラン）の不一致
  //   ②は 2026-08 の実測にもとづく。同じ構成でも実行を分けると予測が約10%変わるため、
  //   別々の実行で測った2構成の差には「実行間のばらつき」が混ざる。
  const warns = [];
  if (A.version !== B.version)
    warns.push('⚠ version が異なります（dataset_epoch/wiki_version が違う比較は無効・§12）。同一versionでの比較を推奨。');
  if (A.run_id !== B.run_id)
    warns.push('⚠ <b>この2構成は別々の実行で測定されています</b>（' + esc(A.run_id) + ' と ' + esc(B.run_id) + '）。'
      + '同じ設定でも実行を分けると予測が入れ替わることが実測されています'
      + '（単体モデルは実行をまたいでも再現しますが、<b>合議のような多段構成では約10%の予測が変わりました</b>）。'
      + 'したがって、ここで見えている差には<b>手法の差と実行間のばらつきが混ざっています</b>。'
      + '差を手法の効果として主張するなら、<b>2構成を同じ実行の中で走らせて測り直してください</b>。');
  el("cmp-warn").innerHTML = warns.join("<br>");
  el("cmp-status").textContent = "比較中…";
  const u = `/api/compare?a_run=${encodeURIComponent(A.run_id)}&a_cfg=${encodeURIComponent(A.config_id)}`
    + `&b_run=${encodeURIComponent(B.run_id)}&b_cfg=${encodeURIComponent(B.config_id)}`;
  const d = await fetch(u).then(r => r.json());
  renderCompare(d, A, B);
  el("cmp-status").textContent = "完了";
}

function pct(x) { return (x * 100).toFixed(1); }

function statsHtml(st) {
  if (!st) return "";
  const pStr = st.mcnemar_p < 0.001 ? "<0.001" : st.mcnemar_p.toFixed(3);
  const sigCls = st.sig.indexOf("有意") === 0 ? "ok" : (st.sig.indexOf("傾向") === 0 ? "" : "muted");
  return `<table class="mdt" style="margin-top:8px">`
    + `<tr><th></th><th>A</th><th>B</th></tr>`
    + `<tr><td>正答率 (95%CI)</td>`
    + `<td>${pct(st.a_acc)}% <span class="muted">[${pct(st.a_ci[0])}–${pct(st.a_ci[1])}]</span></td>`
    + `<td>${pct(st.b_acc)}% <span class="muted">[${pct(st.b_ci[0])}–${pct(st.b_ci[1])}]</span></td></tr></table>`
    + `<p class="note" style="margin:6px 0 0">McNemar 検定（対応あり・両者が解いた ${st.n_paired}問）: `
    + `不一致 Aのみ正解 <b class="ok">${st.disc_a}</b> / Bのみ正解 <b class="ng">${st.disc_b}</b>　`
    + `p = <b class="${sigCls}">${pStr}</b> → <b class="${sigCls}">${esc(st.sig)}</b>`
    + `　<span class="muted">（差 B−A = ${(st.diff >= 0 ? "+" : "") + pct(st.diff)}%pt。95%CI が重なる/p≥0.05 なら誤差圏）</span></p>`;
}

function sliceCmpTable(title, arr) {
  if (!arr || !arr.length) return "";
  let h = `<table class="mdt"><tr><th>${esc(title)}</th><th class="num">A%</th><th class="num">B%</th><th class="num">不一致A/B</th><th>p / 判定</th></tr>`;
  arr.forEach(s => {
    const cls = s.sig === "有意" ? "ok" : (s.sig === "傾向" ? "" : "muted");
    const pStr = s.mcnemar_p < 0.001 ? "<0.001" : s.mcnemar_p.toFixed(3);
    h += `<tr><td>${esc(String(s.key))} <span class="muted">(n${s.n_paired})</span></td>`
      + `<td class="num">${pct(s.a_acc)}</td><td class="num">${pct(s.b_acc)}</td>`
      + `<td class="num">${s.disc_a}/${s.disc_b}</td>`
      + `<td><span class="${cls}">${pStr} ${esc(s.sig)}</span></td></tr>`;
  });
  return h + "</table>";
}

function slicesCmpHtml(sl) {
  // ★slices 自体が無い場合も案内は出す（黙って消さない）
  if (!sl) sl = {};
  const axes = [
    // ★年・解答数は trials だけから導出できるので、どのデータでも出る（2026-08-25）
    ["by_year", "年"], ["by_label", "ラベル"], ["by_nselect", "解答数"],
    // ↓以下は question_attributes.csv があるときだけ出る
    ["by_qtype", "設問タイプ"], ["by_region", "領域"],
    ["by_nimages", "画像枚数"], ["by_stemlen", "臨床情報量"],
  ];
  const tables = axes.map(([k, t]) => sliceCmpTable(t, sl[k])).filter(Boolean);
  // ★出せなかった軸は「なぜ出ないか」を必ず書く。黙って消すと利用者は故障と区別できない。
  const missing = sl._missing || [];
  // ★長い説明はかえって読まれない。1行で言い切り、操作できる場所へ直接リンクする。
  const note = missing.length
    ? `<div class="note muted" style="margin:6px 0 2px">`
      + `<a href="#" data-go="manage">問題の管理</a> でタグを付けると、タグごとの分析も出せます。</div>`
    : "";
  if (!tables.length) return note;
  return '<div class="note" style="margin:8px 0 2px">スライス別の有意差（B−A をどこで稼いだか）</div>'
    + '<div style="display:flex;gap:18px;flex-wrap:wrap">'
    + tables.map(h => `<div>${h}</div>`).join("") + "</div>" + note;
}

function renderCompare(d, A, B) {
  const s = d.summary;
  el("cmp-summary").style.display = "";
  const archTxt = arr => (arr || []).length
    ? arr.map(x => `${esc(x.type || "?")}${x.model ? "：" + esc(x.model) : ""}`).join(" → ")
    : '<span class="muted">記録なし</span>';
  el("cmp-summary").innerHTML =
    `<table class="mdt"><tr><th></th><th>A: ${esc(A.config_id)}</th><th>B: ${esc(B.config_id)}</th></tr>`
    + `<tr><td>構成（処理の流れ：段の種類とモデル）</td>`
    + `<td>${archTxt(d.a_arch)}</td><td>${archTxt(d.b_arch)}</td></tr>`
    + `<tr><td>正答率</td><td>${(s.a_correct / s.n * 100).toFixed(1)}% (${s.a_correct}/${s.n})</td>`
    + `<td>${(s.b_correct / s.n * 100).toFixed(1)}% (${s.b_correct}/${s.n})</td></tr></table>`
    + `<p class="note" style="margin:8px 0 0">A のみ正解 <b class="ok">${s.a_only}</b> ／ `
    + `B のみ正解 <b class="ng">${s.b_only}</b> ／ 両方正解 ${s.both} ／ 両方誤り ${s.neither}`
    + `　<span class="muted">（差し引き A−B = ${s.a_correct - s.b_correct}。反転 ${s.a_only + s.b_only}問が両者の違い）</span></p>`
    + statsHtml(d.stats)
    + slicesCmpHtml(d.slices);

  const vcls = { a: "vw-a", b: "vw-b", both: "", neither: "vw-n" };
  const vlabel = { a: "A勝", b: "B勝", both: "同(両正)", neither: "同(両誤)" };
  // 問題の属性（年・ラベル等）は結果に付いている分だけ列にする（無いデータでは列自体を出さない）
  const attrKeys = [];
  const ATTR_JA = { year: "年", label: "ラベル", qtype: "設問タイプ", region: "領域",
                    n_images: "画像枚数", n_select: "解答数" };
  Object.keys(ATTR_JA).forEach(k => {
    if (d.rows.some(r => r.attrs && r.attrs[k] !== undefined && r.attrs[k] !== "")) attrKeys.push(k);
  });
  let h = '<p class="note">問題番号をクリックすると、その問題で両者が何と答えたかを開けます。</p>'
    + '<table><thead><tr><th>問題</th>'
    + attrKeys.map(k => `<th>${esc(ATTR_JA[k])}</th>`).join("")
    + '<th>正解</th><th>A pred</th><th>A</th>'
    + '<th>B pred</th><th>B</th><th>判定</th></tr></thead><tbody>';
  // 問題番号をクリックすると、その問題での A/B の解答内容を開く（qanswer.js が受ける）
  const ansSpec = esc(JSON.stringify([
    { run: A.run_id, cfg: A.config_id, label: "A" },
    { run: B.run_id, cfg: B.config_id, label: "B" },
  ])).replace(/"/g, "&quot;");
  for (const r of d.rows) {
    h += `<tr class="${vcls[r.verdict]}"><td class="ansq" data-qid="${esc(r.q_id)}" data-ans="${ansSpec}" `
      + `title="クリックで解答内容" style="cursor:pointer;text-decoration:underline">${esc(r.q_id)}</td>`
      + attrKeys.map(k => `<td class="muted">${esc((r.attrs || {})[k] ?? "")}</td>`).join("")
      + `<td>${esc((r.gold || []).join("|"))}</td>`
      + `<td>${esc((r.a_pred || []).join("|"))}</td><td>${mk(r.a_correct)}</td>`
      + `<td>${esc((r.b_pred || []).join("|"))}</td><td>${mk(r.b_correct)}</td>`
      + `<td>${vlabel[r.verdict]}</td></tr>`;
  }
  el("cmp-table").innerHTML = h + "</tbody></table>";
}

async function initCompare() {
  await loadEntries();
  el("cmp-go").onclick = compare;
}
initCompare();
})();
