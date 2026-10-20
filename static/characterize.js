// Benchforge 特性評価タブ: 失敗分析・誤ラベル疑い・Holm有意差・タグ。IIFEで閉じる（グローバル汚染防止）。
(function () {
"use strict";

const el = id => document.getElementById(id);
function esc(s){ return String(s==null?"":s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function pct(x){ return (x*100).toFixed(1)+"%"; }
function goldStr(g){ return Array.isArray(g)?g.join(","):(g==null?"–":String(g)); }

let CONFIGS = [];        // {config_id, acc, correct, n}
let SELECTED = new Set();
let LOADED = false;
let DATASET = "";        // ""=外部実行系、それ以外=標準データセット名。タグ/split/分析のスコープ
function dsq(){ return DATASET ? "&dataset=" + encodeURIComponent(DATASET) : ""; }   // GET用
function dsp(){ return DATASET; }                                                    // POST body用

let DS_OPTS = "";
function dsLabel(n){ return n === "your_data" ? n + "（あなたのデータ）" : n + "（サンプル）"; }
let DS_CURRENT = "";
async function fetchDatasetOpts(){
  // ★選択肢はフォルダ名そのもの。設定が データセット/ の外を指しているときだけ
  //   「設定で指定した場所」という選択肢を足す（開発者向け。ふつうの利用者には出ない）。
  let opts = "";
  try {
    const d = await fetch("/api/datasets").then(r=>r.json());
    if (d.external) opts += '<option value="">設定で指定した場所</option>';
    (d.datasets||[]).forEach(n=> opts += `<option value="${esc(n)}">${esc(dsLabel(n))}</option>`);
    DS_CURRENT = d.current || "";
    if (!DATASET && DS_CURRENT) DATASET = DS_CURRENT;   // 最初から設定中のものを選んでおく
  } catch(e){
    // ★黙って空にしない。ここで握りつぶすと、サーバが落ちているのに
    //   「構成を1つ以上選んでください」としか出ず、原因にたどり着けない（実機検証 2026-08-14）。
    if (window.BF_NETFAIL) window.BF_NETFAIL(e && (e.message || e));
  }
  DS_OPTS = opts;
  return opts;
}
function setDataset(v){
  DATASET = v; SELECTED.clear();
  ["ch-dataset","mg-dataset"].forEach(id=>{ const s=el(id); if(s) s.value = DATASET; });
  const o1=el("ch-out"); if(o1) o1.innerHTML="";
  updateDownloadLinks();
  loadConfigs(); loadTags(); loadSplits();
  QLIST = []; loadQuestionList();   // 問題の一覧も選んだデータのものに切り替える
}
async function loadDatasets(){
  const opts = await fetchDatasetOpts();
  ["ch-dataset","mg-dataset"].forEach(id=>{
    const sel=el(id); if(!sel) return;
    sel.innerHTML = opts; sel.value = DATASET;
    sel.onchange = ()=> setDataset(sel.value);
  });
  updateDownloadLinks();
}

async function loadConfigs(){
  const d = await fetch("/api/characterize/configs?since=2026-07-13"+dsq()).then(r=>r.json());
  CONFIGS = d.configs || [];
  renderConfigChips();
  el("ch-baseline").innerHTML = CONFIGS.map(c=>`<option value="${esc(c.config_id)}">${esc(c.config_id)}</option>`).join("");
  const b = CONFIGS.find(c=>c.config_id==="base_gemma4_31b");
  if (b) el("ch-baseline").value = "base_gemma4_31b";
}

function renderConfigChips(){
  el("ch-configs").innerHTML = CONFIGS.map(c=>{
    const on = SELECTED.has(c.config_id) ? " on" : "";
    return `<label class="${on.trim()}" data-cfg="${esc(c.config_id)}">`
      + `<input type="checkbox" ${SELECTED.has(c.config_id)?"checked":""}>`
      + `${esc(c.config_id)} <span class="acc">${pct(c.acc)}</span></label>`;
  }).join("");
  el("ch-configs").querySelectorAll("label").forEach(lab=>{
    lab.onclick = e=>{
      if (e.target.tagName!=="INPUT") { const cb=lab.querySelector("input"); cb.checked=!cb.checked; }
      const c = lab.dataset.cfg;
      if (lab.querySelector("input").checked) SELECTED.add(c); else SELECTED.delete(c);
      lab.classList.toggle("on", SELECTED.has(c));
    };
  });
}

function sel(){ return Array.from(SELECTED); }
function need(min){
  if (sel().length < min){ el("ch-status").textContent = `構成を${min}つ以上選んでください`; return false; }
  return true;
}
function busy(t){ el("ch-status").textContent = t; }

// ── 有意差(Holm) ──
async function runSig(){
  if(!need(2)) return; busy("計算中…");
  const d = await fetch("/api/characterize/significance?configs="+encodeURIComponent(sel().join(","))+dsq()).then(r=>r.json());
  let h = `<h3>有意差（総当たり McNemar ＋ Holm 補正・共通 n=${d.n_common}）</h3>`;
  h += '<table><thead><tr><th>構成</th><th class="num">正答率</th><th class="num">95%CI</th></tr></thead><tbody>';
  (d.rows||[]).forEach(r=>{ h+=`<tr><td>${esc(r.config_id)}</td><td class="num ok">${pct(r.acc)}</td>`
    +`<td class="num muted">${(r.ci[0]*100).toFixed(0)}–${(r.ci[1]*100).toFixed(0)}</td></tr>`; });
  h += "</tbody></table>";
  h += '<table><thead><tr><th>比較</th><th class="num">勝/負</th><th class="num">生p</th><th class="num">Holm p</th><th>判定</th></tr></thead><tbody>';
  (d.pairs||[]).forEach(p=>{ const sig=p.sig==="有意";
    h+=`<tr><td>${esc(p.a)} vs ${esc(p.b)}</td><td class="num">${p.disc_a}/${p.disc_b}</td>`
      +`<td class="num muted">${p.p.toFixed(3)}</td><td class="num">${p.p_holm.toFixed(3)}</td>`
      +`<td class="${sig?"ok":"muted"}">${esc(p.sig)}</td></tr>`; });
  el("ch-out").innerHTML = h + "</tbody></table>"
    + '<p class="note">Holm補正後 p&lt;0.05 のみ「有意」。誤差圏＝この母集団では統計的に区別できない。</p>';
  showToolbar(); busy("完了");
}

// ── 属性別正答率 ──
async function runSlices(){
  if(!need(1)) return; busy("計算中…");
  const base = el("ch-baseline").value;
  const d = await fetch(`/api/characterize/slices?configs=${encodeURIComponent(sel().join(","))}&baseline=${encodeURIComponent(base)}${dsq()}`).then(r=>r.json());
  if (d.error){ el("ch-out").innerHTML=`<div class="empty">${esc(d.error)}</div>`; busy(""); return; }
  const cfgs = d.configs||[];
  let h = `<h3>属性別 正答率（共通 n=${d.n_common}・列＝各構成）</h3>`;
  (d.axes||[]).forEach(ax=>{
    if(!ax.rows.length) return;
    h += `<table><thead><tr><th>${esc(ax.axis)}</th><th class="num">n</th>`
      + cfgs.map(c=>`<th class="num">${esc(c)}</th>`).join("") + "</tr></thead><tbody>";
    ax.rows.forEach(r=>{ h+=`<tr><td>${esc(r.bucket)}</td><td class="num muted">${r.n}</td>`
      + cfgs.map(c=>`<td class="num">${pct(r.cfgs[c])}</td>`).join("") + "</tr>"; });
    h += "</tbody></table>";
  });
  el("ch-out").innerHTML = h;
  showToolbar(); busy("完了");
}

// ── 難問コア ──
async function runCore(){
  if(!need(2)) return; busy("計算中…");
  const d = await fetch("/api/characterize/hardcore?configs="+encodeURIComponent(sel().join(","))+dsq()).then(r=>r.json());
  if (d.error){ el("ch-out").innerHTML=`<div class="empty">${esc(d.error)}</div>`; busy(""); return; }
  let h = `<h3>難問コア（選択した ${d.configs.length} 構成が<b>全て不正解</b>の問題）</h3>`;
  h += `<p class="note">難問コア = <b>${d.n_core} / ${d.n_common}</b>（${(100*d.n_core/d.n_common).toFixed(0)}%）`
    + ` ・ 全構成正解 = ${d.n_allpass}。これが集約で動かせない天井の実体。</p>`;
  if ((d.bias||[]).length){
    h += "<h3>難問コアの属性偏り（コア構成比 − 全体構成比・±6pt超）</h3>";
    h += '<table><thead><tr><th>軸</th><th>バケット</th><th class="num">コア%</th><th class="num">全体%</th><th class="num">差</th></tr></thead><tbody>';
    d.bias.forEach(b=> b.buckets.forEach((x,i)=>{
      h+=`<tr><td>${i?"":esc(b.axis)}</td><td>${esc(x.bucket)}</td><td class="num">${x.share_core}%</td>`
        +`<td class="num muted">${x.share_all}%</td><td class="num ${x.delta>0?"ng":"muted"}">${x.delta>0?"+":""}${x.delta}</td></tr>`;
    }));
    h += "</tbody></table>";
  }
  h += `<h3>難問コアの問題一覧（クリックで画像つき詳細・${d.core_qids.length}問）</h3>`;
  h += '<p>'+d.core_qids.map(q=>`<span class="chip qlink" data-qid="${esc(q)}" style="cursor:pointer">${esc(q)}</span>`).join("")+'</p>';
  el("ch-out").innerHTML = h;
  showToolbar();
  busy("完了");
}

// ── 誤ラベル疑い ──
async function runSuspects(){
  busy("計算中…");
  const cfgQ = sel().length>=3 ? "&configs="+encodeURIComponent(sel().join(",")) : "";
  const ref = el("ch-baseline").value || "arbiter_ff";
  const d = await fetch(`/api/characterize/suspects?min_pb=-0.3&ref=${encodeURIComponent(ref)}${cfgQ}${dsq()}`).then(r=>r.json());
  if (d.note && !(d.suspects||[]).length){ el("ch-out").innerHTML=`<div class="empty">${esc(d.note)}</div>`; busy(""); return; }
  // 確認状態（未確認/正しい/要修正）を一緒に取る＝表とCSVの両方に出すため
  const rv = await fetch("/api/review?x=1"+dsq()).then(r=>r.json()).then(x=>x.review||{}).catch(()=>({}));
  let h = `<h3>登録された正解が疑わしい問題（強い構成ほど別の答えを選ぶ問題）</h3>`;
  h += `<p class="note">構成 ${d.n_configs} 種で計算。AI 側の誤りか、<b>登録した正解の側の誤り</b>かを`
    + `原典で確認したい候補です（参照構成の解答も表示）。多様な系統の構成を選ぶほど信頼できます。
       問題番号をクリックすると画像つきで確認でき、その場で状態を記録できます。</p>`;
  h += '<p><a class="ghost" href="/api/review/export.csv?only_fix=1'+dsq()+'">「要修正」だけをCSVで取得</a></p>';
  h += '<table><thead><tr><th>問題</th><th>確認状態</th><th class="num">pb</th><th class="num">全構成正答率</th>'
    + `<th>登録された正解</th><th>${esc(d.ref_config)}の解答</th></tr></thead><tbody>`;
  (d.suspects||[]).forEach(s=>{ const diff = goldStr(s.gold)!==goldStr(s.ref_pred);
    const st = (rv[s.q_id] && rv[s.q_id].status) || "未確認";
    const stc = st==="要修正" ? "ng" : (st==="正しい" ? "ok" : "muted");
    h+=`<tr><td><span class="qlink" data-qid="${esc(s.q_id)}" style="cursor:pointer;text-decoration:underline">${esc(s.q_id)}</span></td>`
      +`<td class="${stc}">${esc(st)}</td>`
      +`<td class="num ng">${s.pb}</td><td class="num">${s.all_acc}%</td>`
      +`<td>${esc(goldStr(s.gold))}</td><td class="${diff?"ng":"muted"}">${esc(goldStr(s.ref_pred))}</td></tr>`; });
  el("ch-out").innerHTML = h + "</tbody></table>";
  showToolbar();
  busy("完了");
}

// ── タグ ──
async function loadTags(){
  const d = await fetch("/api/tags?x=1"+dsq()).then(r=>r.json());
  const tags = d.tags || {};
  const names = d.names || [];
  let h = names.length ? "現在のタグ: " + names.map(n=>`<span class="chip">${esc(n)}</span>`).join("") : "タグ未登録。";
  const entries = Object.entries(tags);
  if (entries.length){
    h += "<br>" + entries.map(([q,ts])=>`${esc(q)}：${ts.map(t=>esc(t)).join("/")}`).join(" ・ ");
  }
  el("ch-tags").innerHTML = h;
}
async function setTag(add){
  const q = el("ch-tag-qid").value.trim(), t = el("ch-tag-name").value.trim();
  if(!q||!t){ el("ch-tag-status").textContent="q_id とタグ名を入力"; return; }
  await fetch("/api/tags",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({q_id:q,tag:t,add:add,dataset:dsp()})}).then(r=>r.json());
  el("ch-tag-status").textContent = add?`付与: ${q} ← ${t}`:`除去: ${q} × ${t}`;
  loadTags();
}

async function importTagsSample(){
  el("ch-tag-status").textContent = "サンプルタグ取込中…";
  // 選択中データセットのサンプルCSVを取得→取込（外部実行系の選択時は最初のデータセットのサンプル）
  const text = await fetch("/api/tags/sample.csv?x=1"+dsq()).then(r=>r.text());
  const d = await fetch("/api/tags/import",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({csv:text,dataset:dsp(),mode:(el("ch-split-replace")&&el("ch-split-replace").checked)?"replace":"merge"})}).then(r=>r.json());
  el("ch-tag-status").textContent = d.ok ? `取込 ${d.added} 件` : ("失敗: "+(d.error||""));
  loadTags();
}
function updateDownloadLinks(){
  // データセットを切り替えたら、書き出しリンクの対象も切り替える
  const set = (id, url)=>{ const e=el(id); if(e) e.href = url + "?x=1" + dsq(); };
  set("ch-tag-cur",  "/api/tags/export.csv");
  set("ch-split-cur","/api/splits/export.csv");
  set("mg-dl-questions", "/api/questions/export.csv");
  set("mg-imp-template", "/api/questions/import_template.csv");
}

// ── CSV から問題を取り込む ──
async function importQuestionsCsv(){
  const st = el("mg-imp-status"), out = el("mg-imp-result");
  const f = el("mg-imp-file") && el("mg-imp-file").files[0];
  if(!f){ st.textContent = "CSVファイルを選んでください"; return; }
  st.textContent = "取り込み中…";
  // ★文字列ではなく**バイト列**で送る。ここで文字列にすると、Excel 既定の
  //   Shift-JIS がブラウザ側で化けてしまい、サーバでは直せなくなる。
  const buf = await f.arrayBuffer();
  const ow = el("mg-imp-ow") && el("mg-imp-ow").checked ? 1 : 0;
  const url = `/api/questions/import_csv?dataset=${encodeURIComponent(DATASET)}&overwrite=${ow}`;
  const d = await fetch(url, {method:"POST", headers:{"Content-Type":"application/octet-stream"}, body:buf})
              .then(r=>r.json());
  if(d.error){ st.textContent = "失敗"; out.innerHTML = `<div class="ng">${esc(d.error)}</div>`; return; }
  st.textContent = "完了";
  let h = `<b>${d.created.length}問</b>を作りました（文字コード: ${esc(d.encoding)}／問題セット: ${esc(d.dataset)}）。`;
  if(d.updated.length) h += ` 上書き ${d.updated.length}問。`;
  if(d.answers) h += ` 正解 ${d.answers}件も登録しました。`;
  if(d.skipped.length) h += `<br><span class="ng">同じ問題IDが既にあるため飛ばした: ${esc(d.skipped.join(", "))}</span>`
    + `（上書きするなら左のチェックを入れて再実行してください）`;
  if((d.warnings||[]).length) h += "<br>" + d.warnings.map(w=>`<span class="ng">${esc(w)}</span>`).join("<br>");
  out.innerHTML = h;
  QLIST = []; loadQuestionList();
}

// ── 問題の一覧（クリックで画像つき詳細）──
let QLIST = [];
async function loadQuestionList(){
  const st = el("mg-status"); if(st) st.textContent = "読み込み中…";
  const d = await fetch("/api/questions/list?x=1"+dsq()).then(r=>r.json()).catch(()=>null);
  if(!d){ if(st) st.textContent = "読み込めませんでした"; return; }
  QLIST = d.questions || [];
  const c = el("mg-qcount"); if(c) c.textContent = `（${d.n}問）`;
  renderQuestionList();
  if(st) st.textContent = "";
}
function renderQuestionList(){
  const box = el("mg-list"); if(!box) return;
  const kw = (el("mg-filter") && el("mg-filter").value || "").trim().toLowerCase();
  const rows = QLIST.filter(r=> !kw
    || r.q_id.toLowerCase().includes(kw)
    || (r.tags||[]).join(" ").toLowerCase().includes(kw)
    || (r.splits||[]).join(" ").toLowerCase().includes(kw));
  if(!rows.length){ box.innerHTML = '<div class="empty">該当する問題がありません。</div>'; return; }
  let h = '<table><thead><tr><th>問題</th><th>タグ</th><th>問題グループ</th><th>確認状態</th></tr></thead><tbody>';
  rows.forEach(r=>{
    const stc = r.status==="要修正" ? "ng" : (r.status==="正しい" ? "ok" : "muted");
    h += `<tr><td><span class="qlink" data-qid="${esc(r.q_id)}" style="cursor:pointer;text-decoration:underline">${esc(r.q_id)}</span></td>`
      + `<td>${(r.tags||[]).map(t=>`<span class="chip">${esc(t)}</span>`).join("")}</td>`
      + `<td>${(r.splits||[]).map(t=>`<span class="chip">${esc(t)}</span>`).join("")}</td>`
      + `<td class="${stc}">${esc(r.status||"未確認")}</td></tr>`;
  });
  box.innerHTML = h + "</tbody></table>";
}
async function importTagsFile(){
  const f = el("ch-tag-file").files[0];
  if(!f){ el("ch-tag-status").textContent="CSVファイルを選択"; return; }
  const text = await f.text();
  // ★置き換えは消える側があるので、押した時点で必ず確認する（2026-08-25）
  const rep = el("ch-tag-replace") && el("ch-tag-replace").checked;
  if(rep && !confirm("CSV の内容で置き換えます。CSV に無いタグは消えます。よろしいですか？\n（直前の状態は tags.json.bak に退避されます）")) return;
  const d = await fetch("/api/tags/import",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({csv:text,dataset:dsp(),mode:rep?"replace":"merge"})}).then(r=>r.json());
  el("ch-tag-status").textContent = d.ok
    ? (rep ? `置き換え完了（追加 ${d.added} / 削除 ${d.removed||0}）` : `取込 ${d.added} 件（追加のみ）`)
    : "失敗";
  loadTags();
}


// ── split 定義（タグ同様に UI から問題サブセットを作る）──
async function loadSplits(){
  const d = await fetch("/api/splits?x=1"+dsq()).then(r=>r.json());
  const sp = d.splits || {};
  const names = Object.keys(sp);
  if(!names.length){ el("ch-splits").textContent = "split 未定義。"; return; }
  el("ch-splits").innerHTML = "定義済み split: " + names.map(n=>
    `<a href="#" data-sp="${esc(n)}" class="chip" style="cursor:pointer">${esc(n)}（${sp[n].length}問）</a>`).join("");
  el("ch-splits").querySelectorAll("a[data-sp]").forEach(a=>{
    a.onclick = async e=>{ e.preventDefault(); const n=a.dataset.sp;
      el("ch-split-name").value=n; el("ch-split-qids").value=(sp[n]||[]).join(", ");
      const y = await fetch("/api/splits/export?name="+encodeURIComponent(n)+dsq()).then(r=>r.json());
      const pre=el("ch-split-yaml"); pre.style.display="block";
      pre.textContent = "▼ 実行側の設定ファイルに貼り付ける用のテキスト\n"+y.yaml; };
  });
}
async function saveSplit(){
  const name = el("ch-split-name").value.trim();
  const qids = el("ch-split-qids").value;
  if(!name){ el("ch-split-status").textContent="split名を入力"; return; }
  const d = await fetch("/api/splits",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name:name,qids:qids,dataset:dsp()})}).then(r=>r.json());
  el("ch-split-status").textContent = d.ok ? `保存: ${d.name}（${d.n}問）` : ("失敗: "+(d.error||""));
  loadSplits();
}
async function delSplit(){
  const name = el("ch-split-name").value.trim();
  if(!name) return;
  await fetch("/api/splits/delete",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name:name,dataset:dsp()})}).then(r=>r.json());
  el("ch-split-status").textContent = `削除: ${name}`;
  el("ch-split-yaml").style.display="none";
  loadSplits();
}

async function importSplitsSample(){
  el("ch-split-status").textContent = "サンプルsplit取込中…";
  const text = await fetch("/api/splits/sample.csv?x=1"+dsq()).then(r=>r.text());
  const d = await fetch("/api/splits/import",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({csv:text,dataset:dsp()})}).then(r=>r.json());
  el("ch-split-status").textContent = d.ok ? `split ${d.added} 件取込` : ("失敗: "+(d.error||""));
  loadSplits();
}
async function importSplitsFile(){
  const f = el("ch-split-file").files[0];
  if(!f){ el("ch-split-status").textContent="CSVファイルを選択"; return; }
  const text = await f.text();
  const d = await fetch("/api/splits/import",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({csv:text,dataset:dsp()})}).then(r=>r.json());
  el("ch-split-status").textContent = d.ok ? `split ${d.added} 件取込` : "失敗";
  loadSplits();
}

// ── ③ CSV/印刷 ──
function showToolbar(){ const t=el("ch-toolbar"); if(t) t.style.display="flex"; }
// ── ① 問題ビューア ＋ ② 確認ワークフロー ──
async function openQuestion(qid){
  const modal=el("qv-modal"), body=el("qv-body");
  body.innerHTML="読み込み中…"; modal.style.display="flex";
  const cfgQ = sel().length ? "&configs="+encodeURIComponent(sel().join(",")) : "";
  let d;
  try { d = await fetch(`/api/question?qid=${encodeURIComponent(qid)}${dsq()}${cfgQ}`).then(r=>r.json()); }
  catch(e){ body.innerHTML="取得エラー"; return; }
  if (d.error){ body.innerHTML=`<p class="empty">${esc(d.error)}</p>`; return; }
  const gold = new Set((Array.isArray(d.gold)?d.gold:[]).map(x=>String(x).toLowerCase()));
  let h = `<h3 style="margin-top:0">問題 ${esc(qid)}</h3>`;
  if ((d.images||[]).length)
    h += '<div class="qv-imgs">'+d.images.map((s,i)=>`<img src="${s}" alt="画像${i+1}" title="クリックで拡大">`).join("")+'</div>';
  else h += '<p class="muted">（画像は表示できません。問題ディレクトリ未設定の可能性があります）</p>';
  h += `<div class="qv-stem">${esc(d.stem||"")}</div>`;
  if ((d.choices||[]).length){
    h += '<ul class="qv-choices">'+d.choices.map(c=>{
      const g = gold.has(c.key.toLowerCase());
      return `<li class="${g?"gold":""}"><span class="k">${esc(c.key)}</span>${esc(c.text)}${g?' <b class="ok">← 正解</b>':''}</li>`;
    }).join("")+'</ul>';
  }
  h += `<p><b>正解（gold）:</b> ${esc(goldStr(d.gold))}</p>`;
  if ((d.answers||[]).length){
    h += '<table class="qv-ans"><thead><tr><th>構成</th><th>解答</th><th>正誤</th></tr></thead><tbody>';
    d.answers.forEach(a=>{ h+=`<tr><td>${esc(a.config_id)}</td><td>${esc(goldStr(a.pred))}</td>`
      +`<td>${a.correct===true?'<span class="ok">○</span>':a.correct===false?'<span class="ng">×</span>':'-'}</td></tr>`; });
    h += "</tbody></table>";
  }
  // ② 確認ワークフロー
  h += '<div class="qv-review"><b>正解ラベルの確認</b>'
    + '<div class="mg-row" style="margin-top:.5em"><span class="lbl">状態</span>'
    + '<select id="qv-status"><option>未確認</option><option>正しい</option><option>要修正</option></select>'
    + '<input id="qv-memo" placeholder="メモ（例：原典では e）" style="flex:1;min-width:12em">'
    + '<button id="qv-save" class="ghost">保存</button><span id="qv-rstatus" class="status"></span></div></div>';
  body.innerHTML = h;
  // 画像クリックで拡大
  body.querySelectorAll(".qv-imgs img").forEach(im=> im.onclick=()=>im.classList.toggle("zoom"));
  // 既存レビューを反映
  try {
    const rv = await fetch("/api/review?x=1"+dsq()).then(r=>r.json());
    const cur = (rv.review||{})[qid];
    if (cur){ el("qv-status").value=cur.status||"未確認"; el("qv-memo").value=cur.memo||""; }
  } catch(e){}
  el("qv-save").onclick = async ()=>{
    const b={q_id:qid,status:el("qv-status").value,memo:el("qv-memo").value,dataset:dsp()};
    await fetch("/api/review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());
    el("qv-rstatus").textContent="保存しました";
  };
}
function closeModal(){ el("qv-modal").style.display="none"; }

function initCh(){
  el("ch-print").onclick = ()=> window.print();
  el("qv-close").onclick = closeModal;
  el("qv-modal").onclick = e=>{ if(e.target===el("qv-modal")) closeModal(); };
  el("ch-out").addEventListener("click", e=>{
    const q = e.target.closest("[data-qid]");
    if(q){ e.preventDefault(); openQuestion(q.getAttribute("data-qid")); }
  });
  el("ch-split-import").onclick = importSplitsFile;
  el("ch-split-save").onclick = saveSplit;
  el("ch-split-del").onclick = delSplit;
  el("ch-tag-import").onclick = importTagsFile;
  const mgr = el("mg-reload"); if(mgr) mgr.onclick = loadQuestionList;
  const imp = el("mg-imp-run"); if(imp) imp.onclick = importQuestionsCsv;
  const mgf = el("mg-filter"); if(mgf) mgf.addEventListener("input", renderQuestionList);
  const mgl = el("mg-list"); if(mgl) mgl.addEventListener("click", e=>{
    const q = e.target.closest("[data-qid]");
    if(q){ e.preventDefault(); openQuestion(q.getAttribute("data-qid")); }
  });
  const mgd = el("mg-details");
  if(mgd) mgd.addEventListener("toggle", ()=>{ if(mgd.open && !QLIST.length) loadQuestionList(); });
  el("ch-sig").onclick = runSig;
  el("ch-slices").onclick = runSlices;
  el("ch-core").onclick = runCore;
  el("ch-suspects").onclick = runSuspects;
  el("ch-tag-add").onclick = ()=>setTag(true);
  el("ch-tag-del").onclick = ()=>setTag(false);
  el("ch-clear").onclick = ()=>{ SELECTED.clear(); renderConfigChips(); };
  el("ch-seltop").onclick = ()=>{ SELECTED = new Set(CONFIGS.slice(0,3).map(c=>c.config_id)); renderConfigChips(); };
  async function ensureLoaded(){ if(!LOADED){ LOADED=true; await loadDatasets(); loadConfigs(); loadTags(); loadSplits(); } }
  ["ch","manage"].forEach(t=>{
    const b = document.querySelector(`#toptabs .tab[data-tab="${t}"]`);
    if (b) b.addEventListener("click", ensureLoaded);
  });
}
initCh();
})();

/* ★選んだファイル名を表示する（2026-08-25）。
   ネイティブの入力欄を隠したため、「何を選んだか」を自前で出す必要がある。
   個別のハンドラを増やすと付け忘れるので、1か所で全部の .filepick を面倒みる。 */
(function () {
  "use strict";
  document.addEventListener("change", function (e) {
    const inp = e.target;
    if (!inp || inp.type !== "file" || !inp.closest(".filepick")) return;
    const out = document.getElementById(inp.id + "-name");
    if (out) out.textContent = inp.files && inp.files[0] ? inp.files[0].name : "未選択";
  });
})();
