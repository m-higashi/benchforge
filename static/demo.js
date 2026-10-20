// Benchforge P3 モデル実演: ステージ列を Benchforge 自前 ollama で順次実行し、
// ステージごとに思考(message.thinking)と本文を NDJSON ストリームでリアルタイム表示。
// 名前衝突防止: 全体を IIFE で包みファイルスコープに閉じる。
(function () {
"use strict";

let DM_MODELS = [];
let DM_IMAGES = [];      // data-URI base64
let DM_CAPTIONS = "";    // 画像-モダリティ対応ブロック（サンプル由来）
let DM_GOLD = "";        // 正解
let DM_STAGES = null;    // 選んだ構成のステージ列。null=構成未選択
let DM_TEMP = 0;         // 選んだ構成の temperature
let DM_CONFIGS = [];
let DM_ABORT = null;

const DM_LLM_STAGES = ["direct_mc", "vision_findings", "reason"];
const DM_STAGE_DESC = {
  direct_mc: "画像+設問→記号を直接", vision_findings: "画像→所見記述",
  reason: "所見+設問→根拠付き解答",  vote: "解答を多数決",
};

function dmEsc(s) { return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
function el(id) { return document.getElementById(id); }

function currentStages() {
  return (DM_STAGES && DM_STAGES.length) ? DM_STAGES : [];
}

function renderArch() {
  const stages = currentStages();
  const parts = [`<span class="node" data-idx="in">入力<small>画像${DM_IMAGES.length}${DM_CAPTIONS ? " + caption" : ""}</small></span>`];
  stages.forEach((s, i) => {
    const m = s.model ? `<small>${dmEsc(s.model)}</small>` : "";
    const cls = DM_LLM_STAGES.includes(s.type) ? "node accent" : "node";
    parts.push(`<span class="arrow" id="arch-arrow-${i}">→</span>`);
    parts.push(`<span class="${cls}" id="arch-node-${i}" data-idx="${i}">`
      + `${dmEsc(s.type)}${m}<small>${DM_STAGE_DESC[s.type] || ""}</small></span>`);
  });
  el("dm-arch").innerHTML = '<div class="archrow">' + parts.join("") + "</div>";
}

// ── 観客向け演出: 推論中ノードを光らせる ──
function archActivate(i) {
  document.querySelectorAll("#dm-arch .arrow").forEach(a => a.classList.remove("flow"));
  document.querySelectorAll("#dm-arch .node").forEach(n => {
    const d = n.dataset.idx;
    if (d === "in" || +d < i) { n.classList.remove("active"); n.classList.add("done"); }
    else if (+d > i) { n.classList.remove("active", "done"); }
  });
  const cur = el("arch-node-" + i);
  if (cur) { cur.classList.add("active"); cur.classList.remove("done"); }
  const ar = el("arch-arrow-" + i);
  if (ar) ar.classList.add("flow");
}
function archDone(i) {
  const n = el("arch-node-" + i);
  if (n) { n.classList.remove("active"); n.classList.add("done"); }
  const ar = el("arch-arrow-" + i);
  if (ar) ar.classList.remove("flow");
}
function archReset() {
  document.querySelectorAll("#dm-arch .node").forEach(n => n.classList.remove("active", "done"));
  document.querySelectorAll("#dm-arch .arrow").forEach(a => a.classList.remove("flow"));
}
function archFinish() {
  document.querySelectorAll("#dm-arch .arrow").forEach(a => a.classList.remove("flow"));
  document.querySelectorAll("#dm-arch .node").forEach(n => {
    n.classList.remove("active");
    if (n.dataset.idx !== "in") n.classList.add("done");
  });
}

// ステージ表示ブロックを取得/作成
function stageBlock(i, label) {
  let b = document.getElementById("stg-" + i);
  if (!b) {
    b = document.createElement("div"); b.className = "stageblock"; b.id = "stg-" + i;
    b.innerHTML = `<div class="stagehdr"></div>`
      + `<div class="substream meta" style="display:none"></div>`
      + `<details class="promptbox" style="display:none"><summary>注入プロンプト（このステージにLLMへ送った内容）</summary>`
      + `<div class="substream prompt"></div></details>`
      + `<div class="substream knowledge" style="display:none"></div>`
      + `<div class="substream think" style="display:none"></div>`
      + `<div class="substream content"></div>`;
    el("dm-stages").append(b);
  }
  if (label) b.querySelector(".stagehdr").textContent = label;
  return b;
}

async function dmRun() {
  const stages = currentStages();
  if (!stages.length) { dmStatus("先に「構成」を選んでください"); return; }
  el("dm-stages").innerHTML = ""; el("dm-answer").innerHTML = "";
  el("dm-notice").style.display = "none"; el("dm-notice").textContent = "";
  renderArch(); archReset();   // 演出: 前回のハイライトを消して仕切り直し
  dmSetRunning(true); dmStatus("実行中…");
  const body = {
    stages: DM_STAGES || [],
    question: el("dm-question").value, captions: DM_CAPTIONS, images: DM_IMAGES,
    temperature: DM_TEMP,
  };
  const acc = {};       // stage_index -> {think, content}
  let final = "";
  let skipped = "";     // モデル未設定などで実行されなかった段の理由（結論の文言に反映する）
  DM_ABORT = new AbortController();
  const t0 = performance.now();
  try {
    const res = await fetch("/api/demo/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: DM_ABORT.signal,
    });
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        let ev; try { ev = JSON.parse(line); } catch { continue; }
        if (ev.hb) continue;   // ハートビート(接続維持)は無視
        if (ev.error) { dmStatus("エラー: " + ev.error); continue; }
        if (ev.type === "notice") { el("dm-notice").textContent = ev.text || ""; el("dm-notice").style.display = ""; continue; }
        if (ev.type === "final") { final = ev.answer || ""; continue; }
        const i = ev.stage_index;
        if (i === undefined) continue;
        const a = acc[i] || (acc[i] = { think: "", content: "" });
        if (ev.phase === "start") {
          const sb = stageBlock(i, `#${i + 1} ${ev.stage_type}${ev.model ? " (" + ev.model + ")" : ""}`);
          archActivate(i);
          if (ev.user !== undefined) {       // LLMへ実際に送った注入済みプロンプトを表示
            sb.querySelector(".promptbox").style.display = "";
            const inj = (ev.injected && ev.injected.length) ? `\n\n— 注入された文脈: ${ev.injected.join(", ")} —` : "";
            sb.querySelector(".prompt").textContent =
              (ev.system ? "【system】\n" + ev.system + "\n\n" : "") + "【user（プレースホルダ注入済み）】\n" + ev.user + inj;
          }
        }
        if (ev.phase === "end") archDone(i);
        if (ev.note) {
          const mb = stageBlock(i, `#${i + 1} ${ev.stage_type || ""}`).querySelector(".meta");
          mb.style.display = ""; mb.textContent = "（" + ev.note + "）";
          // ★理由を捨てない。ここで拾っておかないと、結論欄に無関係な文言が出る（下）
          if (/モデル未指定/.test(ev.note)) skipped = ev.note;
        }
        if (ev.knowledge !== undefined) {    // RAG/補助で取り出した知識（次ステージへ注入）
          const kb = stageBlock(i).querySelector(".knowledge");
          kb.style.display = ""; kb.textContent = "📚 取得した知識（後段プロンプトに注入）:\n" + (ev.knowledge || "(なし)");
        }
        if (ev.phase === "end" && ev.stage_type === "vote") {
          stageBlock(i, `#${i + 1} vote`).querySelector(".content").textContent = "多数決 → " + (ev.answer || "(なし)");
        }
        const b = stageBlock(i);
        if (ev.thinking) {
          a.think += ev.thinking;
          const t = b.querySelector(".think"); t.style.display = ""; t.textContent = "💭 " + a.think; t.scrollTop = t.scrollHeight;
        }
        if (ev.content) {
          a.content += ev.content;
          const c = b.querySelector(".content"); c.textContent = a.content; c.scrollTop = c.scrollHeight;
        }
      }
    }
    let mark = "";
    if (final && DM_GOLD) {
      const norm = s => [...new Set((s.toLowerCase().match(/[a-e]/g) || []))].sort().join("");
      mark = norm(final) === norm(DM_GOLD) ? ' <span class="ok">○ 正解</span>' : ' <span class="ng">× 不正解</span>';
    }
    archFinish();
    const ab = el("dm-answer");
    // ★結論の文言は「なぜ出なかったか」で出し分ける（2026-08-14）。
    //   以前は final が空かどうかしか見ておらず、**モデルが未設定で AI を一度も呼んでいない**
    //   場合にも「解答を抽出できませんでした」と出していた。原因と表示が無関係で、
    //   実機検証では利用者がここで11分詰まった。原因は note に入っていたのに使っていなかった。
    let fail = "（解答を抽出できませんでした）";
    if (skipped) {
      fail = '<span class="ng">この構成にはモデルが設定されていません。</span>'
        + '「構成の作成」タブでモデルを選んで保存してから、もう一度実行してください。'
        + ' <a href="#editor" data-go="editor">構成の作成をひらく</a>';
    }
    ab.innerHTML = final ? `解答: <b>${dmEsc(final)}</b>${mark}` : fail;
    ab.classList.remove("reveal"); void ab.offsetWidth; ab.classList.add("reveal");  // 登場演出
    dmStatus(`完了 ${(performance.now() - t0) / 1000 | 0}s`);
  } catch (e) {
    dmStatus(e.name === "AbortError" ? "停止しました" : "通信エラー: " + e.message);
  } finally { dmSetRunning(false); DM_ABORT = null; archFinish(); }
}

function dmSetRunning(on) { el("dm-run").disabled = on; el("dm-stop").disabled = !on; }
function dmStatus(m) { el("dm-status").textContent = m; }

// デフォルトに戻す（再読み込みせず入力・結果をクリア）
function dmReset() {
  if (DM_ABORT) { DM_ABORT.abort(); DM_ABORT = null; }
  DM_IMAGES = []; DM_CAPTIONS = ""; DM_GOLD = ""; DM_STAGES = null; DM_TEMP = 0;
  el("dm-images").value = ""; el("dm-question").value = "";
  el("dm-config").value = ""; el("dm-cfgnote").textContent = "";
  el("dm-gold").innerHTML = ""; el("dm-answer").innerHTML = ""; el("dm-stages").innerHTML = "";
  el("dm-notice").style.display = "none"; el("dm-notice").textContent = "";
  dmThumbs(); renderArch(); archReset();
  dmStatus("デフォルトに戻しました");
}
function dmThumbs() { el("dm-thumbs").innerHTML = DM_IMAGES.map(s => `<img class="thumb" src="${s}">`).join(""); }

async function loadSampleList() {
  try {
    const d = await fetch("/api/demo/samples").then(r => r.json());
    const sel = el("dm-sample-name");
    if (!sel) return;
    // 各オプションに dataset を data 属性で保持（外部実行系は空）
    sel.innerHTML = (d.samples || []).map(s => {
      const label = s.dataset ? `${s.name}（${s.dataset}）` : `${s.name}（あなたのデータ）`;
      return `<option value="${s.name}" data-ds="${s.dataset || ""}">${label}</option>`;
    }).join("");
  } catch (e) { /* 一覧取得失敗は無視（既定名で読める） */ }
}

async function loadSample() {
  dmStatus("サンプル読込中…");
  try {
    const sel = el("dm-sample-name");
    const name = (sel && sel.value) || "s001";
    const ds = sel && sel.selectedOptions[0] ? (sel.selectedOptions[0].dataset.ds || "") : "";
    const dsq = ds ? "&dataset=" + encodeURIComponent(ds) : "";
    const s = await fetch("/api/demo/sample?name=" + encodeURIComponent(name) + dsq).then(r => r.json());
    if (s.error) { dmStatus("取得失敗: " + s.error); return; }
    el("dm-question").value = s.question || "";
    DM_IMAGES = s.images || []; DM_CAPTIONS = s.captions || ""; DM_GOLD = s.gold || "";
    el("dm-gold").innerHTML = DM_GOLD ? `正解: <b>${dmEsc(DM_GOLD)}</b>` : "";
    el("dm-answer").innerHTML = ""; el("dm-stages").innerHTML = "";
    dmThumbs(); renderArch();
    dmStatus(`サンプル読込: ${s.title}（画像${DM_IMAGES.length}・caption${s.captions ? "有" : "無"}・正解${DM_GOLD || "?"}）`);
  } catch (e) { dmStatus("取得エラー: " + e.message); }
}

function ensureOption(id, value) {
  if (!value) return;
  const sel = el(id);
  if (![...sel.options].some(o => o.value === value)) sel.add(new Option(value + "（未登録）", value));
  sel.value = value;
}

function applyConfig(cfg) {
  if (!cfg) return;
  // 構成のモデル/プロンプト/パラメータは選んだ構成に内包（手動設定は撤去）。
  // 多段は cfg.stages を、単段は cfg から1ステージを合成して DM_STAGES に統一。
  if (cfg.stages && cfg.stages.length) {
    DM_STAGES = cfg.stages;
  } else {
    DM_STAGES = [{ type: "direct_mc", model: cfg.model || null, prompt_id: cfg.prompt_id || null,
                   think: cfg.think || "default", num_ctx: cfg.num_ctx ?? null }];
  }
  DM_TEMP = cfg.temperature ?? 0;
  // ★「※モデル未マップ」は専門用語すぎて意味が取れないと実機検証で指摘された（2026-08-14）。
  //   何が起きるのかを平易に書く。判定も cfg.model 単体でなく実際の段を見る
  //   （多段構成では cfg.model が空でも各段にモデルがあることがある）。
  const noModel = DM_STAGES.some(s => s.type !== "vote" && s.type !== "aggregate" && !s.model);
  el("dm-cfgnote").textContent = noModel
    ? "（この構成はモデル未設定です。実行時に、入っているモデルから自動で選びます）"
    : `（${DM_STAGES.length}ステージ）`;
  renderArch();
  dmStatus("構成適用: " + cfg.label + (cfg.note ? `［${cfg.note}］` : ""));
}

async function loadConfigs() {
  const sel = el("dm-config");
  const data = await fetch("/api/demo/configs").then(r => r.json());
  DM_CONFIGS = []; sel.innerHTML = '<option value="">（構成を選択）</option>';
  const grp = (label, items) => {
    if (!items || !items.length) return;
    const og = document.createElement("optgroup"); og.label = label;
    items.forEach(it => { const idx = DM_CONFIGS.push(it) - 1; og.append(new Option(it.label + (it.multi ? " ⛓多段" : ""), String(idx))); });
    sel.append(og);
  };
  grp("保存プラン", data.plans); grp("最高ベンチ", data.best);
  sel.onchange = e => { if (e.target.value !== "") applyConfig(DM_CONFIGS[+e.target.value]); };
}

// ★2026-10-16 修正（学会の実演で指摘）: 画面をリロードしても問題文が残り、画像欄に「10枚を選択」が残り続けた。
//   アプリは保存していない。Firefox がリロード時にフォームの中身を復元していた（画像の実体 DM_IMAGES は空なので、
//   表示と中身が食い違う）。HTML 側の autocomplete="off" に加え、ここで必ず空にする。
//   pageshow(persisted) は「戻る/進む」でキャッシュから復帰したとき（initDemo が走らない）用。
function dmClearInputs() {
  el("dm-question").value = "";
  el("dm-images").value = "";
  DM_IMAGES = []; DM_CAPTIONS = ""; DM_GOLD = "";
  el("dm-gold").innerHTML = ""; el("dm-answer").innerHTML = ""; el("dm-stages").innerHTML = "";
  dmThumbs();
}
window.addEventListener("pageshow", e => { if (e.persisted) { dmClearInputs(); renderArch(); } });

async function initDemo() {
  dmClearInputs();
  const meta = await fetch("/api/editor/meta").then(r => r.json());
  DM_MODELS = meta.models || [];
  el("dm-meta").textContent = `接続先の AI: ${meta.ollama_url}（モデル ${DM_MODELS.length} 件）`;

  el("dm-images").addEventListener("change", e => {
    DM_IMAGES = []; const files = [...e.target.files]; let pend = files.length;
    if (!pend) { dmThumbs(); renderArch(); return; }
    files.forEach(f => { const fr = new FileReader(); fr.onload = () => { DM_IMAGES.push(fr.result); if (--pend === 0) { dmThumbs(); renderArch(); } }; fr.readAsDataURL(f); });
  });
  el("dm-run").onclick = dmRun;
  el("dm-stop").onclick = () => { if (DM_ABORT) DM_ABORT.abort(); };
  el("dm-reset").onclick = dmReset;
  el("dm-sample").onclick = loadSample;
  el("dm-config-reload").onclick = e => reloadDemoModels(e.currentTarget);
  await loadSampleList();
  await loadConfigs();
  renderArch();
}

// モデル一覧を ollama から再取得（新しく pull したモデルを反映）。構成一覧も更新する。
// ★2026-08-14 修正: この関数は (1) `btn` が未宣言で、呼べば ReferenceError で即落ち
//   (2) そもそもどこからも呼ばれていなかった（＝デッドコード）。
//   そのためモデル一覧は起動時の1回しか取られず、「モデルを入れたのに個別実行に出ない」
//   という実機検証の症状に直結していた。引数で受け取る形に直し、再取得ボタンに結線する。
async function reloadDemoModels(btn) {
  if (btn) { btn.disabled = true; btn.textContent = "取得中…"; }
  try {
    const meta = await fetch("/api/editor/meta").then(r => r.json());
    DM_MODELS = meta.models || [];
    el("dm-meta").textContent = `接続先の AI: ${meta.ollama_url}（モデル ${DM_MODELS.length} 件）`;
    await loadConfigs();
  } catch (e) {
    el("dm-meta").textContent = "モデル一覧の取得に失敗しました（ollama が起動しているか確認してください）";
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "モデルと構成を再取得"; }
  }
}

initDemo();
})();
