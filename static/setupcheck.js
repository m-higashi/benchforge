/* 環境チェック（はじめて動かす人の最初の一歩）。IIFE 必須（同一グローバル共有のため・CLAUDE.md 不変条件2）。 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // コマンドは「押すだけでコピーできる」ようにする。
  // ★未経験者は打ち間違いで詰まる。手入力させない。
  function cmdBox(cmd) {
    if (!cmd) return "";
    return `<div class="sk-cmd"><code>${esc(cmd)}</code>`
      + `<button class="ghost sk-copy" data-cmd="${esc(cmd)}">コピー</button></div>`;
  }

  function render(d) {
    const head = d.ready
      ? `<div class="sk-ok">✓ 準備ができています。${esc(d.next)}</div>`
      : `<div class="sk-ng">まだ足りないものがあります。${esc(d.next)}</div>`;
    const steps = (d.steps || []).map((s) =>
      `<div class="sk-step ${s.ok ? "on" : "off"}">`
      + `<div class="sk-mark">${s.ok ? "✓" : "×"}</div>`
      + `<div class="sk-body"><b>${esc(s.title)}</b>`
      + `<div class="note">${esc(s.detail)}</div>${cmdBox(s.cmd)}</div></div>`).join("");

    // メモリに合わせて「置けるモデル」だけを出す。
    // ★8GB の人に 31B を勧めない。動くけれど極端に遅くなり「使えない」と誤解される。
    const rec = (d.recommend || []).map((r) =>
      `<tr><td>${r.vram} GB〜</td><td><code>${esc(r.tag)}</code></td>`
      + `<td class="num">${r.size_gb} GB</td><td class="muted">${esc(r.note)}</td>`
      + `<td>${cmdBox("ollama pull " + r.tag)}</td></tr>`).join("");
    const mem = d.memory && d.memory.gb
      ? `このPCで使えるメモリは <b>${d.memory.gb} GB</b>（${esc(d.memory.kind)}）でした。`
      : "メモリを判定できませんでした。小さいものから試してください。";
    // ★迷わせない。「まずこれ1つ」を表より前に、コピーできる形で出す。
    //   （表だけだと、どの行を選べばよいか分からないまま最大のものを入れてしまう）
    const starter = d.starter
      ? `<p class="note"><b>迷ったら、まずこれを1つ入れてください：</b> <code>${esc(d.starter)}</code>`
        + `（一覧の中で最小です）</p>${cmdBox("ollama pull " + d.starter)}`
      : "";

    $("sk-out").innerHTML = head + `<div class="sk-steps">${steps}</div>`
      + `<h4 style="margin:1em 0 .3em">入れるモデルの目安</h4>`
      + `<p class="note">${mem} 数字は4bit量子化での目安です。`
      + `<b>モデルがメモリを超えると、動きますが極端に遅くなります。</b></p>`
      + starter
      + `<table class="sk-rec"><thead><tr><th>必要メモリ</th><th>モデル</th><th class="num">大きさ</th>`
      + `<th>用途</th><th>入れ方</th></tr></thead><tbody>${rec}</tbody></table>`
      + `<p class="note">画像つきの問題を解かせるには<b>画像を読めるモデル</b>が必要です（上の一覧はすべて対応）。`
      + `テキスト専用のモデルを選ぶと<b>画像が無視され、成績だけが低く出ます</b>。</p>`;
  }

  async function run() {
    $("sk-out").innerHTML = '<div class="empty">確認しています…</div>';
    try {
      render(await fetch("/api/setup_check").then((r) => r.json()));
    } catch (e) {
      $("sk-out").innerHTML = `<div class="empty">確認できませんでした: ${esc(e)}</div>`;
    }
  }

  document.addEventListener("click", (e) => {
    const c = e.target.closest(".sk-copy");
    if (c) {
      navigator.clipboard.writeText(c.dataset.cmd).then(() => {
        const t = c.textContent; c.textContent = "コピーしました";
        setTimeout(() => { c.textContent = t; }, 1200);
      });
      return;
    }
    if (e.target.closest("#sk-run")) run();
  });

  // 概要タブは最初から表示されているので、開いた時点で1回だけ自動で確認する。
  // ★「押さないと分からない」より「開いたら出ている」ほうが、初めての人には親切。
  document.addEventListener("DOMContentLoaded", () => { if ($("sk-out")) run(); });
})();
