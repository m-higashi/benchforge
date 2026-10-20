// Benchforge P2 プランエディタ: モデル組合せ・ステージ・パラメータ・プロンプトを編集。
// 構成は Benchforge 自前形式（stage.model = ollama タグ）。外部実行系には書かない。
// 名前衝突防止: 全体を IIFE で包みファイルスコープに閉じる。
(function () {
"use strict";

// 画面に出す前に必ず通す（データセット名・split 名は利用者のフォルダ名やCSVのキー＝任意の文字列）。
// ★2026-09-23 追加: nightly.js は同じ箇所を esc() 済みだったのに、このファイルだけ素のまま
//   innerHTML に差し込んでいた（同じ規則の写しが2つあり、片方だけ直っていた）。
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

let MODELS = [];        // [{name, params, size_gb}]
let STAGE_TYPES = [];
let PROMPTS = {};       // {id: {system, user, description}}
let PLAN = null;

const LLM_STAGES = ["direct_mc", "vision_findings", "reason"];

// ステージの使い方注釈
const STAGE_DESC = {
  direct_mc: "画像+設問+選択肢を渡し、記号を直接選ばせる（最も基本）。モデル/プロンプト要。",
  vision_findings: "画像から所見を自由記述させる（後段 reason への入力）。診断は断定させない。",
  reason: "所見+設問+選択肢から根拠を述べて選ばせる（vision_findings の後段）。",
  vote: "直前までの解答ステージの結果を多数決（アンサンブル統合）。モデル/プロンプト不要。",
};

function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  for (const kid of kids) e.append(kid?.nodeType ? kid : document.createTextNode(kid ?? ""));
  return e;
}

function sel(opts, cur, onch) {
  const s = h("select", { onchange: e => onch(e.target.value) });
  opts.forEach(o => { const op = h("option", { value: o }, o); if (o === cur) op.selected = true; s.append(op); });
  return s;
}

function firstPromptId() { return Object.keys(PROMPTS)[0] || "mc"; }
// ★既定値: 空欄でも動くよう推奨値を最初から入れておく（利用者が知らなくてよいように）
const DEF_NUM_PREDICT = 2048;   // 出力の上限。思考モードは 6144 程度必要
const DEF_NUM_CTX     = 32768;  // 文脈長。画像や長い指示文もここを消費する
// 描画後に「.lbl とその直後の入力欄」を .fld で包み、途中で折り返さないようにする。
// （個別に field() へ書き換えるより安全＝既存の生成コードを触らない）
function wrapFields(root) {
  root.querySelectorAll(".stagerow > .lbl").forEach(lbl => {
    const nx = lbl.nextElementSibling;
    if (!nx || !/^(INPUT|SELECT)$/.test(nx.tagName)) return;
    const box = document.createElement("span");
    box.className = "fld";
    lbl.parentNode.insertBefore(box, lbl);
    box.appendChild(lbl); box.appendChild(nx);
  });
}

// ラベルと入力欄を1単位にして、その間で折り返さないようにする（.stagerow が flex-wrap のため）
function field(labelText, titleText, inputProps) {
  return h("span", { class: "fld" },
    h("span", { class: "lbl", title: titleText || "" }, labelText),
    h("input", inputProps));
}

function newStage() { return { type: "direct_mc", model: "", prompt_id: firstPromptId(), think: "default",
                               num_ctx: DEF_NUM_CTX, num_predict: DEF_NUM_PREDICT }; }
function newConfig(n) { return { config_id: "cfg_" + n, temperature: 0.0, stages: [newStage()] }; }
function defaultPlan() { return { name: "my_plan", split: "", dataset_path: "", configs: [newConfig(1)] }; }

// ---- 保存形 JSON ----
function buildPlanJSON() {
  const p = {
    date: new Date().toISOString().slice(0, 10),
    // ★|| "dev" を付けない。問題セット選択時は「全問」＝空文字なので必ず "dev" に化け、
    //   画面のどこにも出ないまま保存 JSON に入っていた（Win 実機検証 2026-08-14）。
    split: document.getElementById("ed-split").value,
    max_configs: PLAN.configs.length,
    configs: PLAN.configs.map(c => {
      const cfg = {
        config_id: c.config_id,
        temperature: Number(c.temperature) || 0.0,
      };
      // top_k / top_p は入力があるときだけ書く（空欄=ollama既定に委ねる＝キーを出さない）。
      // ⚠ temperature=0 では無効（argmax確定）。実際に効かせるには temperature>0 が必要。
      if (c.top_k !== "" && c.top_k != null) cfg.top_k = Number(c.top_k);
      if (c.top_p !== "" && c.top_p != null) cfg.top_p = Number(c.top_p);
      cfg.stages = c.stages.map(s => {
        const o = { type: s.type };
        if (LLM_STAGES.includes(s.type)) {
          o.model = s.model || null;
          if (s.prompt_id) o.prompt_id = s.prompt_id;
          if (s.think === "true") o.think = true;
          else if (s.think === "false") o.think = false;
          if (s.num_ctx !== "" && s.num_ctx != null) o.num_ctx = Number(s.num_ctx);
          if (s.num_predict !== "" && s.num_predict != null) o.num_predict = Number(s.num_predict);
        }
        return o;
      });
      return cfg;
    })
  };
  const ds = document.getElementById("ed-dataset").value.trim();
    if (ds) p.dataset_path = ds;
  return p;
}

function refreshPreview() {
  document.getElementById("ed-preview").textContent = JSON.stringify(buildPlanJSON(), null, 2);
}

// ---- ステージ行 ----
function renderStage(cfg, s, si) {
  const typeSel = h("select", { onchange: e => { s.type = e.target.value; renderConfigs(); } });
  STAGE_TYPES.forEach(t => { const o = h("option", { value: t }, t); if (t === s.type) o.selected = true; typeSel.append(o); });

  const row = h("div", { class: "stagerow" }, h("span", { class: "lbl" }, `#${si + 1}`), typeSel);

  if (LLM_STAGES.includes(s.type)) {
    const modelSel = h("select", { onchange: e => { s.model = e.target.value; refreshPreview(); } },
      h("option", { value: "" }, "(モデル選択)"));
    MODELS.forEach(m => {
      const o = h("option", { value: m.name }, `${m.name}${m.params ? " (" + m.params + ")" : ""}`);
      if (m.name === s.model) o.selected = true; modelSel.append(o);
    });
    const promptSel = h("select", { onchange: e => { s.prompt_id = e.target.value; refreshPreview(); } });
    Object.keys(PROMPTS).forEach(pid => { const o = h("option", { value: pid }, pid); if (pid === s.prompt_id) o.selected = true; promptSel.append(o); });

    row.append(
      h("span", { class: "lbl" }, "model"), modelSel,
      h("span", { class: "lbl" }, "prompt"), promptSel,
      h("button", { class: "mini", title: "このプロンプトを編集", onclick: () => loadPromptToEditor(s.prompt_id) }, "編集"),
      h("span", { class: "lbl" }, "think"),
      sel(["default", "false", "true"], s.think || "default", v => { s.think = v; refreshPreview(); }),
      h("span", { class: "lbl", title: "文脈長 context length。入力+出力の上限トークン数。推奨 32768。空欄=モデル既定" }, "文脈長"),
      h("input", { value: s.num_ctx ?? "", type: "number", placeholder: String(DEF_NUM_CTX), style: "width:82px",
        oninput: e => { s.num_ctx = e.target.value; refreshPreview(); } }),
      h("span", { class: "lbl", title: "出力上限 num_predict。推奨 2048。思考モードを on にするなら 6144 以上（思考が枠を食い解答が出ず棄権になるため）" }, "出力上限"),
      h("input", { value: s.num_predict ?? "", type: "number", placeholder: String(DEF_NUM_PREDICT), style: "width:82px",
        oninput: e => { s.num_predict = e.target.value; refreshPreview(); } }));
  }

  row.append(h("button", { class: "x", onclick: () => {
    cfg.stages.splice(si, 1); if (!cfg.stages.length) cfg.stages.push(newStage()); renderConfigs();
  } }, "×"));

  // 注釈
  const box = h("div", {}, row, h("div", { class: "stagedesc" }, STAGE_DESC[s.type] || ""));
  return box;
}

function renderConfigs() {
  const root = document.getElementById("ed-configs");
  root.innerHTML = "";
  PLAN.configs.forEach((c, ci) => {
    const box = h("div", { class: "cfgbox" });
    box.append(h("div", { class: "stagerow" },
      h("span", { class: "lbl" }, "config_id"),
      h("input", { value: c.config_id, style: "width:170px", placeholder: "半角英数字",
        oninput: e => { c.config_id = e.target.value; markCfgId(e.target); refreshPreview(); } }),
      h("span", { class: "lbl" }, "temperature"),
      h("input", { value: c.temperature, type: "number", step: "0.1", style: "width:70px", oninput: e => { c.temperature = e.target.value; refreshPreview(); } }),
      // サンプリング候補集合の制御。空欄=ollama既定に委ねる。⚠temperature=0では無効(argmax確定)。
      field("top_k", "サンプリング候補上位k個。空欄=ollama既定(gemma4=64)。temperature=0では無効",
        { value: c.top_k ?? "", type: "number", step: "1", placeholder: "既定", style: "width:64px",
          oninput: e => { c.top_k = e.target.value; refreshPreview(); } }),
      h("span", { class: "lbl", title: "核サンプリング確率質量。空欄=ollama既定(gemma4=0.95)。temperature=0では無効" }, "top_p"),
      h("input", { value: c.top_p ?? "", type: "number", step: "0.05", placeholder: "既定", style: "width:64px",
        oninput: e => { c.top_p = e.target.value; refreshPreview(); } }),
      h("button", { class: "x", onclick: () => {
        PLAN.configs.splice(ci, 1); if (!PLAN.configs.length) PLAN.configs.push(newConfig(1)); renderConfigs();
      } }, "構成を削除")));
    c.stages.forEach((s, si) => box.append(renderStage(c, s, si)));
    box.append(h("button", { class: "mini", onclick: () => { c.stages.push(newStage()); renderConfigs(); } }, "+ ステージを追加"));
    root.append(box);
  });
  refreshPreview();
  wrapFields(root);   // ラベルと入力欄をペアで包み、途中で折り返さないようにする
}

// ---- プロンプト編集 ----
function refreshPromptSelect() {
  const psel = document.getElementById("pr-select");
  psel.innerHTML = "";
  Object.keys(PROMPTS).forEach(pid => psel.append(h("option", { value: pid }, pid)));
}
// 読み込み元の ID を覚えておく。保存時に「改名」と「別名で増やす」を区別するため。
let PR_LOADED_ID = "";
function loadPromptToEditor(pid) {
  const p = PROMPTS[pid]; if (!p) return;
  PR_LOADED_ID = pid;
  document.getElementById("pr-select").value = pid;
  document.getElementById("pr-id").value = pid;
  document.getElementById("pr-desc").value = p.description || "";
  document.getElementById("pr-system").value = p.system || "";
  document.getElementById("pr-user").value = p.user || "";
  document.getElementById("pr-id").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
async function savePrompt() {
  const body = {
    id: document.getElementById("pr-id").value.trim(),
    description: document.getElementById("pr-desc").value,
    system: document.getElementById("pr-system").value,
    user: document.getElementById("pr-user").value,
  };
  if (!body.id) { prStatus("ID を入力してください"); return; }
  // ★ID を書き換えて保存したとき、何が起きるのかを必ず本人に選ばせる（2026-08-07）。
  //   以前は黙って「別名で増える」動きで、改名したつもりの利用者に元が残っていた。
  let url = "/api/prompts";
  if (PR_LOADED_ID && PR_LOADED_ID !== body.id && PROMPTS[PR_LOADED_ID]) {
    const rename = confirm(
      `プロンプトの ID を「${PR_LOADED_ID}」から「${body.id}」に変えて保存します。\n\n`
      + `［OK］名前を変える（元の「${PR_LOADED_ID}」は無くなります）\n`
      + `［キャンセル］別名で保存する（元の「${PR_LOADED_ID}」も残ります）`);
    if (rename) { url = "/api/prompts/rename"; body.old_id = PR_LOADED_ID; }
  }
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  }).then(x => x.json());
  if (r.error) { prStatus("エラー: " + r.error); return; }
  await loadPrompts();
  refreshPromptSelect();
  renderConfigs();           // ステージの prompt ドロップダウンに反映
  document.getElementById("pr-select").value = r.saved;
  PR_LOADED_ID = r.saved;
  prStatus(r.removed_old ? `名前を変えました: ${r.saved}`
                         : ("保存: " + r.saved + (r.note ? "  " + r.note : "")));
}
function prStatus(m) { const el = document.getElementById("pr-status"); el.textContent = m; setTimeout(() => el.textContent = "", 4000); }

async function loadPrompts() {
  PROMPTS = (await fetch("/api/prompts").then(x => x.json())).prompts || {};
}

// ---- 保存プラン ----
async function loadSaved() {
  const r = await fetch("/api/plans").then(x => x.json());
  const root = document.getElementById("ed-saved"); root.innerHTML = "";
  (r.plans || []).forEach(p => root.append(h("div", { class: "savedrow" },
    h("a", { onclick: () => loadPlan(p.name) }, p.name),
    h("span", {}, `${p.configs}構成 / ${p.split || "-"} / ${p.updated}`))));
  if (!(r.plans || []).length) root.textContent = "（まだありません）";
}
async function loadPlan(name) {
  const doc = await fetch("/api/plans/" + encodeURIComponent(name)).then(x => x.json());
  PLAN = {
    name, split: doc.split || "", dataset_path: doc.dataset_path || "",
    configs: (doc.configs || []).map(c => ({
      config_id: c.config_id, temperature: c.temperature ?? 0,
      // top_k / top_p は無ければ空欄（=ollama既定に委ねる）として保持し編集で出せるようにする
      top_k: c.top_k ?? "", top_p: c.top_p ?? "",
      stages: (c.stages || []).map(s => ({
        type: s.type, model: s.model || "", prompt_id: s.prompt_id || firstPromptId(),
        think: s.think === true ? "true" : s.think === false ? "false" : "default",
        // ★既定を補う: 古いプランや欄の無いプランを読んでも推奨値で動くようにする
        num_ctx: s.num_ctx ?? DEF_NUM_CTX,
        num_predict: s.num_predict ?? DEF_NUM_PREDICT
      }))
    }))
  };
  document.getElementById("ed-name").value = name;
  document.getElementById("ed-split").value = PLAN.split;
  document.getElementById("ed-dataset").value = PLAN.dataset_path;
  renderConfigs();
  setStatus("読み込み: " + name);
}
// プラン名は保存ファイル名になり、非ASCIIは "_" に潰れて読み込めなくなる。
// 保存してから気づくと事故になるので、入力中にその場で警告する。
// ---- 名前の規則（★ファイル名になる名前だけ。タグ名・split名は日本語でよい）----
// サーバ側 app/services/naming.py と同じ規則。片方だけ変えないこと。
const NAME_OK = /^[A-Za-z0-9_\-]+$/;
function badName(v) { return !!(v || "").trim() && !NAME_OK.test((v || "").trim()); }

function checkNameAscii() {
  const el = document.getElementById("ed-name");
  const hint = document.getElementById("ed-name-hint");
  if (!el || !hint) return;
  const bad = badName(el.value);
  hint.style.color = bad ? "#e0a050" : "";
  hint.innerHTML = bad
    ? '⚠ <b>この名前では保存できません</b>：半角英数字・ハイフン(-)・アンダースコア(_) のみ。'
      + '例: <code>my_plan</code> / <code>ensemble-judge</code>'
    : '<b>半角英数字・ハイフン(-)・アンダースコア(_) で付けてください</b>'
      + '（保存ファイル名になります。→<a href="#" data-tab="home" class="nm-link">名前の付け方</a>）';
}

// config_id も成績表の見出し・解析APIの絞り込みキーになるため同じ規則。
// （カンマを含むと複数構成の指定が分断され、解析タブで選べなくなる）
function markCfgId(inp) {
  const bad = badName(inp.value);
  inp.style.borderColor = bad ? "#e0a050" : "";
  inp.title = bad ? "構成IDは半角英数字・ハイフン・アンダースコアで付けてください（成績表の見出しになります）" : "";
}

document.addEventListener("input", e => { if (e.target && e.target.id === "ed-name") checkNameAscii(); });

function setStatus(m) { const el = document.getElementById("ed-status"); el.textContent = m; setTimeout(() => el.textContent = "", 4000); }

// ---- デフォルトに戻す（再読み込みせず初期状態へ） ----
function resetEditor() {
  PLAN = defaultPlan();
  document.getElementById("ed-name").value = PLAN.name;
  document.getElementById("ed-split").value = PLAN.split;
  document.getElementById("ed-dataset").value = "";
  renderConfigs();
  loadPromptToEditor(firstPromptId());
  setStatus("デフォルトに戻しました");
}

// データセット（トグル）＋その split をドロップダウンに読み込む
async function loadDatasetToggle() {
  const dsSel = document.getElementById("ed-dataset");
  const spSel = document.getElementById("ed-split");
  if (!dsSel || !spSel) return;
  let opts = "", cur = "";
  try {
    const d = await fetch("/api/datasets").then(r => r.json());
    if (d.external) opts += '<option value="">設定で指定した場所</option>';
    (d.datasets || []).forEach(n => opts +=
      `<option value="${esc(n)}">${esc(n)}${n === "your_data" ? "（あなたのデータ）" : "（サンプル）"}</option>`);
    cur = d.current || "";
  } catch (e) { /* ignore */ }
  dsSel.innerHTML = opts;
  if (cur) dsSel.value = cur;
  async function loadSplits() {
    const ds = dsSel.value;
    let sp = ds
      ? '<option value="">（全問＝IN-BOX全体）</option>'
      : '<option value="dev">dev</option><option value="dev_mini">dev_mini</option><option value="test">test</option>';
    try {
      const q = ds ? "?dataset=" + encodeURIComponent(ds) : "";
      const s = await fetch("/api/splits" + q).then(r => r.json());
      Object.keys(s.splits || {}).forEach(n => sp += `<option value="${esc(n)}">${esc(n)}（定義済み ${s.splits[n].length}問）</option>`);
    } catch (e) { /* ignore */ }
    spSel.innerHTML = sp;
    refreshPreview();
  }
  dsSel.onchange = loadSplits;
  await loadSplits();
}

// モデル一覧を ollama から再取得して各ステージの選択肢へ反映（新しく pull したモデルを反映）
async function reloadModels() {
  const btn = document.getElementById("ed-reload-models");
  if (btn) { btn.disabled = true; btn.textContent = "取得中…"; }
  try {
    const m = await fetch("/api/editor/meta").then(x => x.json());
    MODELS = m.models || []; STAGE_TYPES = m.stage_types || [];
    document.getElementById("ed-meta").textContent =
      `接続先 ${m.ollama_url}（使えるモデル ${MODELS.length} 件）`;
    if (PLAN) renderConfigs();   // ステージのモデル選択肢を再描画
  } catch (e) {
    document.getElementById("ed-meta").textContent = "モデル一覧の取得に失敗しました（ollama が起動しているか確認してください）";
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "モデル一覧を再取得"; }
  }
}

async function initEditor() {
  const m = await fetch("/api/editor/meta").then(x => x.json());
  MODELS = m.models || []; STAGE_TYPES = m.stage_types || [];
  await loadPrompts();
  document.getElementById("ed-meta").textContent =
    `接続先 ${m.ollama_url}（使えるモデル ${MODELS.length} 件）`;
  PLAN = defaultPlan();
  renderConfigs();
  await loadDatasetToggle();   // PLAN 確定後に（refreshPreview が PLAN.configs を触るため）
  refreshPromptSelect();
  loadPromptToEditor(firstPromptId());
  loadSaved();

  document.getElementById("ed-add-config").onclick = () => { PLAN.configs.push(newConfig(PLAN.configs.length + 1)); renderConfigs(); };
  const rmb = document.getElementById("ed-reload-models"); if (rmb) rmb.onclick = reloadModels;
  document.getElementById("ed-save").onclick = async () => {
    const name = document.getElementById("ed-name").value.trim() || "my_plan";
    const res = await fetch("/api/plans", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, plan: buildPlanJSON() })
    }).then(x => x.json());
    if (res.error) { setStatus("保存できません: " + res.error); return; }
    setStatus("保存しました: " + (res.saved || name)); loadSaved();
  };
  document.getElementById("ed-export").onclick = () => {
    const blob = new Blob([JSON.stringify(buildPlanJSON(), null, 2)], { type: "application/json" });
    const a = h("a", { href: URL.createObjectURL(blob), download: (document.getElementById("ed-name").value.trim() || "plan") + ".json" });
    a.click();
  };
  document.getElementById("ed-reset").onclick = resetEditor;
  document.getElementById("pr-save").onclick = savePrompt;
  document.getElementById("pr-select").onchange = e => loadPromptToEditor(e.target.value);
  document.getElementById("pr-new").onclick = () => {
    PR_LOADED_ID = "";
    ["pr-id", "pr-desc", "pr-system", "pr-user"].forEach(id => document.getElementById(id).value = "");
    document.getElementById("pr-id").focus();
  };
  document.getElementById("ed-split").addEventListener("change", refreshPreview);
}

initEditor();
})();
