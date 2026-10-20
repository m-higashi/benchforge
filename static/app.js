// Benchforge P1 フロント: 最高/最新ベンチマークの取得・表示・並び替え
// 名前衝突防止: 全体を IIFE で包みファイルスコープに閉じる（3JSはグローバル共有のため）
(function () {
"use strict";

function ci95(e) {
  const c = e.accuracy_ci;
  return (c && c.length === 2) ? `${(c[0] * 100).toFixed(0)}–${(c[1] * 100).toFixed(0)}` : "";
}
function abstainCell(e) {
  const r = e.abstain_rate;
  return (e.scored ? `${e.abstained} (${(r * 100).toFixed(0)}%)` : String(e.abstained ?? ""));
}

const COLS = [
  {key: "config_id",     label: "構成",        type: "str"},
  {key: "accuracy",      label: "正答率",      type: "num", fmt: v => (v * 100).toFixed(1) + "%", cls: "acc"},
  {key: "accuracy_ci",   label: "95%CI",       type: "str", fmtEntry: ci95, cls: "muted"},
  {key: "correct",       label: "正答",        type: "num"},
  {key: "scored",        label: "採点数",      type: "num"},
  {key: "abstained",     label: "棄権",        type: "num", fmtEntry: abstainCell},
  {key: "errors",        label: "エラー",      type: "num"},
  // ★温度はステージ実効値（生成側の stage_params があればそれ）。段ごとに違えば "0.0,0.7" と併記される。
  {key: "temperature",   label: "temp",        type: "str",
   fmtEntry: e => (e.temperature === null || e.temperature === undefined) ? "—" : e.temperature},
  {key: "top_k",         label: "top_k",       type: "num", cls: "muted", fmtEntry: e => (e.top_k === null || e.top_k === undefined) ? "—" : e.top_k},
  {key: "top_p",         label: "top_p",       type: "num", cls: "muted", fmtEntry: e => (e.top_p === null || e.top_p === undefined) ? "—" : e.top_p},
  {key: "num_predict", label: "出力上限", type: "str", cls: "muted", fmtEntry: e => (e.num_predict === null || e.num_predict === undefined) ? "—" : e.num_predict},
  {key: "num_ctx", label: "文脈長", type: "str", cls: "muted", fmtEntry: e => (e.num_ctx === null || e.num_ctx === undefined) ? "—" : e.num_ctx},
  // 反復ペナルティ: 未記録＝推論エンジンの既定に委ねている（ollama 0.30.10 は 1.1 で中立ではない）
  {key: "repeat_penalty", label: "反復ペナ", type: "str", cls: "muted",
   fmtEntry: e => (e.repeat_penalty === null || e.repeat_penalty === undefined)
                  ? "既定" : e.repeat_penalty},
  {key: "quant",         label: "量子化",      type: "str", cls: "muted"},
  {key: "backend",       label: "backend",     type: "str", cls: "muted"},
  {key: "think",         label: "think",       type: "str", cls: "muted"},
  {key: "split",         label: "split",       type: "str"},
  {key: "run_id",        label: "run_id",      type: "str"},
];

function cell(entry, col) {
  let v = col.fmtEntry ? col.fmtEntry(entry)
        : col.fmt ? col.fmt(entry[col.key] ?? 0)
        : entry[col.key];
  if (v === undefined || v === null) v = "";
  const cls = [col.type === "num" ? "num" : "", col.cls || ""].filter(Boolean).join(" ");
  return `<td class="${cls}">${escapeHtml(String(v))}</td>`;
}

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
}

function renderTable(targetId, entries, sortState) {
  const el = document.getElementById(targetId);
  if (!entries || entries.length === 0) {
    el.innerHTML = `<div class="empty">データがありません（入力源にまだベンチマークがありません）。</div>`;
    return;
  }
  if (sortState && sortState.key) {
    const col = COLS.find(c => c.key === sortState.key);
    const dir = sortState.dir;
    entries = entries.slice().sort((a, b) => {
      let x = a[sortState.key], y = b[sortState.key];
      if (col.type === "num") { x = +x || 0; y = +y || 0; return dir * (x - y); }
      return dir * String(x).localeCompare(String(y));
    });
  }
  const head = COLS.map(c =>
    `<th class="${c.type === 'num' ? 'num' : ''}" data-key="${c.key}">${c.label}</th>`).join("");
  const body = entries.map(e => `<tr>${COLS.map(c => cell(e, c)).join("")}</tr>`).join("");
  el.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  el.querySelectorAll("tbody tr").forEach((tr, i) => {
    tr.classList.add("clickable");
    tr.onclick = () => openDetail(entries[i], tr);
  });
  el.querySelectorAll("th").forEach(th => {
    th.onclick = () => {
      const key = th.dataset.key;
      const dir = (sortState.key === key && sortState.dir === -1) ? 1 : -1;
      renderTable(targetId, entries, {key, dir});
    };
  });
}

// ---- 最高ベンチマーク ----
let bestEntries = [];
async function loadBest() {
  const r = await fetch("/api/benchmarks/best");
  bestEntries = (await r.json()).entries || [];
  renderTable("best-table", bestEntries, {key: "accuracy", dir: -1});
}


// ---- 詳細（問題別ドリルダウン + 生出力） ----
function mark(c) {
  return c === true ? '<span class="ok">○</span>'
    : c === false ? '<span class="ng">×</span>' : '<span class="na">-</span>';
}

function archHTML(arch) {
  if (!arch || !arch.length) return "";
  const nodes = arch.map(s => {
    const m = s.model ? `<small>${escapeHtml(s.model)}</small>` : "";
    return `<span class="node">${escapeHtml(s.type)}${m}</span>`;
  });
  return '<div class="arch" style="margin:6px 0 10px"><div class="archrow">'
    + '<span class="node">入力</span><span class="arrow">→</span>'
    + nodes.join('<span class="arrow">→</span>')
    + '<span class="arrow">→</span><span class="node">解答</span></div></div>';
}

function sliceTable(title, arr) {
  if (!arr || !arr.length) return "";
  let h = `<table class="mdt"><tr><th>${escapeHtml(title)}</th><th class="num">正答率</th><th class="num">n</th></tr>`;
  arr.forEach(s => {
    h += `<tr><td>${escapeHtml(String(s.key))}</td>`
      + `<td class="num">${(s.acc * 100).toFixed(1)}% <span class="muted">[${(s.ci[0] * 100).toFixed(0)}–${(s.ci[1] * 100).toFixed(0)}]</span></td>`
      + `<td class="num">${s.correct}/${s.scored}</td></tr>`;
  });
  return h + "</table>";
}

function slicesHTML(sl) {
  if (!sl) return "";
  // 表示する軸（キー, タイトル）。属性CSVが無ければ該当軸は空で自動的に省略。
  const axes = [
    ["by_label", "ラベル"],
    ["by_qtype", "設問タイプ"], ["by_region", "領域"],
    ["by_nselect", "解答数"], ["by_nimages", "画像枚数"], ["by_stemlen", "臨床情報量"],
  ];
  const tables = axes.map(([k, t]) => sliceTable(t, sl[k])).filter(Boolean);
  if (!tables.length) return "";
  return '<div class="note" style="margin:6px 0 2px">スライス（属性別の正答率＝どんな問題で効く/崩れるか）</div>'
    + '<div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:8px">'
    + tables.map(h => `<div>${h}</div>`).join("") + "</div>";
}

function detailHTML(d) {
  const rows = d.rows || [];
  // ★解答内容を持たない成績（同梱サンプルなど）は、その旨と「どうすれば見られるか」を先に出す。
  //   前回の実機検証で、初めての人が最初にやる操作＝同梱の成績をクリックすることであり、
  //   そこで何も出ないと「壊れている」と受け取られると分かった（2026-08-10 / 08-14）。
  const noRaw = !d.has_raw;
  const banner = noRaw
    ? '<div class="callout-warn" style="margin:0 0 10px">'
      + '<b>この成績には、AI の解答内容が保存されていません。</b>'
      + '（同梱のサンプル成績は、正答率などの数値だけを収めています）<br>'
      + 'この構成を<b>自分で一度実行すると、ここに1問ごとの解答文が出ます</b>。'
      + ' <a href="#" data-go="nightly">まとめて実行をひらく</a></div>'
    : "";
  let html = '<div class="detailbox">' + banner
    + `<div class="detail-head"><strong>${escapeHtml(d.config_id)} `
    + `<span class="muted">(${escapeHtml(d.run_id) || "同梱サンプル"})</span></strong>`
    + `<span class="note" style="margin:0">${noRaw ? "行クリックで問題ごとの内訳を展開"
                                                   : "行クリックで生出力を展開"}</span></div>`
    + archHTML(d.arch)
    + slicesHTML(d.slices)
    + '<table><thead><tr><th>問題</th><th>pred</th><th>gold</th>'
    + '<th>正誤</th><th class="num">秒</th></tr></thead><tbody>';
  rows.forEach((r, i) => {
    const pred = (r.pred || []).join("|") || (r.error ? "ERR" : "");
    const gold = (r.gold || []).join("|");
    html += `<tr class="qrow" data-i="${i}"><td>${escapeHtml(r.q_id || "")}</td>`
      + `<td>${escapeHtml(pred)}</td><td>${escapeHtml(gold)}</td>`
      + `<td>${mark(r.correct)}</td><td class="num">${r.latency_sec ?? ""}</td></tr>`;
    // ★「(生出力なし)」だけでは、なぜ無いのか・どうすれば出るのかが分からない
    const raw = r.raw_output || r.error || "";
    const rawCell = raw
      ? `<div class="rawcell"><b>生出力</b>\n${escapeHtml(raw)}</div>`
      : '<div class="rawcell"><b>解答内容はありません</b>\n'
        + 'この成績には AI の解答文が保存されていません。'
        + 'この構成を「まとめて実行」で一度実行すると、ここに実際の解答文が出ます。</div>';
    html += `<tr class="rawrow" data-raw="${i}" style="display:none"><td colspan="5">`
      + attrsHTML(r.attrs)
      + rawCell
      + promptDetailHTML(r) + `</td></tr>`;
  });
  html += "</tbody></table>";
  // ★解答内容がある成績のときだけ注記する。無い成績には上部の案内を既に出しており、
  //   そこへ「記録されません」を重ねると、何が足りないのか分からなくなる。
  //   ⚠ 以前の文言は削除済みの機能（RAG）と、利用者に意味のない日付を含んでいた（2026-08-14 修正）。
  if (d.has_raw && !d.has_prompt) {
    html += '<p class="note">※ この成績には、AI へ送った指示文の記録は含まれていません'
      + '（解答文のみ）。</p>';
  }
  return html + "</div>";
}

// 各問に付与された属性ラベルをピル表示（クリック展開時）
function attrsHTML(a) {
  if (!a || !Object.keys(a).length) return "";
  const label2mri = {};   // ラベル値の意味はデータセット依存のため既定では変換しない
  const items = [
    ["ラベル", label2mri[String(a.label)] || (a.label ? "値" + a.label : "")],
    ["設問", a.qtype], ["領域", a.region], ["解答数", a.n_select],
    ["画像", a.n_images], ["情報量", a.stem_len],
  ];
  const pills = items.filter(([, v]) => v)
    .map(([k, v]) => `<span class="pill">${escapeHtml(k)}: ${escapeHtml(String(v))}</span>`)
    .join(" ");
  if (!pills) return "";
  return `<div style="margin:2px 0 8px"><span class="muted" style="margin-right:6px">属性</span>${pills}</div>`;
}

// 各問の「注入プロンプト（注入後＝RAG/所見を含む）」と RAG 参照ページを折りたたみ表示
function promptDetailHTML(r) {
  const sd = r.stages_detail || [];
  const pre = 'style="max-height:320px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:#0000000d;padding:8px;border-radius:6px;margin:4px 0;font-size:12px"';
  let h = "";
  sd.forEach(s => {
    const head = escapeHtml(s.type || "")
      + (s.prompt_id ? " (" + escapeHtml(s.prompt_id) + ")" : "")
      + (s.model ? " · " + escapeHtml(s.model) : "");
    h += `<details style="margin:6px 0"><summary>🧩 注入プロンプト: ${head}</summary>`
      + (s.system ? `<div class="muted">system</div><pre ${pre}>${escapeHtml(s.system)}</pre>` : "")
      + `<div class="muted">user（注入後＝RAG知識・所見を含む最終プロンプト）</div><pre ${pre}>${escapeHtml(s.user || "")}</pre>`
      + (s.output ? `<div class="muted">この段の出力</div><pre ${pre}>${escapeHtml(s.output)}</pre>` : "")
      + `</details>`;
  });
  if (r.wiki_pages && r.wiki_pages.length) {
    h += `<details style="margin:6px 0"><summary>📚 RAG参照ページ (${r.wiki_pages.length})</summary>`
      + `<pre ${pre}>${escapeHtml(r.wiki_pages.join("\n"))}</pre></details>`;
  }
  return h;
}

// クリックした行の「真下」に詳細を挿入（アコーディオン・1テーブル1つ）
async function openDetail(entry, tr) {
  // ★run_id が空でも開く（2026-08-14 修正）。同梱サンプルの成績には run_id が無く、
  //   `!entry.run_id` が空文字で真になるため **クリックしても何も起きなかった**。
  //   「反応しない」は利用者から見て故障と区別できない。開いた上で、
  //   中身が無い理由と次の一手を出す（detailHTML 側）。
  if (!entry || !entry.config_id) return;
  const tbody = tr.parentElement;
  const already = tr.classList.contains("open");
  tbody.querySelectorAll("tr.detailrow").forEach(e => e.remove());
  tbody.querySelectorAll("tr.clickable.open").forEach(x => x.classList.remove("open"));
  if (already) return;  // 同じ行の再クリック=閉じる

  const dr = document.createElement("tr");
  dr.className = "detailrow";
  dr.innerHTML = `<td colspan="${COLS.length}"><div class="detailbox">読み込み中…</div></td>`;
  tr.insertAdjacentElement("afterend", dr);
  tr.classList.add("open");

  const url = `/api/benchmark/detail?run_id=${encodeURIComponent(entry.run_id)}`
    + `&config_id=${encodeURIComponent(entry.config_id)}`;
  const d = await fetch(url).then(r => r.json());
  if (!tr.classList.contains("open")) return;  // 途中で閉じられた
  dr.querySelector("td").innerHTML = detailHTML(d);
  dr.querySelectorAll(".qrow").forEach(q => {
    q.onclick = () => {
      const rr = dr.querySelector(`tr.rawrow[data-raw="${q.dataset.i}"]`);
      rr.style.display = rr.style.display === "none" ? "" : "none";
    };
  });
}

function closeAllDetails() {
  document.querySelectorAll("tr.detailrow").forEach(e => e.remove());
  document.querySelectorAll("tr.clickable.open").forEach(x => x.classList.remove("open"));
}

// ---- トップタブ ----
function initTopTabs() {
  document.querySelectorAll("#toptabs .tab").forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll("#toptabs .tab").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
      document.getElementById("pane-" + btn.dataset.tab).classList.add("active");
      closeAllDetails();  // タブ切替で詳細(生ログ)行を閉じる
      // アドレス欄に現在のタブを残す（そのURLを開けば同じタブが出る＝人に渡せる）
      if (history.replaceState) history.replaceState(null, "", "#" + btn.dataset.tab);
    };
  });
  // 起動時、#タブ名 が付いていればそのタブを開く
  const want = (location.hash || "").replace(/^#/, "");
  if (want) {
    const btn = document.querySelector('#toptabs .tab[data-tab="' + want + '"]');
    if (btn) btn.click();
  }
}

async function loadMeta() {
  const m = await fetch("/api/meta").then(r => r.json());
  // 版数を必ず出す（不具合報告のとき「どの版か」が分からないと突き合わせられない）
  document.getElementById("meta").textContent =
    `版 ${m.version || "?"} ・ 表示時刻 ${m.generated_at}`;
}

initTopTabs();

// 他ペインからタブへ誘導するリンク: <a data-go="selbias"> のように書く。
// タブ切替は #toptabs のボタンにしか bind されていないため、該当ボタンを押す。
document.addEventListener("click", (e) => {
  const g = e.target.closest("[data-go]");
  if (!g) return;
  e.preventDefault();
  const btn = document.querySelector('#toptabs .tab[data-tab="' + g.dataset.go + '"]');
  if (btn) { btn.click(); window.scrollTo(0, 0); }
});
loadMeta();
loadBest();
})();
