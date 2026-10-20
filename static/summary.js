// Benchforge P4「まとめ(Forge)」: 結果統計→ollama で Wiki風総論を生成・表示。IIFEでスコープ閉じる。
(function () {
"use strict";

function esc(s) { return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

// 最小 Markdown → HTML（見出し/太字/コード/箇条書き/番号/表/段落/水平線）
function mdToHtml(md) {
  const lines = (md || "").replace(/<!--.*?-->/gs, "").split("\n");
  const out = [];
  let i = 0, inUl = false, inOl = false;
  const closeLists = () => { if (inUl) { out.push("</ul>"); inUl = false; } if (inOl) { out.push("</ol>"); inOl = false; } };
  const inline = t => esc(t)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  while (i < lines.length) {
    let ln = lines[i];
    if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      // テーブル
      closeLists();
      const head = ln.trim().slice(1, -1).split("|").map(c => c.trim());
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(lines[i].trim().slice(1, -1).split("|").map(c => c.trim())); i++;
      }
      out.push('<table class="mdt"><thead><tr>' + head.map(h => `<th>${inline(h)}</th>`).join("") +
        "</tr></thead><tbody>" +
        rows.map(r => "<tr>" + r.map(c => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") +
        "</tbody></table>");
      continue;
    }
    let m;
    if ((m = ln.match(/^(#{1,4})\s+(.*)$/))) { closeLists(); out.push(`<h${m[1].length + 1}>${inline(m[2])}</h${m[1].length + 1}>`); }
    else if (/^\s*[-*]\s+/.test(ln)) { if (!inUl) { closeLists(); out.push("<ul>"); inUl = true; } out.push(`<li>${inline(ln.replace(/^\s*[-*]\s+/, ""))}</li>`); }
    else if (/^\s*\d+\.\s+/.test(ln)) { if (!inOl) { closeLists(); out.push("<ol>"); inOl = true; } out.push(`<li>${inline(ln.replace(/^\s*\d+\.\s+/, ""))}</li>`); }
    else if (/^\s*---+\s*$/.test(ln)) { closeLists(); out.push("<hr>"); }
    else if (ln.trim() === "") { closeLists(); }
    else { closeLists(); out.push(`<p>${inline(ln)}</p>`); }
    i++;
  }
  closeLists();
  return out.join("\n");
}

const el = id => document.getElementById(id);

function fillSaved(list) {
  const sel = el("sm-saved");
  sel.innerHTML = (list && list.length)
    ? list.map(s => `<option value="${esc(s.name)}">${esc(s.period || "?")} ${esc(s.date || "")} (${s.n_configs ?? "?"}構成)</option>`).join("")
    : '<option value="">（蓄積なし）</option>';
}

async function loadSummary() {
  const period = el("sm-period").value;
  const d = await fetch("/api/summary?period=" + encodeURIComponent(period)).then(r => r.json());
  el("sm-md").innerHTML = d.markdown ? mdToHtml(d.markdown) : "（未生成）「この期間で総論を生成」を押してください。";
  el("sm-stats").textContent = d.stats || "";
  el("sm-meta").textContent = `${d.n_configs || 0}構成（${period}）` + (d.updated ? ` ・ 最新生成 ${d.updated}` : "");
  fillSaved(d.saved);
}

async function genSummary() {
  const model = el("sm-model").value, period = el("sm-period").value;
  el("sm-status").textContent = "生成中…（GPU使用・モデル応答待ち）"; el("sm-gen").disabled = true;
  try {
    const r = await fetch("/api/summary/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, period }),
    }).then(x => x.json());
    if (r.error) { el("sm-status").textContent = "エラー: " + r.error; return; }
    el("sm-md").innerHTML = mdToHtml(r.markdown);
    el("sm-stats").textContent = r.stats || "";
    fillSaved(r.list);
    el("sm-status").textContent = `生成完了（${r.model} / ${r.period} / ${r.n_configs}構成）→ 蓄積: ${r.saved}`;
  } catch (e) {
    el("sm-status").textContent = "通信エラー: " + e.message;
  } finally { el("sm-gen").disabled = false; }
}

async function viewSaved() {
  const name = el("sm-saved").value;
  if (!name) { el("sm-status").textContent = "参照するまとめがありません"; return; }
  const d = await fetch("/api/summary/get?name=" + encodeURIComponent(name)).then(r => r.json());
  if (d.markdown) { el("sm-md").innerHTML = mdToHtml(d.markdown); el("sm-status").textContent = "表示: " + name; }
}

function toStrategy() {
  window.BF_PICK_SUMMARY = el("sm-saved").value || "";   // strategy.js が拾って参照に設定
  const tab = document.querySelector('#toptabs .tab[data-tab="strategy"]');
  if (tab) tab.click();
}

async function initSummary() {
  const meta = await fetch("/api/editor/meta").then(r => r.json());
  el("sm-model").innerHTML = (meta.models || [])
    .map(m => `<option value="${m.name}"${m.name.startsWith("gemma4:12b") ? " selected" : ""}>${m.name}</option>`).join("");
  el("sm-gen").onclick = genSummary;
  el("sm-period").onchange = loadSummary;
  el("sm-view").onclick = viewSaved;
  el("sm-to-strategy").onclick = toStrategy;
  loadSummary();
}

initSummary();
})();
