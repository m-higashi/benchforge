/* 選抜バイアス（勝者の呪い）診断タブ。IIFE 必須（CLAUDE.md 不変条件2）。 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = (x) => (100 * x).toFixed(0) + "%";
  let loaded = false, CONFIGS = [];
  const SELECTED = new Set();
  const RESET = `<div class="empty">構成を3つ以上選び、「診断する」を押してください。</div>`;

  async function loadConfigs() {
    const d = await fetch("/api/characterize/configs?since=").then((r) => r.json());
    CONFIGS = d.configs || [];
    renderChips();
    $("sb-base").innerHTML = `<option value="">（最下位を自動で基準にする）</option>`
      + CONFIGS.map((c) => `<option value="${esc(c.config_id)}">${esc(c.config_id)}</option>`).join("");
  }

  // ★問題数(n)は必ず一緒に見せる。正答率だけ見せると n=3 の断片が上位に並び、
  //   利用者が「一番良い構成」と誤解して選んでしまう（実際にそうなっていた）。
  function maxN() { return CONFIGS.reduce((m, c) => Math.max(m, c.n || 0), 0); }

  function renderChips() {
    const mx = maxN();
    $("sb-configs").innerHTML = CONFIGS.map((c) => {
      const weak = (c.n || 0) < mx / 2;                 // 主要な問題集合の半分未満＝比較に向かない
      const t = weak ? ` title="この構成は ${c.n} 問しか解いていません（最大 ${mx} 問）。`
                       + `解いた問題が違う構成を混ぜると診断が成立しません"` : "";
      return `<label class="${SELECTED.has(c.config_id) ? "on" : ""}${weak ? " weak" : ""}"`
        + ` data-cfg="${esc(c.config_id)}" data-n="${c.n}"${t}>`
        + `<input type="checkbox"${SELECTED.has(c.config_id) ? " checked" : ""}>`
        + `${esc(c.config_id)} <span class="acc">${(100 * c.acc).toFixed(1)}%</span>`
        + `<span class="ev-n">${c.n}問</span></label>`;
    }).join("");
    $("sb-configs").querySelectorAll("label").forEach((lab) => {
      lab.onclick = (e) => {
        if (e.target.tagName !== "INPUT") {
          const cb = lab.querySelector("input"); cb.checked = !cb.checked;
        }
        const c = lab.dataset.cfg;
        if (lab.querySelector("input").checked) SELECTED.add(c); else SELECTED.delete(c);
        lab.classList.toggle("on", SELECTED.has(c));
        count();
      };
    });
    count();
  }
  function count() {
    const sel = CONFIGS.filter((c) => SELECTED.has(c.config_id));
    const ns = [...new Set(sel.map((c) => c.n))];
    let msg = SELECTED.size ? `${SELECTED.size} 構成を選択中` : "未選択";
    // ★選んだ構成が「同じ問題を解いているか」をその場で知らせる（診断の前提条件）
    if (sel.length >= 2) {
      // ★問題数(n)ではなく問題集合の署名(qset)で判定する。n が同じでも中身が違えば比較は成立しない。
      const qs = [...new Set(sel.map((c) => c.qset))];
      msg += qs.length === 1
        ? `（すべて同じ ${ns[0]} 問・問題集合が一致 ✓）`
        : (ns.length === 1
            ? `（⚠ 問題数は同じ ${ns[0]} 問ですが、解いている問題が違います）`
            : `（⚠ 問題集合がばらばら: ${ns.sort((a, b) => b - a).join(" / ")} 問）`);
    }
    $("sb-count").textContent = msg;
    $("sb-count").style.color = (sel.length >= 2 && [...new Set(sel.map((c) => c.qset))].length > 1)
      ? "var(--warn,#e0a050)" : "";
    $("sb-run").disabled = SELECTED.size < 3;
  }

  function render(d) {
    if (d.error) { $("sb-out").innerHTML = `<div class="empty">${esc(d.error)}</div>`; return; }
    const corr = d.sims.find((s) => s.label === "相関を考慮") || d.sims[0];
    const indep = d.sims.find((s) => s.label === "独立と仮定") || d.sims[0];
    // 判定: 実測最大が「偶然でも届く範囲」に収まっているか
    const explained = d.best_acc <= corr.p95;
    const verdict = explained
      ? { cls: "sb-warn", head: "この最良値は「たくさん試したから」で説明できてしまいます",
          body: `実測の最高 <b>${d.best_acc.toFixed(1)}%</b> は、<b>全構成の実力が同じ</b>と仮定した場合でも
                 偶然到達しうる範囲（上位5%点 ${corr.p95.toFixed(1)}%）に収まっています。` }
      : { cls: "sb-ok", head: "この最良値は偶然だけでは説明しきれません",
          body: `実測の最高 <b>${d.best_acc.toFixed(1)}%</b> は、全構成の実力が同じと仮定したときの
                 上位5%点（${corr.p95.toFixed(1)}%）を超えています。ただし下の多重比較の結果も併せて判断してください。` };

    let h = `<div class="sb-verdict ${verdict.cls}"><b>${verdict.head}</b><br>${verdict.body}</div>`;

    h += `<h3>1. 「たくさん試すと最高値は勝手に上がる」量</h3>
      <p class="note">選んだ <b>${d.k}</b> 構成すべてが<b>同じ実力（選んだ構成の平均 ${d.null_acc.toFixed(1)}%）である</b>と仮定し、
      成績のばらつきだけで最高値がどこまで届くかを計算しました。
      構成同士は似た間違い方をする（今回の平均相関 ${d.phi_bar.toFixed(2)}）ため、
      見かけの ${d.k} 構成は<b>実質 ${d.k_eff.toFixed(1)} 構成分</b>の情報しかありません。両方の見積りで挟んで判断します。</p>
      <div class="tbl-wrap"><table class="tbl sbgrid"><colgroup><col class="c1"><col class="c2"><col class="c3"><col class="c4"><col class="c5"></colgroup><tr><th>前提</th><th class="num">最高値の期待</th><th class="num">上位5%点</th><th class="barcol"></th><th class="num">実測最高(${d.best_acc.toFixed(1)}%)以上になる確率</th></tr>`;
    [indep, corr].forEach((s) => {
      h += `<tr><td>${esc(s.label)}（${s.k.toFixed(1)} 構成分）</td><td class="num">${s.mean.toFixed(1)}%</td>`
        + `<td class="num">${s.p95.toFixed(1)}%</td><td class="barcol"></td><td class="num"><b>${pct(s.exceed)}</b></td></tr>`;
    });
    h += `</table></div>
      <p class="note">※ 相関を考慮した側が下限、独立と仮定した側が上限の目安です。真の値はこの間にあります。</p>`;

    h += `<h3>2. 「最良」は問題を入れ替えても最良か</h3>
      <p class="note">採点した問題を無作為に選び直す操作（bootstrap resampling）を繰り返し、そのたびに順位を付け直しました。
      <b>本当に優れているなら何度やっても上位に来る</b>はずです。</p>
      <div class="tbl-wrap"><table class="tbl sbgrid"><colgroup><col class="c1"><col class="c2"><col class="c3"><col class="c4"><col class="c5"></colgroup><tr><th>構成</th><th class="num">正答率</th><th class="num">最良になった割合</th><th class="barcol"></th><th class="num">順位の95%範囲</th></tr>`;
    d.stability.slice(0, 10).forEach((s) => {
      const w = Math.round(100 * s.win_rate);
      h += `<tr><td>${esc(s.config)}</td><td class="num">${s.acc.toFixed(1)}%</td>`
        + `<td class="num"><b>${w}%</b></td><td class="barcol"><div class="sb-bar" style="width:${w}%"></div></td>`
        + `<td class="num">${s.rank_lo}–${s.rank_hi} 位</td></tr>`;
    });
    h += `</table></div>`;
    const top = d.stability[0];
    if (top && top.win_rate < 0.5) {
      h += `<p class="note">★ 最高成績の <code>${esc(top.config)}</code> でさえ、最良になるのは
        <b>${pct(top.win_rate)}</b> にとどまり、順位は <b>${top.rank_lo}〜${top.rank_hi}位</b>まで動きます。
        「1位」という順位そのものが問題の選び方に左右されている状態です。</p>`;
    }

    h += `<h3>3. 基準との差は統計的に意味があるか（多重比較の補正）</h3>
      <p class="note">多数の構成を比べると、偶然「差がある」ように見えるものが必ず出ます。
      その分を差し引く補正（Holm–Bonferroni method）を行いました。臨床研究で複数の項目を比較するときと同じ考え方です。
      基準は <code>${esc(d.baseline)}</code>（${d.baseline_acc.toFixed(1)}%）です（上の欄で変更できます）。</p>
      <div class="tbl-wrap"><table class="tbl sbgrid"><colgroup><col class="c1"><col class="c2"><col class="c3"><col class="c4"><col class="c5"></colgroup><tr><th>構成</th><th class="num">基準より正解</th><th class="num">基準より不正解</th><th class="num">補正前 p</th><th class="num">補正後 p</th></tr>`;
    d.holm.slice(0, 10).forEach((r) => {
      h += `<tr><td>${esc(r.config)}</td><td class="num">${r.rescued}</td><td class="num">${r.broken}</td>`
        + `<td class="num">${r.p.toFixed(3)}</td><td class="num">${r.holm.toFixed(3)}`
        + (r.sig ? ` <span class="pill ok">有意</span>` : "") + `</td></tr>`;
    });
    h += `</table></div>`;

    h += `<h3>報告するときの注意</h3><ul class="note sb-caveat">
      <li>最高成績の構成を報告する場合は、<b>「多数を試した中の最高値であり、上振れを含む推定値である」</b>と明記してください。</li>
      <li>補正後 p が 0.05 を超えるものは、<b>「最も高かった」とは書けても「有意に優れている」とは書けません</b>。</li>
      <li>選抜と性能の推定に<b>同じ問題群を使うと必ず上振れ</b>します。可能なら、構成を選ぶ用と最終評価用でデータを分けてください
        （出典: <a href="https://arxiv.org/abs/2605.05973" target="_blank" rel="noopener">arXiv:2605.05973</a>）。</li>
    </ul>`;
    $("sb-out").innerHTML = h;
  }

  async function run() {
    const cs = Array.from(SELECTED);
    if (cs.length < 3) return;
    $("sb-out").innerHTML = `<div class="empty">診断中…（数秒かかります）</div>`;
    try {
      const q = `configs=${encodeURIComponent(cs.join(","))}&baseline=${encodeURIComponent($("sb-base").value || "")}&since=`;
      render(await fetch(`/api/selection_bias?${q}`).then((r) => r.json()));
    } catch (e) {
      $("sb-out").innerHTML = `<div class="empty">取得に失敗しました: ${esc(e)}</div>`;
    }
  }

  document.addEventListener("click", (e) => {
    // 「成績一覧」の警告からの導線: 本物のタブボタンを押してペインを切り替える
    if (e.target.closest(".sb-link")) {
      e.preventDefault();
      const btn = document.querySelector('#toptabs .tab[data-tab="selbias"]');
      if (btn) btn.click();
      return;
    }
    if (e.target.closest("#sb-run")) { run(); return; }
    if (e.target.closest("#sb-clear")) { SELECTED.clear(); renderChips(); $("sb-out").innerHTML = RESET; return; }
    if (e.target.closest("#sb-same")) {
      // ★選んだ1つと「まったく同じ問題集合」の構成をすべて選ぶ（2026-08-25 追加）。
      //   qset は解いた q_id 群の署名。n が同じでも中身が違えば選ばない。
      const sel1 = [...SELECTED];
      if (sel1.length !== 1) {
        $("sb-count").textContent = "先に構成を1つだけ選んでから押してください（現在 "
          + sel1.length + " 構成を選択中）";
        $("sb-count").style.color = "var(--warn,#e0a050)";
        return;
      }
      const base1 = CONFIGS.find((c) => c.config_id === sel1[0]);
      if (!base1) return;
      SELECTED.clear();
      CONFIGS.filter((c) => c.qset === base1.qset).forEach((c) => SELECTED.add(c.config_id));
      renderChips(); $("sb-out").innerHTML = RESET; return;
    }
    if (e.target.closest("#sb-all")) {
      // ★以前は「正答率の上位10構成」を選んでいたが、n=3 のような小さな断片が
      //   上位に紛れて診断が成立しなかった。**主要な問題集合（最大 n）に揃えてから**上位を採る。
      SELECTED.clear();
      const mx = maxN();
      CONFIGS.filter((c) => c.n === mx).slice(0, 10).forEach((c) => SELECTED.add(c.config_id));
      renderChips(); $("sb-out").innerHTML = RESET; return;
    }
    const t = e.target.closest('[data-tab="selbias"]');
    if (!t || loaded) return;
    loaded = true;
    loadConfigs();
  });
})();
