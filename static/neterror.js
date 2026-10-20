/* 通信失敗を利用者に必ず伝える。IIFE 必須（同一グローバル共有のため・CLAUDE.md 不変条件2）。
 *
 * ★なぜ中央で受けるか（bug-hunting-playbook §3-4・§4-5）:
 *   画面の各所は `busy("計算中…")` のあと `await fetch(...)` を無防備に呼んでいる（42箇所）。
 *   通信が切れると例外で処理が中断し、**「計算中…」の表示が永久に残る**。
 *   利用者から見ると「固まった」だけで、失敗したことも原因も分からない。
 *   42箇所を書き換えるより、**捕まえ損ねた失敗をここで受ける**方が回帰の危険が小さい。
 *
 * ★エラー文言は原因を名指しする（「通信エラー」では利用者は動けない）。
 */
(function () {
  "use strict";

  function banner(msg) {
    let el = document.getElementById("net-error");
    if (!el) {
      el = document.createElement("div");
      el.id = "net-error";
      el.className = "net-error";
      document.body.appendChild(el);
    }
    el.innerHTML = msg + ' <button type="button" class="ghost" id="net-error-x">閉じる</button>';
    el.style.display = "block";
    // ★帯は position:fixed なので、そのままだと画面最上部のタイトルに重なって隠す
    //   （Windows 実機検証 2026-08-10）。帯の高さぶん本文を押し下げる。
    document.body.style.paddingTop = el.offsetHeight + "px";
  }

  function hide() {
    const el = document.getElementById("net-error");
    if (el) el.style.display = "none";
    document.body.style.paddingTop = "";
  }

  // 「…」で終わったまま止まっている状態表示を、失敗と分かる文言に置き換える
  function clearBusy() {
    document.querySelectorAll('[id$="-status"], .status').forEach((n) => {
      const t = (n.textContent || "").trim();
      if (t.endsWith("…") || t.endsWith("...")) n.textContent = "失敗しました";
    });
  }

  function show(reason) {
    const r = String(reason || "");
    // 原因を名指しする。ここで出る失敗のほとんどは「サーバが落ちている／閉じた」
    const msg = /Failed to fetch|NetworkError|Load failed/i.test(r)
      ? "<b>アプリに接続できませんでした。</b>"
        + "起動用のウィンドウ（start）が閉じていないか確認し、開いていなければもう一度起動してください。"
      : "<b>処理に失敗しました。</b>" + (r ? " 詳細: " + r.slice(0, 200) : "");
    clearBusy();
    banner(msg);
  }

  // ★自前で try/catch している画面から明示的に呼べるようにする（2026-08-14）。
  //   握りつぶしている箇所（監視・有意差・構成の提案・特性評価）では小さな「取得失敗」しか
  //   出ず、**どのタブを触ったかで原因が分かったり分からなかったりする**状態だった。
  window.BF_NETFAIL = show;

  window.addEventListener("unhandledrejection", (e) => show(e.reason && (e.reason.message || e.reason)));
  window.addEventListener("error", (e) => {
    // 読み込み失敗（スクリプトや画像）は握りつぶさず知らせる
    if (e && e.target && e.target.tagName === "SCRIPT") show("画面の部品を読み込めませんでした");
  }, true);

  document.addEventListener("click", (e) => {
    if (e.target.closest("#net-error-x")) hide();
  });
})();
