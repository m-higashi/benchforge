/* 表の問題番号をクリックすると、その問題での「各構成の解答内容」を開く。IIFE 必須（不変条件2）。
 *
 * 使い方（HTML 側だけで完結・関数呼び出しは不要＝ファイル間で関数を共有しない）:
 *   <td class="ansq" data-qid="s001"
 *       data-ans='[{"run":"...","cfg":"base","label":"A"},{"run":"...","cfg":"judge","label":"B"}]'>s001</td>
 *
 * ★なぜ必要か: 成績一覧では解答内容が見られるのに、2構成の比較と合議の内訳では
 *   正誤しか出ておらず、「なぜそう答えたか」を確かめる導線が無かった（2026-08-07 の指摘）。
 */
(function () {
  "use strict";

  const CACHE = new Map();   // "run\0cfg" -> {q_id: {pred, raw_output}}

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  async function detail(run, cfg) {
    const key = run + "\0" + cfg;
    if (!CACHE.has(key)) {
      const url = "/api/benchmark/detail?run_id=" + encodeURIComponent(run)
        + "&config_id=" + encodeURIComponent(cfg);
      const d = await fetch(url).then(r => r.json());
      const rows = Array.isArray(d) ? d : (d.rows || []);
      const m = {};
      rows.forEach(r => { m[r.q_id] = r; });
      CACHE.set(key, m);
    }
    return CACHE.get(key);
  }

  function close(tr) {
    const nx = tr.nextElementSibling;
    if (nx && nx.classList.contains("ansrow")) { nx.remove(); return true; }
    return false;
  }

  document.addEventListener("click", async e => {
    const cell = e.target.closest("[data-ans]");
    if (!cell) return;
    const tr = cell.closest("tr");
    if (!tr || close(tr)) return;

    let specs;
    try { specs = JSON.parse(cell.getAttribute("data-ans")); } catch (err) { return; }
    const qid = cell.getAttribute("data-qid") || (cell.textContent || "").trim();

    const row = document.createElement("tr");
    row.className = "ansrow";
    const td = document.createElement("td");
    td.colSpan = tr.children.length;
    td.innerHTML = '<div class="note">解答内容を読み込んでいます…</div>';
    row.appendChild(td);
    tr.after(row);

    try {
      const parts = await Promise.all(specs.map(async s => {
        const r = (await detail(s.run, s.cfg))[qid];
        const txt = r && r.raw_output ? r.raw_output : "";
        const pred = r && r.pred ? [].concat(r.pred).join(",") : "—";
        return `<div class="ansblk"><b>${esc(s.label || s.cfg)}</b>（${esc(s.cfg)}）：解答 <b>${esc(pred)}</b>`
          // ★「記録されていません」だけでは、なぜ出ないのか・どうすれば見られるのかが分からない。
          //   同梱サンプルの成績は数値だけで解答文を持たないため、初めての人が自然にやる操作
          //   （同梱の構成を選んでクリック）では必ず空になり「壊れている」と誤解される。
          //   Win/Mac の実機検証が独立に同じ指摘をした（2026-08-10 / 08-14）。
          + (txt ? `<pre class="preview">${esc(txt)}</pre>`
                 : '<span class="muted"> — 解答内容はありません。'
                   + '<b>同梱のサンプル成績には解答文が含まれていません</b>'
                   + '（数値だけを収めています）。「まとめて実行」でこの構成を一度実行すると、'
                   + 'それ以降は解答文が読めます。 '
                   + '<a href="#nightly" data-go="nightly">まとめて実行をひらく</a></span>')
          + "</div>";
      }));
      td.innerHTML = parts.join("");
    } catch (err) {
      td.innerHTML = '<div class="note ng">解答内容を読み込めませんでした。'
        + '起動用のウィンドウが閉じていないか確認してください。</div>';
    }
  });
})();
