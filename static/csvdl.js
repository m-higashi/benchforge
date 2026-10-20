/* 画面に出ている表を CSV で保存する共通処理。IIFE 必須（不変条件2）。
 *
 * ★なぜ1か所に集約するか（bug-hunting-playbook §3-3）:
 *   同じ「表→CSV」を各タブに書くと、片方だけ直る事故が確定する。
 *   使い方は HTML 側に <button data-csv-from="出力先のid" data-csv-name="ファイル名"> を置くだけ。
 *
 * 表(<table>)に加えて、チップ(.chip)で並べた問題番号の一覧も拾う
 * （難問コアの問題一覧が <table> ではなくチップだったため、CSV に出せなかった）。
 */
(function () {
  "use strict";

  function csvCell(v) {
    const s = String(v == null ? "" : v).replace(/"/g, '""');
    return /[",\n]/.test(s) ? `"${s}"` : s;
  }

  function tableToCsv(tb) {
    let out = "";
    tb.querySelectorAll("tr").forEach(tr => {
      out += [...tr.querySelectorAll("th,td")]
        .map(c => csvCell((c.textContent || "").trim())).join(",") + "\n";
    });
    return out;
  }

  function collect(root) {
    let csv = "";
    // 見出し＋表
    root.querySelectorAll("h3, table").forEach(node => {
      if (node.tagName === "H3") csv += (csv ? "\n" : "") + csvCell(node.textContent.trim()) + "\n";
      else csv += tableToCsv(node);
    });
    // チップで並んだ問題番号（クリックで詳細が出る一覧）
    const chips = [...root.querySelectorAll(".chip[data-qid]")].map(c => c.getAttribute("data-qid"));
    if (chips.length) {
      csv += (csv ? "\n" : "") + "問題番号\n" + chips.map(csvCell).join("\n") + "\n";
    }
    return csv;
  }

  function save(csv, name) {
    // 先頭の BOM は Excel が UTF-8 と認識するために必要（無いと日本語が化ける）
    const blob = new Blob(["﻿" + csv], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (name || "benchforge") + ".csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  document.addEventListener("click", e => {
    const btn = e.target.closest("[data-csv-from]");
    if (!btn) return;
    e.preventDefault();
    const root = document.getElementById(btn.getAttribute("data-csv-from"));
    const say = m => {
      const s = btn.parentElement && btn.parentElement.querySelector(".status");
      if (s) s.textContent = m; else btn.title = m;
    };
    if (!root) { say("保存できる表がありません"); return; }
    const csv = collect(root);
    if (!csv.trim()) { say("まだ表が出ていません。先に計算してください"); return; }
    save(csv, btn.getAttribute("data-csv-name") || "benchforge");
    say("CSVを保存しました");
  });
})();
