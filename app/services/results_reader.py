"""
results_reader — radbench の results/ を「入力源」として読むだけ（読み取り専用・非依存）。

ベンチマーク1件の定義: 同一 (run_id, config_id, dataset_epoch, wiki_version, split) の
trials を集計した1構成の成績（正答率・採点数など）。trials.jsonl の行スキーマは radbench
側の出力契約に従う（run_id/config_id/correct/dataset_epoch/wiki_version/split など）。
"""
from __future__ import annotations

import json
import math
import re
from math import comb
from pathlib import Path


# ─── 統計（小サンプルの正答率比較。scipy 非依存） ──────────────────────
def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """正答率の Wilson スコア95%信頼区間。小Nでも端で破綻しない。"""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (max(0.0, c - h), min(1.0, c + h))


def mcnemar_exact_p(b: int, c: int) -> float:
    """対応あり2構成の McNemar 正確（二項）両側 p 値。b,c は不一致ペア数。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(k + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def load_question_meta(results_dir: Path) -> dict[str, dict]:
    """results/ の静的メタを {q_id: {year,label, qtype,region,...}} で読む。無ければ空。
    - question_meta.csv（radbench 出力）: year, label
    - question_attributes.csv（tools/question_attributes.py 出力）: qtype/region/n_select/n_images/stem_len
    どちらも分離原則で results/ の静的CSVとして読む（他アプリのコードに依存しない）。"""
    import csv
    meta: dict[str, dict] = {}
    p = Path(results_dir) / "question_meta.csv"
    if p.is_file():
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                meta[r["q_id"]] = {"year": r.get("year", ""), "label": r.get("label", "")}
    pa = Path(results_dir) / "question_attributes.csv"
    if pa.is_file():
        with open(pa, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                m = meta.setdefault(r["q_id"], {})
                for k in ("qtype", "region", "n_select", "n_images", "stem_len"):
                    if r.get(k):
                        m[k] = r[k]
    return meta


def _label_name(v) -> str | None:
    """ラベル値の表示名。
    ★以前は作者のデータの意味（値2=ある撮像法だけ 等）を直に書いていた（2026-08-07 に除去）。
      ラベル値の意味は**データセットごとに違う**ので、配布物では値をそのまま見せる。
      利用者が意味を持たせたければ、タグ機能で日本語の名前を付けられる。"""
    v = str(v or "").strip()
    return f"値{v}" if v else None

# スライス軸: (返却キー, ラベル, meta属性名 or None=特別扱い)
_ATTR_AXES = [
    ("by_qtype", "設問タイプ", "qtype"),
    ("by_region", "領域", "region"),
    ("by_nselect", "解答数", "n_select"),
    ("by_nimages", "画像枚数", "n_images"),
    ("by_stemlen", "臨床情報量", "stem_len"),
]


def slices(rows: list[dict], meta: dict[str, dict]) -> dict:
    """benchmark_detail の行を各属性軸で集計。年/ラベル + 設問タイプ/領域/解答数/画像枚数/情報量。"""
    from collections import defaultdict

    def agg(keyfn):
        g: dict = defaultdict(lambda: [0, 0])  # key -> [correct, scored]
        for r in rows:
            if r.get("correct") is None:
                continue
            k = keyfn(r)
            if not k:
                continue
            g[k][0] += 1 if r["correct"] else 0
            g[k][1] += 1
        return [{"key": k, "correct": v[0], "scored": v[1],
                 "acc": (v[0] / v[1]) if v[1] else 0.0,
                 "ci": list(wilson_ci(v[0], v[1]))}
                for k, v in sorted(g.items())]

    out = {}
    out["by_label"] = agg(lambda r: _label_name(
        meta.get(r.get("q_id", ""), {}).get("label", "")))

    # ★手ラベル不要で導出できる軸（2026-08-25 追加）。
    #   配布版では question_attributes.csv が無いのが普通で、以前はスライスが
    #   **何も出ず、しかも理由が分からなかった**（機能が無いのか壊れているのか区別できない）。
    #   ⇒ trials だけから確実に出せる「年」「解答数」は常に出す。
    def _year(r):
        m = re.match(r"^(\d{4})", str(r.get("q_id") or ""))
        return m.group(1) if m else None

    def _nsel(r):
        # meta に n_select があればそれを優先（作者側の定義を尊重）
        v = meta.get(r.get("q_id", ""), {}).get("n_select")
        if v:
            return v
        g = r.get("gold")
        if not isinstance(g, (list, tuple)) or not g:
            return None
        return "1つ選択" if len(g) == 1 else f"{len(g)}つ選択"

    out["by_year"] = agg(_year)
    out["by_nselect"] = agg(_nsel)

    for outkey, _title, attr in _ATTR_AXES:
        if outkey == "by_nselect":      # 上で導出済み
            continue
        out[outkey] = agg(lambda r, a=attr: meta.get(r.get("q_id", ""), {}).get(a))

    # ★人手（またはデータ提供者）の情報が要る軸のうち、データが無かったものを伝える。
    #   黙って消すと利用者は故障と区別できない（Benchforge の既知の失敗パターン）。
    need_csv = [title for outkey, title, _a in _ATTR_AXES
                if outkey != "by_nselect" and not out.get(outkey)]
    out["_missing"] = need_csv
    return out


def _sig_word(p: float) -> str:
    return "有意" if p < 0.05 else ("傾向" if p < 0.10 else "誤差圏")


def paired_slice_stats(A: dict, B: dict, meta: dict) -> dict:
    """ペア比較を年別・ラベル(モダリティ)別にスライスし、各スライスで McNemar を計算。
    A/B は {q_id: detail_row}。McNemar は両者が解いた問題のみ。"""
    from collections import defaultdict

    def slice_by(keyfn) -> list[dict]:
        g: dict = defaultdict(lambda: {"ac": 0, "an": 0, "bc": 0, "bn": 0,
                                       "da": 0, "db": 0, "np": 0})
        for q in set(A) | set(B):
            k = keyfn(q)
            if not k:
                continue
            a, b = A.get(q), B.get(q)
            s = g[k]
            if a is not None:
                s["an"] += 1; s["ac"] += 1 if a.get("correct") else 0
            if b is not None:
                s["bn"] += 1; s["bc"] += 1 if b.get("correct") else 0
            if a is not None and b is not None:
                s["np"] += 1
                ac, bc = bool(a.get("correct")), bool(b.get("correct"))
                if ac and not bc:
                    s["da"] += 1
                elif bc and not ac:
                    s["db"] += 1
        rows = []
        for k in sorted(g):
            s = g[k]
            p = mcnemar_exact_p(s["da"], s["db"])
            rows.append({"key": k,
                         "a_acc": (s["ac"] / s["an"]) if s["an"] else 0.0,
                         "b_acc": (s["bc"] / s["bn"]) if s["bn"] else 0.0,
                         "a_n": s["an"], "b_n": s["bn"],
                         "disc_a": s["da"], "disc_b": s["db"], "n_paired": s["np"],
                         "mcnemar_p": p, "sig": _sig_word(p)})
        return rows

    # ★slices() と同じ扱いを比較タブ側にも入れる（2026-08-25）。
    #   ⚠ 以前 slices() だけ直して比較タブを取り残した＝「同じ症状が複数経路にある」の再発。
    def _year(q):
        m = re.match(r"^(\d{4})", str(q or ""))
        return m.group(1) if m else None

    def _nsel(q):
        v = meta.get(q, {}).get("n_select")
        if v:
            return v
        g = (A.get(q) or B.get(q) or {}).get("gold")
        if not isinstance(g, (list, tuple)) or not g:
            return None
        return "1つ選択" if len(g) == 1 else f"{len(g)}つ選択"

    out = {
        "by_year": slice_by(_year),
        "by_label": slice_by(lambda q: _label_name(meta.get(q, {}).get("label", ""))),
        "by_nselect": slice_by(_nsel),
    }
    for outkey, _title, attr in _ATTR_AXES:
        if outkey == "by_nselect":
            continue
        out[outkey] = slice_by(lambda q, a=attr: meta.get(q, {}).get(a))
    out["_missing"] = [title for outkey, title, _a in _ATTR_AXES
                       if outkey != "by_nselect" and not out.get(outkey)]
    return out


def significance_matrix(results_dir: Path, refs: list[dict]) -> dict:
    """複数構成の有意差。各構成の正答率+CI、および「最良構成」との McNemar(対応あり)を返す。
    refs: [{run_id, config_id}, ...]（同一 version を想定）。"""
    data = []
    for r in refs:
        rows = {x["q_id"]: x for x in benchmark_detail(results_dir, r["run_id"], r["config_id"])}
        scored = [x for x in rows.values() if x.get("correct") is not None]
        cor = sum(1 for x in scored if x.get("correct"))
        data.append({"run_id": r["run_id"], "config_id": r["config_id"], "rows": rows,
                     "correct": cor, "scored": len(scored),
                     "acc": (cor / len(scored)) if scored else 0.0})
    if not data:
        return {"ref": None, "rows": []}
    data.sort(key=lambda d: d["acc"], reverse=True)
    # ★基準（最良）構成は「採点数が十分にあるもの」から選ぶ。
    #   単に正答率が最大の構成を基準にすると、**途中で止まった小さなランが基準を乗っ取る**。
    #   実例: 12問しか解いていない構成が 83.3% で基準になり、**表全体の McNemar が
    #   その12問だけで計算されていた**（2026-08-06 発見）。
    #   条件 = 最大採点数の半分以上、かつ 30問以上。該当が無ければ採点数が最大のものを基準にする。
    max_scored = max(d["scored"] for d in data)
    floor = max(30, max_scored // 2)
    eligible = [d for d in data if d["scored"] >= floor]
    ref = eligible[0] if eligible else max(data, key=lambda d: d["scored"])
    out_rows = []
    for d in data:
        da = db = npr = 0
        for q, ar in ref["rows"].items():
            br = d["rows"].get(q)
            if br is None or ar.get("correct") is None or br.get("correct") is None:
                continue
            npr += 1
            rc, bc = bool(ar.get("correct")), bool(br.get("correct"))
            if rc and not bc:
                da += 1
            elif bc and not rc:
                db += 1
        p = mcnemar_exact_p(da, db) if d is not ref else 1.0
        lo, hi = wilson_ci(d["correct"], d["scored"])
        out_rows.append({
            "config_id": d["config_id"], "run_id": d["run_id"],
            "acc": d["acc"], "ci": [lo, hi], "correct": d["correct"], "scored": d["scored"],
            "is_ref": d is ref, "vs_ref_p": p, "vs_ref_sig": _sig_word(p),
            "disc_ref": da, "disc_self": db, "n_paired": npr,
            # ★基準構成と同じ実行で測られたか（違うと実行間のばらつきが差に混ざる）
            "same_run_as_ref": d["run_id"] == ref["run_id"],
        })
    # 何構成が基準と別の実行で測られたか＝この表全体をどれだけ割り引いて読むべきかの目安
    n_diff = sum(1 for r in out_rows if not r["same_run_as_ref"])
    return {"ref": ref["config_id"], "ref_run": ref["run_id"],
            "ref_scored": ref["scored"], "rows": out_rows, "n_diff_run": n_diff}


def compare_stats(a_correct: int, b_correct: int, n: int,
                  disc_a: int, disc_b: int, n_paired: int) -> dict:
    """ペア比較の有意性まとめ。disc_a=Aのみ正解, disc_b=Bのみ正解（両者が答えた問題）。"""
    p = mcnemar_exact_p(disc_a, disc_b)
    sig = "有意 (p<0.05)" if p < 0.05 else ("傾向 (p<0.10)" if p < 0.10 else "誤差圏")
    a_lo, a_hi = wilson_ci(a_correct, n)
    b_lo, b_hi = wilson_ci(b_correct, n)
    return {
        "a_acc": (a_correct / n) if n else 0.0, "a_ci": [a_lo, a_hi],
        "b_acc": (b_correct / n) if n else 0.0, "b_ci": [b_lo, b_hi],
        "diff": ((b_correct - a_correct) / n) if n else 0.0,
        "disc_a": disc_a, "disc_b": disc_b, "n_paired": n_paired,
        "mcnemar_p": p, "sig": sig,
    }


# 高速化③: trials.jsonl のパース結果を (mtime,size) キーでメモリキャッシュ。
# ファイルが変わったら自動で再読込。12MB/15k行を毎リクエスト再パースする無駄を排除。
_TRIALS_CACHE: dict[str, tuple] = {}


def _read_trials(results_dir: Path) -> list[dict]:
    fp = results_dir / "trials.jsonl"
    if not fp.is_file():
        return []
    st = fp.stat()
    key = str(fp)
    cached = _TRIALS_CACHE.get(key)
    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2]
    rows: list[dict] = []
    for line in fp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        # ★run_id は必ず文字列にそろえる（2026-08-07）。
        #   run_id を書かない結果ファイル（同梱サンプル・古い自前ラン）では None のまま集計され、
        #   画面が渡す文字列 "" と一致せず **成績一覧をクリックしても中身が出なかった**。
        r["run_id"] = r.get("run_id") or ""
        rows.append(r)
    _TRIALS_CACHE[key] = (st.st_mtime, st.st_size, rows)
    return rows


def all_qids(results_dir: Path) -> list[str]:
    """結果に現れる問題番号を重複なく返す（問題一覧の表示用）。"""
    seen = {r.get("q_id") for r in _read_trials(results_dir) if r.get("q_id")}
    return sorted(seen, key=lambda q: _qnum(q))


def version_label(epoch, wiki) -> str:
    """データ版の表示名。
    ★どちらも無い結果では**空文字**を返す（2026-08-07）。以前は "? / ?" を返していたが、
      これは作者の環境（データ版と知識ベース版を持つ）でしか意味がなく、
      配布版では全構成に「(? / ?)」が並ぶだけのノイズだった。表示側は空なら出さない。"""
    if not epoch and not wiki:
        return ""
    return f"{epoch or '?'} / {wiki or '?'}"


def _think_label(config_stages) -> str:
    """config_stages の各モデルステージの think 設定をまとめる。
    on=明示true / off=明示false / —=未指定(モデル既定)。"""
    vals = [s.get("think") for s in (config_stages or [])
            if isinstance(s, dict) and s.get("model")]
    if any(v is True or v == "true" for v in vals):
        return "on"
    if any(v is False or v == "false" for v in vals):
        return "off"
    return "—"


def _eff(r: dict, name: str, legacy_key: str | None = None):
    """ステージ実効値を返す。

    ★経緯: 生成側（radbench）は長らく **config レベルの値しか記録しておらず**、
    ステージで上書きした温度などが記録に載らなかった（温度0.7の構成が 0.0 と表示される不具合）。
    2026-08-05 に `stage_params`（ステージ実効値。段ごとに違えば "0.0,0.7" のように併記）が
    追加されたので、**あればそれを優先**する。
    ★さらに、`stage_params` が無い**過去のラン**でも `config_stages`（完全なステージ構成）には
    ステージの上書き値がそのまま残っている。そこで stage_params → config_stages → 従来フィールド
    の順に見る。これで「温度0.7の構成が 0.0 と表示される」不具合は過去のランでも解消する。
    """
    sp = r.get("stage_params")
    if isinstance(sp, dict) and sp.get(name) not in (None, ""):
        return sp[name]
    vals = []
    for st in (r.get("config_stages") or []):
        if not isinstance(st, dict):
            continue
        v = st.get(name)
        if v is not None and str(v) not in vals:
            vals.append(str(v))
    if vals:
        return ",".join(vals)
    return r.get(legacy_key or name)


def aggregate_benchmarks(results_dir: Path) -> list[dict]:
    """trials を (run_id, config_id, epoch, wiki, split) 単位の成績に集計。"""
    rows = _read_trials(results_dir)
    groups: dict[tuple, dict] = {}
    for r in rows:
        epoch = r.get("dataset_epoch")
        wiki = r.get("wiki_version")
        key = (r.get("run_id"), r.get("config_id"), epoch, wiki, r.get("split"))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "run_id": r.get("run_id"),
                "config_id": r.get("config_id"),
                "dataset_epoch": epoch,
                "wiki_version": wiki,
                "version": version_label(epoch, wiki),
                "split": r.get("split"),
                "models": r.get("models") or [],
                "stages": r.get("stages") or [],
                "prompt_ids": r.get("prompt_ids") or {},
                "config_stages": r.get("config_stages"),   # 多段の完全構成(新ログ・無ければNone)
                # ★ステージ実効値を優先（stage_params があれば。無ければ従来フィールド）
                "temperature": _eff(r, "temperature"),
                "top_k": _eff(r, "top_k"),
                "top_p": _eff(r, "top_p"),
                "num_predict": _eff(r, "num_predict"),
                "num_ctx": _eff(r, "num_ctx"),
                # 反復ペナルティ: 記録が無い＝推論エンジンの既定値に委ねている（ollama は 1.1）
                "repeat_penalty": _eff(r, "repeat_penalty"),
                # 性能評価の説明: 量子化 / バックエンド / Thinking
                "quant": r.get("quant") or "—",
                # ★backend は 2026-08-05 以降のランでのみ記録される。それ以前は「未記録」として
                #   区別する（勝手に "ollama" と決めつけない＝配布版では別エンジンのこともある）。
                "backend": r.get("backend") or "—（未記録）",
                # モデルの digest（同じタグでも中身が変わったことを後から識別するため・新ログのみ）
                "model_digests": r.get("model_digests") or {},
                "think": _think_label(r.get("config_stages")),
                "n": 0, "scored": 0, "correct": 0, "abstained": 0, "errors": 0,
            }
        g["n"] += 1
        if r.get("error"):
            g["errors"] += 1
        if r.get("correct") is None:
            continue
        g["scored"] += 1
        if r.get("correct"):
            g["correct"] += 1
        if r.get("abstained"):
            g["abstained"] += 1

    out = []
    for g in groups.values():
        g["accuracy"] = (g["correct"] / g["scored"]) if g["scored"] else 0.0
        g["accuracy_ci"] = list(wilson_ci(g["correct"], g["scored"]))  # 95%信頼区間
        g["abstain_rate"] = (g["abstained"] / g["scored"]) if g["scored"] else 0.0
        g["date"] = (g["run_id"] or "")[:10]
        out.append(g)
    return out


def _qnum(q_id: str) -> tuple[int, int]:
    try:
        y, n = re.split(r"[-_]", q_id)[:2]
        return (int(y), int(n))
    except Exception:  # noqa: BLE001
        return (0, 0)


def benchmark_detail(results_dir: Path, run_id: str, config_id: str) -> list[dict]:
    """指定 (run_id, config_id) の問題別 trial 行（pred/gold/correct/latency/error）。"""
    out = []
    for r in _read_trials(results_dir):
        if r.get("run_id") == run_id and r.get("config_id") == config_id:
            out.append({
                "q_id": r.get("q_id"),
                "pred": r.get("pred"), "gold": r.get("gold"),
                "correct": r.get("correct"), "abstained": r.get("abstained"),
                "latency_sec": r.get("latency_sec"), "error": r.get("error"),
            })
    out.sort(key=lambda x: _qnum(x.get("q_id") or ""))
    return out


def config_info(results_dir: Path, run_id: str, config_id: str) -> dict:
    """指定構成の stage 構成（アーキ図用）。config_stages(新ログ)優先、無ければ stages型+models。"""
    for r in _read_trials(results_dir):
        if r.get("run_id") == run_id and r.get("config_id") == config_id:
            cs = r.get("config_stages")
            if cs:
                arch = [{"type": s.get("type"), "model": s.get("model")} for s in cs]
            else:
                types = r.get("stages") or []
                models = r.get("models") or []
                arch = [{"type": t, "model": (models[i] if i < len(models) else "")}
                        for i, t in enumerate(types)]
            return {"arch": arch}
    return {"arch": []}


def raw_outputs(results_dir: Path, run_id: str, config_id: str) -> dict[str, dict]:
    """results/raw/<run_id>/<config_id>.csv を {q_id(year-no): {answer, raw_output}} で返す。
    生出力CSVの question_id は <year>_<no:03d>。trials の q_id(<year>-<no>) に正規化して突合。"""
    import csv
    fp = results_dir / "raw" / run_id / f"{config_id}.csv"
    res: dict[str, dict] = {}
    if not fp.is_file():
        return res
    with open(fp, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            y, n = _qnum(row.get("question_id", ""))
            key = f"{y}-{n}" if y else row.get("question_id", "")
            res[key] = {"answer": row.get("answer", ""),
                        "raw_output": row.get("raw_output", "")}
    return res


def prompt_detail(results_dir: Path, run_id: str, config_id: str) -> dict[str, dict]:
    """results/raw/<run_id>/<config_id>.detail.jsonl を {q_id(year-no): rec} で返す。
    rec = {stages:[{type,model,prompt_id,system,user,output,error}], wiki_pages, wiki_used, aux_used}。
    user は wiki/aux/findings 注入後の最終プロンプト＝RAG知識を含む。旧ランには無い（空dict）。"""
    fp = results_dir / "raw" / run_id / f"{config_id}.detail.jsonl"
    res: dict[str, dict] = {}
    if not fp.is_file():
        return res
    with open(fp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            y, n = _qnum(rec.get("q_id", ""))
            key = f"{y}-{n}" if y else rec.get("q_id", "")
            res[key] = rec
    return res


def best(results_dir: Path, limit: int | None = None) -> list[dict]:
    """成績一覧: 全構成を正答率の降順で返す。limit=None なら全件（既定）。"""
    items = aggregate_benchmarks(results_dir)
    items.sort(key=lambda g: (g["accuracy"], g["scored"], g["run_id"] or ""), reverse=True)
    return items if limit is None else items[:limit]


def latest_by_version(results_dir: Path, limit: int = 50) -> list[dict]:
    """最新ベンチマーク: バージョン(epoch×wiki)ごとに分け、各 version で最新順 top N。

    返り値: [{"version":..., "epoch":..., "wiki":..., "entries":[...]}], version は最新順。
    """
    items = aggregate_benchmarks(results_dir)
    by: dict[str, dict] = {}
    for g in items:
        vk = g["version"]
        b = by.get(vk)
        if b is None:
            b = by[vk] = {"version": vk, "epoch": g["dataset_epoch"],
                          "wiki": g["wiki_version"], "entries": []}
        b["entries"].append(g)
    for b in by.values():
        b["entries"].sort(key=lambda g: (g["run_id"] or "", g["accuracy"]), reverse=True)
        b["entries"] = b["entries"][: limit]
    ordered = sorted(
        by.values(),
        key=lambda b: max((g["run_id"] or "") for g in b["entries"]),
        reverse=True,
    )
    return ordered
