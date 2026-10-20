"""
Benchforge — radbench から完全分離した独立ベンチマーク GUI（FastAPI）。

P1: 結果ブラウザ（最高ベンチマーク / 最新ベンチマーク[バージョン別タブ]）。
radbench の results/ を読み取り専用で参照するだけ（import なし・書き込みなし）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from pydantic import BaseModel

from app.config import (load_config, ollama_url, plans_dir, prompts_file,
                        results_dir)
from app.services import ollama_client as OC
from app.services import plan_store as PS
from app.services import prompt_store as PR
from app.services import results_reader as RR
from app.services import ensemble as EN
from app.services import nightly as NT

APP_ROOT = Path(__file__).resolve().parent.parent
CFG = load_config()

app = FastAPI(title="Benchforge")

# ── 高速化①: gzip 圧縮（テキスト系のみ・500B超）。画像/data-URI JSON も圧縮対象だが実害小 ──
from starlette.middleware.gzip import GZipMiddleware

#: 実況ストリーム（NDJSON）の経路。ここだけ圧縮しない。
STREAM_PATHS = ("/api/demo/run",)


class _GZipExceptStream(GZipMiddleware):
    """逐次表示するストリームだけ圧縮から外す gzip。

    ★2026-10-06 修正: GZipMiddleware は応答を溜めてから圧縮するため、
      ブラウザでは「モデル実演」の思考・本文が流れず、**最後にまとめて出て**いた
      （実測: 圧縮なしは5.2秒で初弾・以後連続、圧縮ありは38.6秒まで無音）。
      thinking 対応モデルを使う利用者は全員この影響を受ける。
      ★検証するときは curl に --compressed を付ける（付けないと直って見える）。
    """

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").startswith(STREAM_PATHS):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(_GZipExceptStream, minimum_size=500)


# ── 識別子の検査 ───────────────────────────────────────────────────
# run_id / config_id / name などは、そのままファイル名の一部になる。
# 長すぎる値を渡されると is_file() や glob() が OSError(ENAMETOOLONG) を投げ、
# 「存在しない」ではなく **500** になっていた（2026-09-15 の bug_hunt で検出）。
# ⇒ ファイルシステムに触れる前にここで弾き、400 を返す。
MAX_IDENT = 128


def _bad_ident(**fields) -> JSONResponse | None:
    """識別子が不正なら JSONResponse(400) を返す。正常なら None。"""
    for key, val in fields.items():
        if val is None:
            continue
        s = str(val)
        if len(s) > MAX_IDENT:
            return JSONResponse(
                {"error": f"{key} が長すぎます（{len(s)} 文字・上限 {MAX_IDENT}）"},
                status_code=400)
        if "\x00" in s or "/" in s or "\\" in s:
            return JSONResponse(
                {"error": f"{key} に使えない文字が含まれています"}, status_code=400)
    return None


@app.exception_handler(OSError)
def _oserror_handler(request: Request, exc: OSError):
    """保険。ファイル名長・不正パスの類は 500 ではなく 400 として返す。
    ⚠ 本来は各エンドポイントで _bad_ident により事前に弾く。ここは取りこぼし用。"""
    import errno
    if exc.errno in (errno.ENAMETOOLONG, errno.EINVAL, errno.ELOOP):
        return JSONResponse({"error": "指定された名前が不正です"}, status_code=400)
    raise exc


def _compute_asset_ver() -> str:
    """静的ファイル群(static/*)の名前+サイズ+mtimeから内容版を計算。
    どれか変わると版が変わる→テンプレの ?v= が変わりキャッシュが自動で切り替わる。"""
    import hashlib
    h = hashlib.sha1()
    sdir = APP_ROOT / "static"
    if sdir.is_dir():
        for f in sorted(sdir.iterdir()):
            if f.is_file():
                st = f.stat()
                h.update(f.name.encode()); h.update(str(st.st_size).encode()); h.update(str(int(st.st_mtime)).encode())
    return h.hexdigest()[:10]


ASSET_VER = _compute_asset_ver()

# ── 堅牢化⑤⑥⑦: Host検証 / クロスサイトPOST遮断 / リクエストサイズ上限 ──
_ALLOWED_HOSTS = set(CFG.get("server", {}).get("allowed_hosts", []) or [])
_MAX_BODY = int(CFG.get("server", {}).get("max_body_bytes", 5 * 1024 * 1024))  # 既定5MB


def _host_ok(host_header: str) -> bool:
    """Hostのホスト名部分がIPリテラル/localhost/許可リストのいずれかなら許可（DNSリバインディング対策）。"""
    import ipaddress
    h = (host_header or "").split(",")[0].strip()
    # ポート除去（IPv6 [::1]:8095 は rsplit で対応）
    name = h.rsplit(":", 1)[0] if h.count(":") <= 1 else h
    name = name.strip("[]")
    if not name:
        return False
    if name in ("localhost",) or name in _ALLOWED_HOSTS:
        return True
    try:
        ipaddress.ip_address(name)   # IPリテラルなら許可
        return True
    except ValueError:
        return False


from starlette.responses import PlainTextResponse


@app.middleware("http")
async def _guard(request: Request, call_next):
    # ⑤ Host 検証（DNSリバインディング）
    if not _host_ok(request.headers.get("host", "")):
        return PlainTextResponse("Forbidden: host not allowed", status_code=403)
    # ⑥ クロスサイトPOST遮断（同一オリジンUI・curl等ツールは通す。cross-site のみ拒否）
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        sfs = request.headers.get("sec-fetch-site")
        if sfs == "cross-site":
            return PlainTextResponse("Forbidden: cross-site request", status_code=403)
    # ⑦ リクエストサイズ上限（Content-Length で早期拒否）
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > _MAX_BODY:
        return PlainTextResponse("Payload too large", status_code=413)
    resp = await call_next(request)
    # 高速化②: /static/ に長期 immutable キャッシュ（?v=ASSET_VER でバスト）
    if request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp

app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")
templates = Jinja2Templates(directory=str(APP_ROOT / "templates"))

DISCLAIMER = ("研究・教育目的。出力は臨床診断の代替ではありません。"
              "ベンチマーク上の正答率は臨床的妥当性を意味しません。")


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "disclaimer": DISCLAIMER,
        "v": ASSET_VER,
    })


# ─── ラン監視（読み取り専用。runner には書き込まない＝分離原則） ──────────
def _systemctl_status() -> dict:
    """best-effort で radbench 系 user サービス/タイマーの状態を取る（失敗は無視）。"""
    import subprocess
    res: dict = {}
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", "radbench-trial.service"],
                           capture_output=True, text=True, timeout=3)
        res["radbench-trial"] = (r.stdout or "").strip() or "unknown"
    except Exception:  # noqa: BLE001
        pass
    try:
        r = subprocess.run(["systemctl", "--user", "list-timers", "--all", "radbench-phase2.timer"],
                           capture_output=True, text=True, timeout=3)
        for line in (r.stdout or "").splitlines():
            if "radbench-phase2" in line:
                res["phase2_timer_next"] = " ".join(line.split()[:3])
    except Exception:  # noqa: BLE001
        pass
    return res


def _read_tail(path: Path, max_bytes: int = 65536) -> list[str]:
    data = path.read_bytes()
    text = data[-max_bytes:].decode("utf-8", "replace")
    ls = text.splitlines()
    if len(data) > max_bytes and ls:
        ls = ls[1:]   # 先頭の途中行を捨てる
    return ls


def _run_log_path() -> Path:
    """監視対象のログ。まとめて実行(standalone)が書く先を優先して探す。
    ★以前は閲覧中の results_dir だけを見ていたため、standalone で実行しても
      **常に「run_trial.log が見つかりません」**になっていた（2026-08-07 修正）。"""
    cand = [results_dir(CFG) / "run_trial.log"]
    ds = _run_dataset()
    if ds:
        cand.insert(0, SA.dataset_dir(ds) / "results" / "run_trial.log")
    for p in cand:
        if p.is_file():
            return p
    return cand[0]


@app.get("/api/run/log")
def api_run_log(lines: int = 80):
    """ランのログ末尾を返す（読み取り専用・ライブ監視用）。"""
    p = _run_log_path()
    if not p.is_file():
        return {"exists": False, "lines": []}
    n = max(1, min(int(lines), 500))
    return {"exists": True, "lines": _read_tail(p)[-n:],
            "mtime": p.stat().st_mtime, "size": p.stat().st_size}


@app.get("/api/run/status")
def api_run_status():
    """ランの状態（ログ更新の鮮度から実行中/idle を推定）。書き込みは一切しない。"""
    import time as _t
    p = _run_log_path()
    out: dict = {"exists": p.is_file(), "services": _systemctl_status()}
    if p.is_file():
        age = _t.time() - p.stat().st_mtime
        tail = _read_tail(p, 4096)
        last = tail[-1] if tail else ""
        out["age_sec"] = round(age, 1)
        out["last_line"] = last
        out["state"] = ("idle" if "idle" in last
                        else ("実行中" if age < 150 else "不明（更新途絶）"))
    return out


# ★配布時に make_repo.py が版数を埋める。開発中のソースではプレースホルダのまま。
#   版数が配布物のどこにも出ておらず、実機検証をお願いした相手が
#   「いま何を触っているのか」を確認できなかったため追加（2026-08-14）。
APP_VERSION = "0.1.10"


def _version_label() -> str:
    return "開発版" if APP_VERSION.startswith("__") else APP_VERSION


@app.get("/api/meta")
def api_meta():
    rd = results_dir(CFG)
    trials = rd / "trials.jsonl"
    return {
        "version": _version_label(),
        "input_results_dir": str(rd),
        "trials_exists": trials.is_file(),
        "config_file": CFG.get("_config_file"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/benchmarks/best")
def api_best():
    """成績一覧。★上限なし（2026-08-07・利用者判断）。
    以前は上位50件で打ち切っていたが、打ち切られたことが画面から分からず
    「昔のランが消えた」ように見えた。全件返して並べ替えは画面側に任せる。"""
    return JSONResponse({"entries": RR.best(results_dir(CFG), limit=None)})


@app.get("/api/benchmark/detail")
def api_detail(run_id: str, config_id: str):
    """ベンチ1件の問題別詳細（trial 行）＋ 生出力(raw CSV) をマージして返す。"""
    bad = _bad_ident(run_id=run_id, config_id=config_id)
    if bad:
        return bad
    rd = results_dir(CFG)
    rows = RR.benchmark_detail(rd, run_id, config_id)
    raw = RR.raw_outputs(rd, run_id, config_id)
    pdet = RR.prompt_detail(rd, run_id, config_id)   # 注入プロンプト＋RAG（新ランのみ）
    meta = RR.load_question_meta(rd)                  # 問題属性（year/label + qtype/region/…）
    for d in rows:
        qid = d.get("q_id", "")
        d["raw_output"] = raw.get(qid, {}).get("raw_output", "")
        d["attrs"] = meta.get(qid, {})               # 問題別の属性ラベル（クリック時表示用）
        det = pdet.get(qid)
        if det:
            d["stages_detail"] = det.get("stages", [])
            d["wiki_pages"] = det.get("wiki_pages", [])
            d["wiki_used"] = det.get("wiki_used", False)
            d["aux_used"] = det.get("aux_used", False)
    arch = RR.config_info(rd, run_id, config_id).get("arch", [])
    sl = RR.slices(rows, meta)
    return {"run_id": run_id, "config_id": config_id, "rows": rows,
            "has_raw": bool(raw), "has_prompt": bool(pdet), "arch": arch, "slices": sl}


# ─── アンサンブル個体別内訳 ───────────────────────────────────────────
@app.get("/api/ensemble/list")
def api_ensemble_list():
    """メンバー2以上の構成一覧（プルダウン用）。"""
    return JSONResponse({"entries": EN.list_ensembles(results_dir(CFG))})


@app.get("/api/ensemble/breakdown")
def api_ensemble_breakdown(run_id: str, config_id: str):
    """指定アンサンブル構成の個体別正誤・動態（生ログから・読み取り専用）。"""
    bad = _bad_ident(run_id=run_id, config_id=config_id)
    if bad:
        return bad
    return JSONResponse(EN.breakdown(results_dir(CFG), run_id, config_id))


# ─── 夜間ラン設定（データ/モデル/タイマー・書込は data/nightly のみ） ─────
@app.get("/api/nightly/meta")
def api_nightly_meta():
    dd = APP_ROOT / "data"
    return {"models": OC.list_models(ollama_url(CFG)),
            "prompts": list(PR.load_all(prompts_file(CFG)).keys()),
            "plans": PS.list_plans(plans_dir(CFG)),
            "sample": NT.sample_info(dd),
            "saved": NT.load(dd)}


class NightlyBody(BaseModel):
    form: dict


@app.post("/api/nightly/preview")
def api_nightly_preview(body: NightlyBody):
    """フォームから tonight_plan 形式プラン＋OnCalendar を組み立てて返す（書込なし）。"""
    return NT.preview(body.form)


@app.post("/api/nightly/save")
def api_nightly_save(body: NightlyBody):
    """設定を data/nightly/ に保存（適用前の控え）。"""
    return NT.save(APP_ROOT / "data", body.form)


# ─── ラン実適用・制御（バックエンド差替可能・既定=radbench連携で従来どおり）──
from app.services import runner as RUN  # noqa: E402


def _run_dataset() -> str:
    """まとめて実行が対象にするデータセット名。
    ★以前は常に「最初のデータセット」だったため、画面で別のデータセットを選んで実行しても
      結果が別の場所に書かれ、成績一覧に出てこないことがあった（2026-08-07 修正）。
    優先順: 適用済みプランのデータセット → いま閲覧している results_dir のデータセット。"""
    try:
        p = SA.APP_ROOT / "data" / "standalone_run.json"
        if p.is_file():
            name = (json.loads(p.read_text(encoding="utf-8")) or {}).get("dataset") or ""
            if name:
                return name
    except (OSError, json.JSONDecodeError):
        pass
    rd = results_dir(CFG)
    if rd.name == "results" and (SA.DATASETS_ROOT.resolve() in rd.resolve().parents):
        return rd.parent.name
    return ""


def _runner_backend(dataset: str = ""):
    """config の runner.backend で実行系を選ぶ。
    既定 'standalone' = このアプリ内のランナー（配布版はこれだけで完結）。
    'external' = 別に用意した実行系(Linux+systemd)を操作する開発用の経路。
    ※以前の値 'radbench' も互換のため受け付ける。"""
    mode = (CFG.get("runner", {}).get("backend") or "standalone").lower()
    if mode not in ("external", "radbench"):   # 未知の値は安全側（自己完結）へ倒す
        return RB.StandaloneRunnerBackend(SA.dataset_dir(dataset or _run_dataset() or None),
                                          ollama_url=ollama_url(CFG),
                                          prompts=PR.load_all(prompts_file(CFG)))
    return RB.RadbenchSystemdBackend(results_dir(CFG))


@app.get("/api/runner/status")
def api_runner_status():
    return _runner_backend().status()


@app.post("/api/runner/apply")
def api_runner_apply(body: NightlyBody):
    """フォーム→プラン組立→バックエンドへ適用（radbench連携時はモデルをキー変換）。"""
    ds = (body.form or {}).get("dataset") or ""
    return _runner_backend(ds).apply(NT.assemble_plan(body.form), dataset=ds)


class ApplySavedBody(BaseModel):
    name: str
    # ① データ上書き（空文字は無視＝プラン既定）。なお radbench run_trial が実際に使うのは
    # split のみ（dataset_dir/label_path/answers_csv は paths.yaml 固定で無視される）。
    # プラン構築自体はエディタ側。
    split: str = ""
    dataset_dir: str = ""
    label_path: str = ""
    answers_csv: str = ""


@app.post("/api/runner/apply_saved")
def api_runner_apply_saved(body: ApplySavedBody):
    """プランエディタで保存したプラン(name)を読み、radbench plans/tonight_plan.json に適用
    （モデルをキー変換）。フォーム②③を使わず、保存済みプランをそのまま流す経路。
    ① に値があれば split/dataset_dir/label_path/answers_csv をそのプランに上書き
    （実効は split のみ。例: サンプル=dev_mini で軽く回す）。"""
    plan = PS.load_plan(plans_dir(CFG), body.name)
    if plan is None:
        return JSONResponse({"error": f"plan not found: {body.name}"}, status_code=404)
    if body.split.strip():
        plan["split"] = body.split.strip()
    for k in ("dataset_dir", "label_path", "answers_csv"):
        v = getattr(body, k, "").strip()
        if v:
            plan[k] = v
    # ★画面で選んだ問題セットを実行先として渡す（2026-08-07）。
    #   渡さないと常に「最初の問題セット」に書かれ、成績一覧に出てこない。
    ds = body.dataset_dir.strip()
    return _runner_backend(ds).apply(plan, dataset=ds)


class RunnerActionBody(BaseModel):
    """実行制御POSTの本文（現在は中身なし。将来の引数用に残す）。"""
    oncalendar: str = ""


@app.post("/api/runner/{action}")
def api_runner_action(action: str, body: RunnerActionBody | None = None):
    be = _runner_backend()
    if action == "start":
        return be.start()
    if action == "stop":
        return be.stop()
    if action == "pause":
        return be.pause()
    if action == "resume":
        return be.resume()
    # 予約実行(タイマー)は 2026-08-07 に画面から廃止。API も受け付けない。
    return JSONResponse({"error": f"unknown action: {action}"}, status_code=400)


# ─── プランエディタ(P2) ───────────────────────────────────────────────
# 構成は Benchforge 自前形式（stage.model は ollama タグ）。radbench には書かない。
STAGE_TYPES = ["direct_mc", "vision_findings", "reason", "vote"]


@app.get("/api/editor/meta")
def api_editor_meta():
    return {
        "models": OC.list_models(ollama_url(CFG)),
        "stage_types": STAGE_TYPES,
        "ollama_url": ollama_url(CFG),
        "plans_dir": str(plans_dir(CFG)),
    }


@app.get("/api/plans")
def api_plans():
    return {"plans": PS.list_plans(plans_dir(CFG))}


@app.get("/api/plans/{name}")
def api_plan_get(name: str):
    plan = PS.load_plan(plans_dir(CFG), name)
    if plan is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return plan


class PlanBody(BaseModel):
    name: str
    plan: dict


@app.post("/api/plans")
def api_plan_save(body: PlanBody):
    try:    # 名前の規則違反はエラー本文を画面に出す（黙って別名で保存しない）
        saved = PS.save_plan(plans_dir(CFG), body.name, body.plan)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"saved": saved}


@app.get("/api/prompts")
def api_prompts():
    return {"prompts": PR.load_all(prompts_file(CFG))}


class PromptBody(BaseModel):
    id: str
    system: str = ""
    user: str = ""
    description: str = ""


@app.post("/api/prompts")
def api_prompt_save(body: PromptBody):
    try:
        pid = PR.save_one(prompts_file(CFG), body.id, body.system, body.user, body.description)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"saved": pid}


class PromptRenameBody(BaseModel):
    old_id: str
    id: str
    system: str = ""
    user: str = ""
    description: str = ""


@app.post("/api/prompts/rename")
def api_prompt_rename(body: PromptRenameBody):
    """名前を変えて保存し、元を消す（＝改名）。
    ★以前は「IDを書き換えて保存」しか無く、元が残るのか置き換わるのか画面から分からなかった。
      利用者が『名前を変える』のか『別名で増やす』のかを選べるようにするための片割れ。"""
    try:
        pid = PR.save_one(prompts_file(CFG), body.id, body.system, body.user, body.description)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    removed = False
    if body.old_id and body.old_id != pid:
        removed = PR.delete_one(prompts_file(CFG), body.old_id)
    return {"saved": pid, "removed_old": removed,
            "note": "" if removed or not body.old_id or body.old_id == pid
                    else f"『{body.old_id}』は既定のプロンプトのため残しました。"}


@app.get("/api/compare")
def api_compare(a_run: str, a_cfg: str, b_run: str, b_cfg: str):
    """2構成のペア比較。問題ごとに A/B の正誤を突き合わせ、どちらが勝った/反転したかを返す。"""
    rd = results_dir(CFG)
    A = {r["q_id"]: r for r in RR.benchmark_detail(rd, a_run, a_cfg)}
    B = {r["q_id"]: r for r in RR.benchmark_detail(rd, b_run, b_cfg)}
    qs = sorted(set(A) | set(B), key=lambda q: RR._qnum(q))
    meta = RR.load_question_meta(rd)
    rows = []
    s = {"a_correct": 0, "b_correct": 0, "a_only": 0, "b_only": 0,
         "both": 0, "neither": 0, "n": 0}
    # McNemar 用: 両構成が答えた問題のみの不一致ペア（欠測を誤りにしない）
    pd_a = pd_b = n_paired = 0
    for q in qs:
        a, b = A.get(q), B.get(q)
        ac, bc = bool(a and a.get("correct")), bool(b and b.get("correct"))
        s["n"] += 1
        s["a_correct"] += ac
        s["b_correct"] += bc
        if ac and bc:
            s["both"] += 1; verdict = "both"
        elif ac and not bc:
            s["a_only"] += 1; verdict = "a"
        elif bc and not ac:
            s["b_only"] += 1; verdict = "b"
        else:
            s["neither"] += 1; verdict = "neither"
        if a is not None and b is not None:   # 両者が解いた問題のみ McNemar に算入
            n_paired += 1
            if ac and not bc:
                pd_a += 1
            elif bc and not ac:
                pd_b += 1
        rows.append({"q_id": q, "gold": (a or b or {}).get("gold"),
                     "a_pred": (a or {}).get("pred"), "a_correct": ac,
                     "b_pred": (b or {}).get("pred"), "b_correct": bc, "verdict": verdict,
                     # 問題ごとの属性（年・ラベル・領域など）。どんな問題で差が出たかを表で見るため
                     "attrs": meta.get(q, {})})
    stats = RR.compare_stats(s["a_correct"], s["b_correct"], s["n"], pd_a, pd_b, n_paired)
    slice_stats = RR.paired_slice_stats(A, B, meta)
    return {"rows": rows, "summary": s, "stats": stats, "slices": slice_stats,
            # 各構成が「どんなモデルを、どの順で使うか」。比較の前提として画面に出す
            "a_arch": RR.config_info(rd, a_run, a_cfg).get("arch", []),
            "b_arch": RR.config_info(rd, b_run, b_cfg).get("arch", []),
            "a": {"run": a_run, "cfg": a_cfg}, "b": {"run": b_run, "cfg": b_cfg}}


# ─── 特性評価（失敗分析・誤ラベル疑い・Holm有意差・タグ）P4 ────────────
from app.services import characterize as CH


def _cfg_list(configs: str) -> list[str]:
    return [c for c in (configs or "").split(",") if c.strip()]


# dataset="" or "radbench" = radbench results/、それ以外 = データセット/<name>
# ─── 問題ビューア（画像つき誤答レビュー）────────────────────────────
from app.services import question_view as QV
from app.config import questions_dir as _questions_dir


@app.get("/api/question")
def api_question(qid: str, dataset: str = "", configs: str = "", since: str = "2026-07-13"):
    """1問の本文・選択肢・画像（data-URI）＋ gold ＋ 指定構成の解答/正誤を返す。"""
    try:
        qv = QV.get_question(qid, dataset, _questions_dir(CFG))
    except QV.QuestionFileError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if qv is None:
        return JSONResponse({"error": f"問題 '{qid}' が見つかりません（問題ディレクトリ未設定の可能性）"}, status_code=404)
    per, _ = CH.merged_results(results_dir(CFG), since, dataset)
    want = _cfg_list(configs)
    gold = None
    answers = []
    for c in sorted(per):
        row = per[c].get(qid)
        if not row:
            continue
        if gold is None:
            gold = row.get("gold")
        if not want or c in want:
            answers.append({"config_id": c, "pred": row.get("pred"),
                            "correct": row.get("correct")})
    # 選択した構成を先頭・正答率順は不問（呼び出し側が configs を渡す）
    return {**qv, "gold": gold, "answers": answers}


@app.get("/api/characterize/configs")
def api_ch_configs(since: str = "2026-07-13", dataset: str = ""):
    """マージ後に利用可能な構成一覧（正答率順・q_id横断マージ後の採点数つき）。"""
    return {"configs": CH.config_list(results_dir(CFG), since, dataset)}


@app.get("/api/characterize/slices")
def api_ch_slices(configs: str, baseline: str = "", since: str = "2026-07-13", dataset: str = ""):
    return CH.attribute_slices(results_dir(CFG), APP_ROOT, _cfg_list(configs), baseline, since, dataset)


@app.get("/api/characterize/hardcore")
def api_ch_hardcore(configs: str, since: str = "2026-07-13", dataset: str = ""):
    return CH.hard_core(results_dir(CFG), APP_ROOT, _cfg_list(configs), since, dataset)


@app.get("/api/characterize/rescue")
def api_ch_rescue(config: str, baseline: str, since: str = "2026-07-13", dataset: str = ""):
    return CH.rescue_break(results_dir(CFG), APP_ROOT, config, baseline, since, dataset)


@app.get("/api/characterize/suspects")
def api_ch_suspects(configs: str = "", ref: str = "arbiter_ff", min_pb: float = -0.05,
                    since: str = "2026-07-13", dataset: str = ""):
    return CH.mislabel_suspects(results_dir(CFG), ref_config=ref, min_pb=min_pb,
                                since=since, configs=_cfg_list(configs) or None, dataset=dataset)


from app.services import effective_votes as EV


from app.services import selection_bias as SB


@app.get("/api/selection_bias")
def api_selection_bias(configs: str, baseline: str = "", since: str = "2026-07-13",
                       dataset: str = "", n_boot: int = 1000):
    """選抜バイアス（勝者の呪い）の診断（読取専用）。"""
    return SB.analyze(results_dir(CFG), _cfg_list(configs), baseline, since, dataset, n_boot)


@app.get("/api/effective_votes")
def api_effective_votes(configs: str, since: str = "2026-07-13", dataset: str = "",
                        n_boot: int = 1000):
    """実効独立票 n_eff（選んだ構成群の誤り相関から算出・読取専用）。"""
    return EV.analyze(results_dir(CFG), _cfg_list(configs), since, dataset, n_boot)


@app.get("/api/characterize/significance")
def api_ch_significance(configs: str, since: str = "2026-07-13", dataset: str = ""):
    return CH.significance_holm(results_dir(CFG), _cfg_list(configs), since, dataset)


@app.get("/api/tags")
def api_tags_get(dataset: str = ""):
    tags = CH.load_tags(APP_ROOT, dataset)
    return {"tags": tags, "names": CH.all_tag_names(tags)}


class TagBody(BaseModel):
    q_id: str
    tag: str
    add: bool = True
    dataset: str = ""


@app.post("/api/tags")
def api_tags_set(body: TagBody):
    tags = CH.set_tag(APP_ROOT, body.q_id, body.tag, body.add, body.dataset)
    return {"ok": True, "tags": tags, "names": CH.all_tag_names(tags)}


class TagImportBody(BaseModel):
    csv: str = ""       # CSV本文（q_id,tag）。貼り付け or ファイル読込の中身
    path: str = ""      # 代わりにファイルパス（Benchforge/ 相対）。データセット/<ds>/inbox/tags_sample.csv 等
    dataset: str = ""
    # ★"merge"(既定・追加のみ) / "replace"(CSV の内容で置き換える＝書き出して編集して戻す往復用)
    #   replace は破壊的なので、サービス側で .bak に退避してから書き換える。
    mode: str = "merge"


def _safe_app_path(rel: str) -> Path | None:
    """APP_ROOT 配下の相対パスだけを許可（絶対パス・../ 脱出を拒否）。範囲外は None。"""
    try:
        p = (APP_ROOT / rel).resolve()
        root = APP_ROOT.resolve()
        if p == root or root in p.parents:
            return p
    except OSError:
        pass
    return None


@app.post("/api/tags/import")
def api_tags_import(body: TagImportBody):
    """CSV(q_id,tag) からタグを一括取込。csv本文 or path を受ける。data/tags/<dataset>.json にマージ。"""
    if body.path:
        p = _safe_app_path(body.path)
        if p is None:
            return JSONResponse({"ok": False, "error": "許可されないパスです"}, status_code=403)
        return CH.import_tags_file(APP_ROOT, p, body.dataset, body.mode)
    return CH.import_tags_csv(APP_ROOT, body.csv or "", body.dataset, body.mode)


@app.get("/api/tags/sample.csv")
def api_tags_sample_csv(dataset: str = ""):
    """タグCSVの記入例（指定データセットの tags_sample.csv）をダウンロード。書式の見本。"""
    fp = SA.dataset_dir(dataset or None) / "inbox" / "tags_sample.csv"
    text = fp.read_text(encoding="utf-8") if fp.is_file() else "q_id,tag\ns001,画像1枚;サンプル\n"
    return StreamingResponse(iter([text]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=tags_sample.csv"})


# ─── split（問題サブセット）管理：タグ同様に UI から定義（data/splits/<dataset>.json）──
@app.get("/api/splits")
def api_splits_get(dataset: str = ""):
    return {"splits": CH.load_splits(APP_ROOT, dataset)}


class SplitBody(BaseModel):
    name: str
    qids: str = ""      # カンマ/空白/改行区切りの q_id 羅列
    dataset: str = ""


@app.post("/api/splits")
def api_splits_set(body: SplitBody):
    return CH.save_split(APP_ROOT, body.name, body.qids, body.dataset)


@app.post("/api/splits/delete")
def api_splits_delete(body: SplitBody):
    return CH.delete_split(APP_ROOT, body.name, body.dataset)


@app.get("/api/splits/export")
def api_splits_export(name: str, dataset: str = ""):
    """radbench paths.yaml の split_qids に貼れる YAML 断片（Benchforge は radbench に書かない→手貼り用）。"""
    return {"name": name, "yaml": CH.split_yaml_snippet(APP_ROOT, name, dataset)}


@app.post("/api/splits/import")
def api_splits_import(body: TagImportBody):
    """CSV(split,q_id) から split を一括登録。csv本文 or path。data/splits/<dataset>.json にマージ。"""
    if body.path:
        p = _safe_app_path(body.path)
        if p is None:
            return JSONResponse({"ok": False, "error": "許可されないパスです"}, status_code=403)
        return CH.import_splits_file(APP_ROOT, p, body.dataset, body.mode)
    return CH.import_splits_csv(APP_ROOT, body.csv or "", body.dataset, body.mode)


# ─── 正解ラベルの確認ワークフロー（誤ラベル疑いのレビュー）──────────────
@app.get("/api/review")
def api_review_get(dataset: str = ""):
    return {"review": CH.load_review(APP_ROOT, dataset)}


class ReviewBody(BaseModel):
    q_id: str
    status: str = "未確認"
    memo: str = ""
    dataset: str = ""


@app.post("/api/review")
def api_review_set(body: ReviewBody):
    rv = CH.set_review(APP_ROOT, body.q_id, body.status, body.memo, body.dataset)
    return {"ok": True, "review": rv}


@app.get("/api/review/export.csv")
def api_review_export(dataset: str = "", only_fix: int = 0):
    text = CH.review_csv(APP_ROOT, dataset, only_fix=bool(only_fix))
    fn = "label_review_fix.csv" if only_fix else "label_review.csv"
    return StreamingResponse(iter([text]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={fn}"})


# ─── 問題の管理: 一覧と「いまの状態」の書き出し ───────────────────────
def _all_qids(dataset: str = "") -> list[str]:
    """その問題セットの問題番号を全部返す。
    ★以前は「記入例CSV」しか出せず、いま何に何が付いているか分からなかった。
      利用者が編集して取り込み直せるよう、**現在の状態そのもの**を出せるようにする。"""
    if dataset:
        try:
            return SA.list_questions(SA.dataset_dir(dataset))
        except OSError:
            return []
    return RR.all_qids(results_dir(CFG))


@app.get("/api/questions/list")
def api_questions_list(dataset: str = ""):
    """問題の一覧＋いま付いているタグ・グループ・確認状態（画面の一覧表示用）。"""
    tags = CH.load_tags(APP_ROOT, dataset or None)
    splits = CH.load_splits(APP_ROOT, dataset or None)
    review = CH.load_review(APP_ROOT, dataset or None)
    bysplit: dict[str, list[str]] = {}
    for name, qids in (splits or {}).items():
        for q in qids:
            bysplit.setdefault(q, []).append(name)
    qids = _all_qids(dataset)
    return {"questions": [{"q_id": q, "tags": tags.get(q, []), "splits": bysplit.get(q, []),
                           "status": (review.get(q) or {}).get("status", ""),
                           "memo": (review.get(q) or {}).get("memo", "")} for q in qids],
            "n": len(qids)}


def _csv_response(text: str, filename: str):
    # ファイル名は ASCII のみ（日本語だと latin1 で落ちて 500 になる・既知の落とし穴）
    return StreamingResponse(iter(["\ufeff" + text]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.get("/api/tags/export.csv")
def api_tags_export(dataset: str = ""):
    """いま付いているタグを、取り込みと同じ書式(q_id,tag)で書き出す（編集して戻せる）。"""
    import csv as _csv
    import io
    buf = io.StringIO(); w = _csv.writer(buf); w.writerow(["q_id", "tag"])
    tags = CH.load_tags(APP_ROOT, dataset or None)
    for q in _all_qids(dataset):
        w.writerow([q, ";".join(tags.get(q, []))])
    return _csv_response(buf.getvalue(), "tags_current.csv")


@app.get("/api/splits/export.csv")
def api_splits_export(dataset: str = ""):
    """いまの問題グループを、取り込みと同じ書式(split,q_id)で書き出す。"""
    import csv as _csv
    import io
    buf = io.StringIO(); w = _csv.writer(buf); w.writerow(["split", "q_id"])
    for name, qids in sorted((CH.load_splits(APP_ROOT, dataset or None) or {}).items()):
        w.writerow([name, ";".join(qids)])
    return _csv_response(buf.getvalue(), "splits_current.csv")


@app.get("/api/questions/import_template.csv")
def api_questions_import_template():
    """CSV の記入見本。Excel で開けるよう BOM 付き UTF-8 で返す。"""
    from app.services import question_import as QI
    return _csv_response(QI.TEMPLATE, "questions_template.csv")


@app.post("/api/questions/import_csv")
async def api_questions_import_csv(request: Request, dataset: str = "", overwrite: int = 0):
    """CSV（Excel で作った表）を読み、問題ファイル(YAML)と正解一覧を作る。
    ★本文をバイト列で受ける。ブラウザ側で文字列にすると、Excel 既定の Shift-JIS が
      その時点で化けてしまい、こちらでは直せなくなるため。"""
    from app.services import question_import as QI
    raw = await request.body()
    if not raw:
        return JSONResponse({"error": "ファイルが空です。"}, status_code=400)
    text, enc = QI.decode(raw)
    items, warn = QI.parse(text)
    if not items:
        return JSONResponse({"error": " ".join(warn) or "取り込めませんでした。",
                             "encoding": enc}, status_code=400)
    ds = SA.dataset_dir(dataset or _run_dataset() or None)
    res = QI.write(ds, items, overwrite=bool(overwrite))
    return {"ok": True, "encoding": enc, "dataset": ds.name, "warnings": warn, **res}


@app.get("/api/questions/export.csv")
def api_questions_export(dataset: str = ""):
    """問題一覧（タグ・グループ・確認状態つき）を書き出す。見るため／配るため。"""
    import csv as _csv
    import io
    d = api_questions_list(dataset)
    buf = io.StringIO(); w = _csv.writer(buf)
    w.writerow(["q_id", "tag", "問題グループ", "確認状態", "メモ"])
    for r in d["questions"]:
        w.writerow([r["q_id"], ";".join(r["tags"]), ";".join(r["splits"]), r["status"], r["memo"]])
    return _csv_response(buf.getvalue(), "questions.csv")


@app.get("/api/splits/sample.csv")
def api_splits_sample_csv(dataset: str = ""):
    """split CSV の記入例をダウンロード（指定データセットの splits_sample.csv・無ければ汎用例）。"""
    fp = SA.dataset_dir(dataset or None) / "inbox" / "splits_sample.csv"
    # ★汎用例は dev/test にする（2026-08-25）。"my_group" だとタグと同じ「分析の軸」に見えるが、
    #   split は**解かせる範囲を決める実行の単位**であり、dev/test に分ける規律が本体である。
    text = (fp.read_text(encoding="utf-8") if fp.is_file()
            else "split,q_id\ndev,s001;s002\ntest,s003\n")
    return StreamingResponse(iter([text]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=splits_sample.csv"})


# ─── スタンドアロン（配布版の自前データ経路・radbench非依存）P5 ────────
from app.services import standalone as SA


@app.get("/api/datasets")
def api_datasets():
    """問題セット一覧（データセット/ 直下のサブフォルダ）。
    ★あわせて「いま設定が指しているのはどれか」を返す（2026-08-07）。
      以前は画面のどこにも「config で場所を指定する」ことが書かれておらず、
      設定を開かない利用者には自分がどのデータを見ているのか分からなかった。
      current が空 = 設定が データセット/ の外を指している（開発者の使い方）。"""
    names = SA.list_datasets()
    rd = results_dir(CFG).resolve()
    current = ""
    try:
        if rd.name == "results" and SA.DATASETS_ROOT.resolve() in rd.parents:
            current = rd.parent.name
    except OSError:
        pass
    return {"datasets": names, "current": current, "external": not current,
            "samples": [n for n in names if n != "your_data"],
            "root": SA.DATASETS_ROOT.name}


@app.get("/api/standalone/stats")
def api_sa_stats(dataset: str = ""):
    """指定した自前 dataset だけで統計を計算（radbench results/ 非依存）。"""
    return SA.compute_stats(SA.dataset_dir(dataset or None))


@app.get("/api/standalone/export.csv")
def api_sa_export(dataset: str = ""):
    csv_text = SA.stats_csv(SA.dataset_dir(dataset or None))
    fn = f"benchforge_{dataset or 'dataset'}_stats.csv"
    return StreamingResponse(iter([csv_text]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={fn}"})


from app.services import runner_backend as RB


@app.get("/api/standalone/backends")
def api_sa_backends():
    """利用可能なランナーバックエンド一覧（配布/独立版の実行系抽象化）。"""
    return {"backends": [
        {"name": RB.RadbenchSystemdBackend.name, "os": "Linux+systemd", "status": "実装済(現行の夜間ランタブ)"},
        {"name": RB.StandaloneRunnerBackend.name, "os": "OS非依存",
         "status": "スタブ(配線のみ・実解答=_solveがGPU要で未実装)"},
    ]}


class SARunBody(BaseModel):
    config_id: str = "stub_runner"
    dataset: str = ""
    stub: bool = True         # True=配線確認(GPU不要)。False=自前 ollama で実解答(GPU要)
    plan: str = ""            # stub=False 時: 保存プラン名（先頭 config の stages を使う）


@app.post("/api/standalone/run")
def api_sa_run(body: SARunBody):
    """独立ランナーを実行: IN-BOX を読み、その自前データの results/trials.jsonl を書く。
    stub=True: ダミー解答で配線のみ確認（GPU不要）。
    stub=False: 保存プランの構成を自前 ollama で実解答（GPU使用）。"""
    ds = SA.dataset_dir(body.dataset or None)
    if body.stub:
        return RB.StandaloneRunnerBackend(ds).run(
            config_id=body.config_id or "stub_runner", use_stub=True)
    # 実解答: 保存プランの先頭 config の stages を使う
    plan = PS.load_plan(plans_dir(CFG), body.plan) if body.plan else None
    if not plan or not (plan.get("configs") or []):
        return JSONResponse({"ok": False, "error": "プラン(configs)が見つかりません。stub=False には plan 名が必要です"},
                            status_code=400)
    cfg0 = plan["configs"][0]
    stages = cfg0.get("stages") or []
    backend = RB.StandaloneRunnerBackend(ds, ollama_url=ollama_url(CFG),
                                         prompts=PR.load_all(prompts_file(CFG)))
    return backend.run(config_id=cfg0.get("config_id") or body.config_id or "run",
                       use_stub=False, stages=stages)


@app.get("/api/significance")
def api_significance(dataset_epoch: str = "", wiki_version: str = "", split: str = ""):
    """指定 version の全構成を集め、各正答率+CI と「最良構成との McNemar」を返す（§12 同一version内）。"""
    rd = results_dir(CFG)
    ent = RR.aggregate_benchmarks(rd)
    # version 指定があれば絞り込み、無ければ最も構成数の多い version を自動採用
    def vkey(e):
        return (e.get("dataset_epoch"), e.get("wiki_version"), e.get("split"))
    if dataset_epoch or wiki_version or split:
        ent = [e for e in ent if (not dataset_epoch or e.get("dataset_epoch") == dataset_epoch)
               and (not wiki_version or e.get("wiki_version") == wiki_version)
               and (not split or e.get("split") == split)]
    else:
        from collections import Counter
        if ent:
            top_v = Counter(vkey(e) for e in ent).most_common(1)[0][0]
            ent = [e for e in ent if vkey(e) == top_v]
    # config_id ごとに最高正答率の run を1つ採用（重複ラン排除）
    best_per_cfg: dict = {}
    for e in ent:
        c = e["config_id"]
        if c not in best_per_cfg or e["accuracy"] > best_per_cfg[c]["accuracy"]:
            best_per_cfg[c] = e
    refs = [{"run_id": e["run_id"], "config_id": e["config_id"]} for e in best_per_cfg.values()]
    matrix = RR.significance_matrix(rd, refs)
    version = (ent[0]["version"] if ent else "")
    return {"version": version, **matrix}


# ─── モデル実演(P3) ─────────────────────────────────────────────────
class _Default(dict):
    def __missing__(self, k):  # 未指定プレースホルダは空文字
        return ""


def _fill(template: str, **kw) -> str:
    return (template or "").format_map(_Default(**kw))


_VISION_STAGES = ("direct_mc", "vision_findings")
_ANSWER_STAGES = ("direct_mc", "reason")


# ★解答の抽出規則は solver.py の1か所だけに置く（2026-08-14）。
#   ここに同じ正規表現の写しがあり、片方だけ直すと「まとめて実行」と「個別実行」で
#   採点が食い違う状態だった（Win 実機検証の指摘）。
from app.services.solver import extract_answer as _extract_answer


class DemoBody(BaseModel):
    model: str = ""
    prompt_id: str = ""
    stages: list[dict] = []     # 多段: [{type,model,prompt_id,think,num_ctx}…]。空なら単段
    question: str = ""
    captions: str = ""
    images: list[str] = []      # data-URI or 生 base64
    think: str = "default"      # default/true/false
    temperature: float = 0.0
    num_ctx: int | None = None


@app.get("/api/demo/sample")
def api_demo_sample(name: str = "s001", dataset: str = ""):
    """同梱のサンプル問題を返す（画像は data-URI 化）。"""
    import base64
    import json as _j
    bad = _bad_ident(name=name, dataset=dataset)
    if bad:
        return bad
    sdir = plans_dir(CFG).parent / "samples" / name
    meta_fp = sdir / "sample.json"
    if not meta_fp.is_file():
        # フォールバック: 独立版の同梱 IN-BOX(データセット の指定データセット) から読む
        sa = SA.demo_sample(name, SA.dataset_dir(dataset or None))
        if sa is not None:
            return sa
        return JSONResponse({"error": f"sample '{name}' not found"}, status_code=404)
    meta = _j.loads(meta_fp.read_text(encoding="utf-8"))
    images = []
    for fn in meta.get("images", []):
        fp = sdir / fn
        if fp.is_file():
            ext = fp.suffix.lstrip(".").lower() or "png"
            b64 = base64.b64encode(fp.read_bytes()).decode("utf-8")
            images.append(f"data:image/{ext};base64,{b64}")
    caps = meta.get("captions", [])
    cap_block = ""
    if caps:
        lines = ["【画像と説明（画像の順番に対応）】"]
        for i, c in enumerate(caps, 1):
            lines.append(f"  画像{i}: {c.get('caption', '')}")
        cap_block = "\n".join(lines)
    return {"id": meta.get("id", name), "title": meta.get("title", name),
            "question": meta.get("question", ""), "note": meta.get("note", ""),
            "gold": meta.get("gold", ""), "captions": cap_block, "images": images}


@app.get("/api/demo/samples")
def api_demo_samples():
    """実演タブのサンプル一覧（全データセット横断）。data/samples（既存）＋ 各データセットの IN-BOX。
    各サンプルに dataset を付す（読込時にその dataset で解決する）。"""
    out = []
    sdir = plans_dir(CFG).parent / "samples"
    if sdir.is_dir():
        for d in sorted(sdir.iterdir()):
            if (d / "sample.json").is_file():
                out.append({"name": d.name, "source": "data/samples", "dataset": ""})
    for ds in SA.list_datasets():
        for qid in SA.list_questions(SA.dataset_dir(ds)):
            out.append({"name": qid, "source": ds, "dataset": ds})
    return {"samples": out}


def _norm(s: str) -> str:
    import re as _re
    return _re.sub(r"[._:\-]", "", (s or "").lower())


def _key_to_tag(key: str, tags: list[str]) -> str:
    """radbench のモデルキー(gemma4_31b)を ollama タグ(gemma4:31b)へ正規化突合。"""
    k = _norm(key)
    for t in tags:
        nt = _norm(t)
        if nt == k or nt.startswith(k):
            return t
    return ""


def _first_llm_stage(stages: list[dict]) -> dict | None:
    for s in stages:
        if (s.get("type") if isinstance(s, dict) else s) in ("direct_mc", "reason", "vision_findings"):
            return s if isinstance(s, dict) else {"type": s}
    return None


@app.get("/api/demo/configs")
def api_demo_configs():
    """実演に読み込める構成を返す。
      plans: プランエディタ保存(Benchforge形式・ollamaタグ直) / best: 最高ベンチ(radbench結果→変換)。
    各要素は実演にそのまま適用できる {label, model, prompt_id, think, num_ctx, temperature, multi, note}。"""
    tags = [m["name"] for m in OC.list_models(ollama_url(CFG))]
    out = {"plans": [], "best": []}

    # 1) 保存プラン（Benchforge・ollamaタグ）
    for p in PS.list_plans(plans_dir(CFG)):
        doc = PS.load_plan(plans_dir(CFG), p["name"]) or {}
        for c in doc.get("configs", []):
            st = c.get("stages", [])
            llm = _first_llm_stage(st)
            if not llm:
                continue
            think = llm.get("think")
            multi = len(st) > 1
            out["plans"].append({
                "label": f"{p['name']} / {c.get('config_id', '')}",
                "model": llm.get("model") or "",
                "prompt_id": llm.get("prompt_id") or "",
                "think": "true" if think is True else "false" if think is False else "default",
                "num_ctx": llm.get("num_ctx"),
                "temperature": c.get("temperature", 0.0),
                "stages": st,            # 全ステージ(多段実演用・model は ollamaタグ)
                "multi": multi,
                "note": "多段構成（順次実行）" if multi else "",
            })

    # 2) 最高ベンチ（radbench結果→実演用に変換）
    for g in RR.best(results_dir(CFG)):
        models = g.get("models") or []
        key = models[0] if models else ""
        tag = _key_to_tag(key, tags)
        pids = g.get("prompt_ids") or {}
        prompt_id = next(iter(pids.values()), "") if isinstance(pids, dict) else ""
        # 新ログ(config_stages)があれば多段を忠実に再現（各stageのmodelキー→ollamaタグへ）
        cs = g.get("config_stages")
        mapped = None
        if cs:
            mapped = []
            for s in cs:
                s2 = dict(s)
                if s2.get("model"):
                    s2["model"] = _key_to_tag(s2["model"], tags) or s2["model"]
                mapped.append(s2)
            llm0 = _first_llm_stage(mapped)
            if llm0:
                tag = llm0.get("model") or tag
                prompt_id = llm0.get("prompt_id") or prompt_id
        multi = (len(mapped) > 1) if mapped else (len(g.get("stages") or []) > 1)
        # ★見分けが付く名前にする。version は同梱サンプルでも新規実行でも空なので、
        #   同じ構成を2回実行すると「model_A 0% ()」が2行並んで区別できなかった
        #   （Win 実機検証 2026-08-14）。version が空なら実行の識別子を出し、
        #   どちらも空なら括弧ごと省く。
        tail = g.get("version") or g.get("run_id") or ""
        out["best"].append({
            "label": f"{g['config_id']}  {g['accuracy']*100:.0f}%"
                     + (f"（{tail}）" if tail else ""),
            "model": tag,                       # 空ならフロントで現状維持＋注記
            "model_key": key,
            "prompt_id": prompt_id,
            **({"stages": mapped} if mapped else {}),
            "think": "default",                 # trials に think 記録なし→既定
            "num_ctx": None,
            "temperature": g.get("temperature", 0.0),
            "multi": multi,
            "note": ("" if tag else f"ollamaにタグ未検出({key})") + (" / 多段はLLM1段のみ" if multi else ""),
        })
    return out


@app.post("/api/demo/run")
async def api_demo_run(body: DemoBody, request: Request):
    """ステージ列を Benchforge 自前 ollama で順次実行し、ステージ別に思考/本文を NDJSON ストリーム。
    LLM(direct_mc/vision_findings/reason)と vote に対応。"""
    prompts = PR.load_all(prompts_file(CFG))
    base_url = ollama_url(CFG)
    imgs = [i.split(",")[-1] for i in body.images if i]
    temp = float(body.temperature)
    stages = body.stages or [{"type": "direct_mc", "model": body.model,
                              "prompt_id": body.prompt_id, "think": body.think,
                              "num_ctx": body.num_ctx}]
    # ★モデル未設定なら、入っているモデルで埋める（AI を一度も呼ばずに終わらせない）。
    #   同梱の構成は "model":"" で出荷しているため、ここが無いと初回体験が必ず失敗する。
    from app.services.solver import fill_missing_models as _fill_models
    stages, auto_tag = _fill_models(base_url, stages)

    def _produce():
        import json as _j
        from collections import Counter
        ctx = {"question": body.question, "captions": body.captions,
               "findings": "", "wiki": "", "aux": ""}
        cand: list[str] = []
        final = ""
        try:
            if auto_tag:
                yield _j.dumps({"type": "notice",
                                "text": f"この構成にはモデルが設定されていないため、"
                                        f"入っている {auto_tag} を使いました。"
                                        f"『構成の作成』で指定すると固定できます。"},
                               ensure_ascii=False) + "\n"
            for idx, st in enumerate(stages):
                stype = st.get("type")
                if stype == "vote":
                    voters = [c for c in cand if c]
                    ans = ""
                    if voters:
                        cnt = Counter(voters); top = max(cnt.values())
                        ans = next(v for v in voters if cnt[v] == top)
                    final = ans
                    yield _j.dumps({"stage_index": idx, "stage_type": "vote",
                                    "phase": "end", "answer": ans}, ensure_ascii=False) + "\n"
                    continue
                model = st.get("model")
                if not model:
                    yield _j.dumps({"stage_index": idx, "stage_type": stype,
                                    "note": "モデル未指定→スキップ"}, ensure_ascii=False) + "\n"
                    continue
                pid = st.get("prompt_id", "")
                if pid and pid not in prompts:
                    # ★存在しない prompt_id を黙って空プロンプトで実行すると、解答が空になるのに
                    #   エラーが出ず「成功したように見える」（2026-08-07 のバグ狩りで発見）
                    yield _j.dumps({"error": f"プロンプト『{pid}』が見つかりません。"
                                             f"『構成の作成』画面で選び直してください。"},
                                   ensure_ascii=False) + "\n"
                    return
                p = prompts.get(pid, {})
                system = p.get("system", "")
                user = _fill(p.get("user", "{question}"), **ctx)
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                if stype in _VISION_STAGES and imgs:
                    messages[-1]["images"] = imgs
                opts = {"temperature": temp}
                if st.get("num_ctx"):
                    opts["num_ctx"] = int(st["num_ctx"])
                thv = st.get("think")
                think = True if thv in (True, "true") else False if thv in (False, "false") else None
                yield _j.dumps({"stage_index": idx, "stage_type": stype, "model": model,
                                "phase": "start", "system": system, "user": user,
                                "injected": [k for k in ("findings", "wiki", "aux") if ctx.get(k)]},
                               ensure_ascii=False) + "\n"
                buf = ""
                for ev in OC.stream_chat(base_url, model, messages, opts, think):
                    if ev["thinking"]:
                        yield _j.dumps({"stage_index": idx, "thinking": ev["thinking"]}, ensure_ascii=False) + "\n"
                    if ev["content"]:
                        buf += ev["content"]
                        yield _j.dumps({"stage_index": idx, "content": ev["content"]}, ensure_ascii=False) + "\n"
                ans = ""
                if stype == "vision_findings":
                    ctx["findings"] = buf
                if stype in _ANSWER_STAGES:
                    ans = _extract_answer(buf); cand.append(ans); final = ans
                yield _j.dumps({"stage_index": idx, "stage_type": stype, "phase": "end",
                                "answer": ans}, ensure_ascii=False) + "\n"
            yield _j.dumps({"type": "final", "answer": final, "done": True}, ensure_ascii=False) + "\n"
        except Exception as e:  # noqa: BLE001
            yield _j.dumps({"error": _model_error(body.model or "（未指定）", e)},
                           ensure_ascii=False) + "\n"

    async def gen():
        # 別スレッドで _produce を回し、無出力ギャップ中はハートビートを送って
        # ブラウザ/中継の接続断（Safari "Load failed" 等）を防ぐ。
        import asyncio as _a
        import queue as _q
        import threading as _t
        out = _q.Queue(maxsize=64)
        stop = _t.Event()     # 「停止」で接続が切れたことを作業スレッドへ伝える

        def _worker():
            gen_in = _produce()
            try:
                for line in gen_in:
                    if stop.is_set():
                        break
                    out.put(line)
            except Exception as e:  # noqa: BLE001
                import json as _j
                out.put(_j.dumps({"error": str(e)}, ensure_ascii=False) + "\n")
            finally:
                gen_in.close()    # 推論中のモデルへの接続を閉じる＝実行を止める
                out.put(None)

        _t.Thread(target=_worker, daemon=True).start()
        last = _a.get_event_loop().time()
        try:
            while True:
                try:
                    item = out.get_nowait()
                except _q.Empty:
                    # ★2026-10-06 修正: 「停止」ボタンが見た目だけだった。
                    #   画面を止めても推論は裏で走り続け、次の実行と並走していた
                    #   （実測: 停止10秒後もスロットが掴まれたまま、次の問題の所要が2倍、
                    #     バッチの組み方が変わって解答まで変化）。
                    #   接続断を能動的に確認し、作業スレッドへ確実に伝える。
                    if await request.is_disconnected():
                        break
                    now = _a.get_event_loop().time()
                    if now - last > 8:
                        last = now
                        yield '{"hb":1}\n'   # heartbeat（フロントは無視）
                    await _a.sleep(0.2)
                    continue
                if item is None:
                    break
                last = _a.get_event_loop().time()
                yield item
        finally:
            stop.set()

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


# ─── まとめ(Forge): 期間指定で総論を生成・蓄積・参照 ─────────────────
def _summaries_dir() -> Path:
    return plans_dir(CFG).parent / "summaries"


def _summary_path() -> Path:        # strategist 既定（最新生成）の互換ポインタ
    return _summaries_dir() / "overview.md"


def _summary_file(name: str) -> Path:    # ディレクトリトラバーサル防止
    return _summaries_dir() / (Path(name).name + ".md")


def _list_summaries() -> list[dict]:
    import re as _re
    d = _summaries_dir()
    out = []
    if d.is_dir():
        for fp in sorted(d.glob("summary_*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            m = _re.search(r"period=(\w+) date=(\S+) by (\S+) n=(\d+)",
                           fp.read_text(encoding="utf-8")[:200])
            meta = {"name": fp.stem}
            if m:
                meta.update({"period": m.group(1), "date": m.group(2),
                             "model": m.group(3), "n_configs": int(m.group(4))})
            out.append(meta)
    return out


@app.get("/api/summary")
def api_summary(period: str = "all"):
    from app.services import summary_builder as SB
    stats = SB.build_stats(results_dir(CFG), period)
    fp = _summary_path()
    md = fp.read_text(encoding="utf-8") if fp.is_file() else ""
    updated = (datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds")
               if fp.is_file() else None)
    return {"markdown": md, "updated": updated, "period": period,
            "stats": stats["text"], "n_configs": stats["n_configs"],
            "saved": _list_summaries()}


@app.get("/api/summary/get")
def api_summary_get(name: str):
    bad = _bad_ident(name=name)
    if bad:
        return bad
    fp = _summary_file(name)
    if not fp.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"name": name, "markdown": fp.read_text(encoding="utf-8")}


class SummaryBody(BaseModel):
    model: str = ""
    period: str = "all"


@app.post("/api/summary/generate")
def api_summary_generate(body: SummaryBody):
    from app.services import summary_builder as SB
    stats = SB.build_stats(results_dir(CFG), body.period)
    if not stats["n_configs"]:
        return JSONResponse({"error": "対象期間に結果がありません"}, status_code=400)
    system, user = SB.build_prompt(stats["text"])
    model = body.model or "gemma4:12b"
    try:
        md = OC.chat(ollama_url(CFG), model,
                     [{"role": "system", "content": system}, {"role": "user", "content": user}],
                     options={"temperature": 0.2}, think=False, timeout=600)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": _model_error(model, e)}, status_code=400)
    now = datetime.now().isoformat(timespec="seconds")
    content = f"<!-- period={body.period} date={now} by {model} n={stats['n_configs']} -->\n" + md
    d = _summaries_dir()
    d.mkdir(parents=True, exist_ok=True)
    # ★period は要求由来の値。そのままファイル名にすると `../../` でアプリ外へ書ける
    #   （2026-08-07 のバグ狩りで発見。FileNotFoundError で 500 になっていた）。
    period_safe = NAMING.sanitize(body.period, "all")
    saved = d / f"summary_{period_safe}_{datetime.now().strftime('%Y%m%d-%H%M')}.md"
    saved.write_text(content, encoding="utf-8")          # 蓄積（日報/週報/月報相当）
    _summary_path().write_text(content, encoding="utf-8")  # 最新ポインタ（strategist既定）
    (d / "stats.md").write_text(stats["text"], encoding="utf-8")
    return {"markdown": content, "model": model, "period": body.period,
            "n_configs": stats["n_configs"], "stats": stats["text"],
            "saved": saved.stem, "list": _list_summaries()}


# ─── プラン提案(戦略家): 統計+総論を読み、次のプランを提案→良ければ保存 ──────
def _strategy_models() -> list[str]:
    return [m["name"] for m in OC.list_models(ollama_url(CFG)) if m.get("name")]


@app.get("/api/strategy/context")
def api_strategy_context(period: str = "all"):
    from app.services import strategist as ST
    models = _strategy_models()
    ctx = ST.build_context(results_dir(CFG), _summary_path(), models, period=period)
    return {"stats": ctx["stats_text"], "n_configs": ctx["n_configs"],
            "has_summary": bool(ctx["summary_md"]), "models": models,
            "summaries": _list_summaries()}


class ProposeBody(BaseModel):
    model: str = ""
    note: str = ""
    period: str = "all"
    summary_name: str = ""


@app.post("/api/strategy/propose")
def api_strategy_propose(body: ProposeBody):
    from app.services import strategist as ST
    summary_md = None
    if body.summary_name:
        fp = _summary_file(body.summary_name)
        if fp.is_file():
            summary_md = fp.read_text(encoding="utf-8")
    ctx = ST.build_context(results_dir(CFG), _summary_path(), _strategy_models(),
                           period=body.period, summary_md=summary_md)
    if not ctx["n_configs"]:
        return JSONResponse({"error": "結果がありません（trials.jsonl が空）"}, status_code=400)
    system, user = ST.build_prompt(ctx, body.note)
    model = body.model or "gemma4:12b"
    try:
        text = OC.chat(ollama_url(CFG), model,
                       [{"role": "system", "content": system}, {"role": "user", "content": user}],
                       options={"temperature": 0.4}, think=False, timeout=600)
    except Exception as e:  # noqa: BLE001
        # ★同じ型（生成の失敗）。原因を名指しして 400 で返す（"Internal Server Error" にしない）
        return JSONResponse({"error": _model_error(model, e)}, status_code=400)
    plan, perr = ST.parse_plan(text)
    return {"text": text, "plan": plan, "plan_error": perr, "model": model}


class SaveProposalBody(BaseModel):
    name: str
    plan: dict
    rationale: str = ""


@app.post("/api/strategy/save")
def api_strategy_save(body: SaveProposalBody):
    if not isinstance(body.plan, dict) or not body.plan.get("configs"):
        return JSONResponse({"error": "保存できる有効なプランがありません"}, status_code=400)
    try:
        saved = PS.save_plan(plans_dir(CFG), body.name, body.plan)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if body.rationale.strip():    # 提案理由も併せて残す（後から判断の根拠をたどれるように）
        d = plans_dir(CFG).parent / "strategy"
        d.mkdir(parents=True, exist_ok=True)
        # ★同じ型の穴（要求由来の値でファイル名を作る）。saved は validate 済みだが、
        #   将来の変更で崩れないよう、ここでも名前を固定する。
        (d / f"{NAMING.sanitize(str(saved), 'plan')}.md").write_text(body.rationale, encoding="utf-8")
    return {"saved": saved}


# ─── 環境チェック（はじめて動かす人向けの診断）────────────────────
from app.services import naming as NAMING
from app.services import setup_check as SC


def _model_error(model: str, e: Exception) -> str:
    """生成に失敗したときの案内。★「通信エラー」では利用者は動けないので原因を名指しする。"""
    msg = str(e)
    if "404" in msg or "not found" in msg.lower():
        return (f"モデル『{model}』が見つかりません。"
                f"先に次を実行して入れてください:  ollama pull {model}")
    if "400" in msg:
        return ("入力を受け付けられませんでした。画像が壊れているか、"
                "そのモデルが画像に対応していない可能性があります。")
    if "Connection" in msg or "refused" in msg or "URLError" in msg:
        return "推論エンジン（ollama）に接続できません。ollama が起動しているか確認してください。"
    return f"生成に失敗しました: {msg[:200]}"


@app.get("/api/setup_check")
def api_setup_check():
    """推論エンジン・モデル・画像対応・メモリ・結果の有無を診断して、次にやることを返す。
    ★何も自動で入れない・起動しない（利用者が選ぶ）。"""
    return SC.diagnose(ollama_url(CFG), results_dir(CFG))


@app.get("/healthz")
def healthz():
    return {"ok": True}
