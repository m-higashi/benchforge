// Benchforge ラン監視: run_trial.log のライブ表示＋状態（読み取り専用）。IIFEでスコープ閉じる。
(function () {
"use strict";

const el = id => document.getElementById(id);
function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

let timer = null;

async function refresh() {
  let st;
  try {
    st = await fetch("/api/run/status").then(r => r.json());
  } catch (e) {
    // ★中央の通知にも回す。ここで握りつぶすと「取得失敗」の4文字しか出ず、
    //   サーバが落ちていることも対処法も分からない（実機検証 2026-08-10 / 08-14）。
    el("run-badge").textContent = "取得失敗";
    if (window.BF_NETFAIL) window.BF_NETFAIL(e && (e.message || e)); return;
  }

  // 状態バッジ
  const state = st.state || (st.exists ? "不明" : "ログ無し");
  const cls = state === "実行中" ? "ok" : (state === "idle" ? "muted" : "");
  let badge = `<b class="${cls}">${esc(state)}</b>`;
  if (st.age_sec != null) badge += ` <span class="muted">(最終更新 ${Math.round(st.age_sec)}秒前)</span>`;
  const sv = st.services || {};
  // ★内部のサービス名は画面にも**コードにも**置かない（利用者には無関係）。
  //   サーバが返した最初の値をそのまま状態として見せる。
  const svc = sv["service"] || Object.values(sv)[0];
  if (svc) badge += ` ／ 実行サービス: ${esc(svc)}`;
  if (sv["phase2_timer_next"]) badge += ` ／ 次タイマー: ${esc(sv["phase2_timer_next"])}`;
  el("run-badge").innerHTML = badge;

  // ログ末尾
  const n = el("run-lines").value || 80;
  try {
    const d = await fetch("/api/run/log?lines=" + encodeURIComponent(n)).then(r => r.json());
    const log = el("run-log");
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.textContent = d.exists ? (d.lines || []).join("\n") : "(run_trial.log が見つかりません)";
    if (atBottom) log.scrollTop = log.scrollHeight;   // 末尾に貼り付いていれば自動追従
    // ★1KB 未満を切り捨てると「(0 KB)」＝中身が無いように見える（実機検証 2026-08-10）
    if (d.size != null) el("run-meta").textContent =
      d.size < 1024 ? `(${d.size} バイト)` : `(${(d.size / 1024).toFixed(1)} KB)`;
  } catch (e) { el("run-log").textContent = "ログ取得に失敗しました"; }
}

function setAuto(on) {
  if (timer) { clearInterval(timer); timer = null; }
  if (on) timer = setInterval(refresh, 5000);
}

function initRun() {
  el("run-refresh").onclick = refresh;
  el("run-lines").onchange = refresh;
  el("run-auto").onchange = e => setAuto(e.target.checked);
  // ラン監視タブを開いたとき／初回に更新
  const tabBtn = document.querySelector('#toptabs .tab[data-tab="run"]');
  if (tabBtn) tabBtn.addEventListener("click", () => { refresh(); setAuto(el("run-auto").checked); });
  refresh();
  setAuto(true);
}
initRun();
})();
