// Benchforge「まとめて実行」: 「構成の作成」で保存した構成を選び、問題セット全体をまとめて解く。
// 構成の組み立てはエディタに集約（このタブでは作らない）。IIFEで閉じる。
//
// ★タイマー（予約実行）は 2026-08-07 に廃止した。OS をまたいで確実に動く予約の実装が無く、
//   作者自身も使っていなかったため。予約が要る場合は OS のスケジューラから起動する。
(function () {
"use strict";

const el = id => document.getElementById(id);
function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
let META = { plans: [], saved: {} };
let metaLoaded = false;

async function loadMeta() {
  META = await fetch("/api/nightly/meta").then(r => r.json());
  metaLoaded = true;
  // 保存済みの構成（プランエディタで作成したもの）をセレクトへ
  const psel = el("nt-plan-select");
  const plans = META.plans || [];
  const keep = psel.value;   // 再取得しても選択を保つ
  psel.innerHTML = '<option value="">（保存済みの構成を選択）</option>'
    + plans.map(p => {
        const nm = (typeof p === "string") ? p : (p && p.name) || "";
        const extra = (p && typeof p === "object") ? `（構成${p.configs ?? "?"}・${esc(p.split || "")}）` : "";
        return `<option value="${esc(nm)}">${esc(nm)}${extra}</option>`;
      }).join("");
  if (keep) psel.value = keep;
  const mt = el("nt-meta"); if (mt) mt.textContent = `保存済みの構成 ${plans.length}件`;
  await loadNtDataToggle();
}


// ── ① データ上書き（データセット＋split。run_trial が実際に使うのは split のみ） ──
function dataOverrides() {
  return {
    split: el("nt-split").value.trim(),
    dataset_dir: el("nt-dataset").value.trim(),   // 外部実行系=空／自前=データセット名
  };
}

// データセット（トグル）＋その split をドロップダウンに読み込む（プランエディタと同方式）
async function loadNtDataToggle() {
  const dsSel = el("nt-dataset"), spSel = el("nt-split");
  if (!dsSel || !spSel) return;
  let opts = "";
  let cur = "";
  try {
    const d = await fetch("/api/datasets").then(r => r.json());
    if (d.external) opts += '<option value="">設定で指定した場所</option>';
    (d.datasets || []).forEach(n => opts +=
      `<option value="${esc(n)}">${esc(n)}${n === "your_data" ? "（あなたのデータ）" : "（サンプル）"}</option>`);
    cur = d.current || "";
  } catch (e) { /* ignore */ }
  dsSel.innerHTML = opts;
  if (cur) dsSel.value = cur;   // 最初から設定中のものを選んでおく
  async function loadSp() {
    const ds = dsSel.value;
    let sp = ds ? '<option value="">（全問＝IN-BOX全体）</option>'
                : '<option value="dev">dev</option><option value="dev_mini">dev_mini</option><option value="test">test</option>';
    try {
      const q = ds ? "?dataset=" + encodeURIComponent(ds) : "";
      const s = await fetch("/api/splits" + q).then(r => r.json());
      Object.keys(s.splits || {}).forEach(n => sp += `<option value="${esc(n)}">${esc(n)}（定義済み ${s.splits[n].length}問）</option>`);
    } catch (e) { /* ignore */ }
    spSel.innerHTML = sp;
  }
  dsSel.onchange = loadSp;
  await loadSp();
}

// ── ② 保存済みプラン（プランエディタ作）を表示／外部実行系に適用 ──
function smsg(m) { const e = el("nt-saved-msg"); if (e) e.textContent = m || ""; }
async function loadSavedPlan() {
  const name = el("nt-plan-select").value;
  if (!name) { smsg("プランを選んでください"); return; }
  const plan = await fetch("/api/plans/" + encodeURIComponent(name)).then(r => r.json());
  if (plan && plan.error) { smsg("失敗: " + plan.error); return; }
  el("nt-plan").textContent = JSON.stringify(plan, null, 2);
  smsg("表示: " + name);
}

// ── ④ ラン制御（runner: 外部実行系の plans/・systemd・PAUSE のみ操作） ──
async function rpost(action, body) {
  return fetch("/api/runner/" + action, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : null,
  }).then(r => r.json());
}
function rmsg(m) { el("nt-rmsg").textContent = m || ""; }
async function refreshStatus() {
  const s = await fetch("/api/runner/status").then(r => r.json()).catch(() => null);
  if (!s) { el("nt-rstatus").textContent = "状態取得失敗"; return; }
  // oneshot 実行中は is-active が "activating" を返すため、両方を実行中扱いにする
  const running = s.service === "active" || s.service === "activating";
  const run = running ? "🟢実行中" : "⚪idle";
  el("nt-rstatus").textContent = run + (s.paused ? "・⏸PAUSE" : "")
 + (s.plan_exists ? "" : "・plan未適用");
}
// ★「適用」と「起動」を1つのボタンにまとめた（2026-08-07）。
//   別々だと「適用しただけで実行された気になる」「適用を忘れて古い構成が走る」事故が起きる。
async function runNow() {
  const name = el("nt-plan-select").value;
  if (!name) { rmsg("先に②で構成を選んでください"); return; }
  const ds = el("nt-dataset").value || "（既定の問題セット）";
  if (!confirm(`構成「${name}」で「${ds}」をまとめて解きます（GPUを使います）。\nよろしいですか？`)) return;
  rmsg("構成を反映しています…");
  const ov = dataOverrides();
  const d = await fetch("/api/runner/apply_saved", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(Object.assign({ name }, ov)),
  }).then(r => r.json());
  if (d.error) { rmsg("失敗: " + d.error); return; }
  el("nt-plan").textContent = JSON.stringify(d.plan, null, 2);
  if (d.warnings && d.warnings.length) rmsg("⚠ " + d.warnings.join(" / "));
  rmsg("実行を開始しています…");
  const r = await rpost("start");
  rmsg((r.msg || "開始しました") + "  → 進行は「実行状況の監視」タブで見られます");
  refreshStatus();
}
async function stopRun() {
  if (!confirm("⚠ 実行中のランを停止します。よろしいですか？")) return;
  rmsg("停止中…"); rmsg((await rpost("stop")).msg); refreshStatus();
}
async function pauseRun() { rmsg((await rpost("pause")).msg); refreshStatus(); }
async function resumeRun() { rmsg((await rpost("resume")).msg); refreshStatus(); }

function init() {
  el("nt-load-plan").onclick = loadSavedPlan;
  el("nt-run").onclick = runNow;
  el("nt-reload-plans").onclick = () => { loadMeta(); rmsg("構成の一覧を取り直しました"); };
  el("nt-reload-data").onclick = () => { loadNtDataToggle(); rmsg("問題セットの一覧を取り直しました"); };
  el("nt-stop").onclick = stopRun;
  el("nt-pause").onclick = pauseRun;
  el("nt-resume").onclick = resumeRun;
  el("nt-refresh").onclick = refreshStatus;
  const tab = document.querySelector('#toptabs .tab[data-tab="nightly"]');
  if (tab) tab.addEventListener("click", () => { if (!metaLoaded) loadMeta(); refreshStatus(); });
  refreshStatus();
}
init();
})();
