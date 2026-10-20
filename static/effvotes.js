/* 実効独立票 n_eff（effective sample size）タブ。IIFE 必須（同一グローバル共有のため・CLAUDE.md 不変条件2）。 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = (x) => (100 * x).toFixed(1) + "%";
  let loaded = false;

  // 相関の強さ→背景色（低い=良い=青 / 高い=悪い=赤）
  function heat(v) {
    const t = Math.max(0, Math.min(1, v));
    const r = Math.round(60 + 195 * t), b = Math.round(255 - 175 * t);
    return `background:rgba(${r},${Math.round(120 - 60 * t)},${b},.30)`;
  }

  let CONFIGS = [];
  const SELECTED = new Set();

  async function loadConfigs() {
    const d = await fetch("/api/characterize/configs").then((r) => r.json());
    CONFIGS = d.configs || [];
    renderChips();   // 既定は未選択（自動で選ばない・自動で計算しない）
  }

  // 特性評価タブと同じチップ形式（.chgrid）で揃える
  function renderChips() {
    $("ev-configs").innerHTML = CONFIGS.map((c) =>
      `<label class="${SELECTED.has(c.config_id) ? "on" : ""}" data-cfg="${esc(c.config_id)}">`
      + `<input type="checkbox"${SELECTED.has(c.config_id) ? " checked" : ""}>`
      + `${esc(c.config_id)} <span class="acc">${pct(c.acc)}</span>`
      + `<span class="ev-n">${c.n}問</span></label>`).join("");
    $("ev-configs").querySelectorAll("label").forEach((lab) => {
      lab.onclick = (e) => {
        if (e.target.tagName !== "INPUT") {
          const cb = lab.querySelector("input"); cb.checked = !cb.checked;
        }
        const c = lab.dataset.cfg;
        if (lab.querySelector("input").checked) SELECTED.add(c); else SELECTED.delete(c);
        lab.classList.toggle("on", SELECTED.has(c));
        updateCount();
      };
    });
    updateCount();
  }

  function updateCount() {
    const n = SELECTED.size;
    const sel = CONFIGS.filter((c) => SELECTED.has(c.config_id));
    const ns = [...new Set(sel.map((c) => c.n))];
    let msg = n ? `${n} 構成を選択中` : "未選択";
    // ★「共通して採点された問題のみ」で計算するため、問題集合がばらばらだと
    //   実際に使われる問題が激減する。選ぶ時点で気づけるようにする。
    if (sel.length >= 2) {
      const qs = [...new Set(sel.map((c) => c.qset))];
      msg += qs.length === 1
        ? `（すべて同じ ${ns[0]} 問・問題集合が一致 ✓）`
        : `（⚠ 問題数がばらばら: ${ns.sort((a, b) => b - a).join(" / ")} 問。`
          + `共通部分だけで計算されるため、実際の問題数は最小の ${Math.min(...ns)} 問以下になります）`;
    }
    $("ev-count").textContent = msg;
    $("ev-count").style.color = (sel.length >= 2 && ns.length > 1) ? "var(--warn,#e0a050)" : "";
    $("ev-run").disabled = n < 2;
  }

  function selected() { return Array.from(SELECTED); }

  function render(d) {
    if (d.error) { $("ev-out").innerHTML = `<div class="empty">${esc(d.error)}</div>`; return; }
    const ci = d.ci || {};
    const f2 = (a) => a ? ` <span class="note">95%CI ${a[0].toFixed(2)}–${a[1].toFixed(2)}</span>` : "";
    const f3 = (a) => a ? ` <span class="note">95%CI ${a[0].toFixed(3)}–${a[1].toFixed(3)}</span>` : "";

    let h = `<div class="ev-cards">
      <div class="ev-card"><div class="ev-big">${d.n_eff.toFixed(2)}</div>
        <div>実効独立票${f2(ci.n_eff)}</div>
        <div class="note">名目 ${d.k} 票 → 実質 ${(100 * d.n_eff / d.k).toFixed(0)}%</div></div>
      <div class="ev-card"><div class="ev-big">${d.phi_bar.toFixed(3)}</div>
        <div>平均の誤り相関 φ̄（phi coefficient）${f3(ci.phi_bar)}</div>
        <div class="note">0＝独立 / 1＝完全に同じ間違い方</div></div>
      <div class="ev-card"><div class="ev-big">${d.ceiling ? d.ceiling.toFixed(2) : "—"}</div>
        <div>この先の上限${f2(ci.ceiling)}</div>
        <div class="note">構成を無限に増やしても超えられない票数</div></div>
      <div class="ev-card"><div class="ev-big">${pct(d.double_fault_rate)}</div>
        <div>全員そろって誤る問題</div>
        <div class="note">${d.double_fault}/${d.n_questions}問・合議では救えない</div></div>
    </div>`;

    h += `<p class="note">対象 ${d.k} 構成・共通して採点された ${d.n_questions} 問で計算。`
      + `いずれかが正解できた問題（オラクル）は ${pct(d.oracle_rate)}（${d.oracle}/${d.n_questions}）。`
      + (d.dropped && d.dropped.length ? ` 全問正解／全問不正解のため除外: ${d.dropped.map(esc).join(", ")}。` : "")
      + `</p>`;

    // 相関行列（ヒートマップ）
    const cs = d.configs;
    const pm = {};
    d.pairs.forEach((p) => { pm[p.a + "\0" + p.b] = p.phi; pm[p.b + "\0" + p.a] = p.phi; });
    h += `<h3>構成どうしの「間違え方の似ぐあい」</h3>
      <p class="note">赤いほど<b>同じ問題で一緒に間違えている</b>＝票が重複しており、組み合わせても情報が増えません。
      青いほど互いに違う間違い方をしており、組み合わせる価値があります。</p>
      <div class="ev-scroll"><table class="tbl ev-mat"><tr><th></th>`
      + cs.map((c) => `<th class="ev-rot">${esc(c)}</th>`).join("") + `</tr>`;
    cs.forEach((a) => {
      h += `<tr><th class="ev-rowh">${esc(a)}</th>`;
      cs.forEach((b) => {
        if (a === b) { h += `<td class="ev-diag">—</td>`; return; }
        const v = pm[a + "\0" + b];
        h += v == null ? `<td>—</td>` : `<td style="${heat(v)}">${v.toFixed(2)}</td>`;
      });
      h += `</tr>`;
    });
    h += `</table></div>`;

    const lo = d.pairs.slice(-3).reverse(), hi = d.pairs.slice(0, 3);
    h += `<p class="note">最も重複している組: `
      + hi.map((p) => `${esc(p.a)} × ${esc(p.b)}（${p.phi.toFixed(2)}）`).join(" ／ ")
      + `<br>最も補い合っている組: `
      + lo.map((p) => `${esc(p.a)} × ${esc(p.b)}（${p.phi.toFixed(2)}）`).join(" ／ ") + `</p>`;

    // 飽和カーブ
    if (d.curve && d.curve.length) {
      const maxv = Math.max(...d.curve.map((c) => c.n_eff), d.n_eff);
      h += `<h3>構成を増やしたときの伸びかた</h3>
        <p class="note">実効独立票が最も増える順に構成を足していったときの推移です。
        途中で頭打ちになり、そこから先は<b>足すほど下がる</b>ことがあります
        （新しく足した構成が既存と同じ間違い方をするため）。</p>
        <table class="tbl"><tr><th>構成数</th><th>実効独立票</th><th></th><th>追加した構成</th></tr>`;
      let peak = 0, peakK = 0;
      d.curve.forEach((c) => { if (c.n_eff > peak) { peak = c.n_eff; peakK = c.k; } });
      d.curve.forEach((c) => {
        const w = Math.round(100 * c.n_eff / maxv);
        h += `<tr><td>${c.k}</td><td>${c.n_eff.toFixed(2)}${c.k === peakK ? " ★最大" : ""}</td>`
          + `<td style="width:40%"><div class="ev-bar" style="width:${w}%"></div></td>`
          + `<td class="note">${esc(c.added)}</td></tr>`;
      });
      h += `</table>`;
      if (peakK && peakK < d.k) {
        h += `<p class="note">★ <b>${peakK} 構成で最大（${peak.toFixed(2)}）</b>となり、それ以降は増やしても実効独立票が減っています。
          この母集団では「構成を増やす」戦略はここで頭打ちです。</p>`;
      }
    }

    // 前提の点検
    h += `<h3>この数値を読むときの注意</h3><ul class="note ev-caveat">
      <li>計算式は「どのペアも同じくらい相関している」と仮定しますが、実際は
        ${d.phi_quartiles[0].toFixed(2)}〜${d.phi_quartiles[2].toFixed(2)}（中央 ${d.phi_quartiles[1].toFixed(2)}）と幅があります。平均で代表させた近似値です。</li>
      <li>互いを補い合う（相関が負の）組は ${d.n_negative_pairs}/${d.n_pairs} 組です。
        ${d.n_negative_pairs === 0 ? "<b>1組もありません</b>＝どの2つを取っても一緒に間違える傾向があります。" : ""}</li>
      <li>正解・不正解の2値だけを見ており、「同じ誤答を選んだ」か「別々の誤答を選んだ」かは区別していません。
        多数決では前者のほうが有害なため、この指標は害をやや小さく見積もることがあります。</li>
      <li>伸びかたの表は、構成を足す順序を同じデータ上で選んでいるため、やや良く出る傾向があります。</li>
    </ul>`;
    $("ev-out").innerHTML = h;
  }

  async function run() {
    const cs = selected();
    if (cs.length < 2) { $("ev-out").innerHTML = `<div class="empty">構成を2つ以上選んでください。</div>`; return; }
    $("ev-out").innerHTML = `<div class="empty">計算中…（信頼区間の算出に数秒かかります）</div>`;
    try {
      const r = await fetch(`/api/effective_votes?configs=${encodeURIComponent(cs.join(","))}`);
      render(await r.json());
    } catch (e) {
      $("ev-out").innerHTML = `<div class="empty">取得に失敗しました: ${esc(e)}</div>`;
    }
  }

  const RESET = `<div class="empty">構成を2つ以上選び、「計算する」を押してください。</div>`;

  document.addEventListener("click", (e) => {
    if (e.target.closest("#ev-run")) { run(); return; }
    if (e.target.closest("#ev-clear")) {
      SELECTED.clear(); renderChips(); $("ev-out").innerHTML = RESET; return;
    }
    if (e.target.closest("#ev-same")) {
      // ★選んだ1つと「まったく同じ問題集合」の構成をすべて選ぶ（2026-08-25 追加）。
      //   n_eff は共通問題だけで計算するので、集合が揃っていないと実際の問題数が激減する。
      const sel1 = [...SELECTED];
      if (sel1.length !== 1) {
        $("ev-count").textContent = "先に構成を1つだけ選んでから押してください（現在 "
          + sel1.length + " 構成を選択中）";
        $("ev-count").style.color = "var(--warn,#e0a050)";
        return;
      }
      const base1 = CONFIGS.find((c) => c.config_id === sel1[0]);
      if (!base1) return;
      SELECTED.clear();
      CONFIGS.filter((c) => c.qset === base1.qset).forEach((c) => SELECTED.add(c.config_id));
      renderChips(); $("ev-out").innerHTML = RESET; return;
    }
    if (e.target.closest("#ev-top")) {   // 上位4構成をまとめて選ぶ補助（計算は押すまで走らない）
      // ★問題集合を揃えてから上位を採る。正答率だけで選ぶと n=3 のような断片が紛れ、
      //   「共通して採点された問題」がほぼ残らず計算が成立しない。
      SELECTED.clear();
      const mx = CONFIGS.reduce((m, c) => Math.max(m, c.n || 0), 0);
      CONFIGS.filter((c) => c.n === mx).slice(0, 4).forEach((c) => SELECTED.add(c.config_id));
      renderChips(); $("ev-out").innerHTML = RESET; return;
    }
    const t = e.target.closest('[data-tab="effvotes"]');
    if (!t || loaded) return;
    loaded = true;
    loadConfigs();   // 構成一覧を出すだけ。計算は「計算する」を押してから。
  });
})();
