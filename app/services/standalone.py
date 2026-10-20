"""スタンドアロン・データ経路（配布版 Benchforge の骨組み）。

目的: Benchforge を将来 自己完結した配布アプリにするための「自前データ経路」の
配線サンプル。radbench の results/ に依存せず、Benchforge 自身の dataset ディレクトリ
（IN-BOX + 解析結果）だけで リーダーボード/属性統計 を計算し CSV 出力する。

★現行の radbench 読み込み経路（results_reader / characterize）は一切変更しない＝実運用は温存。
これは並行して足す独立版のスキャフォールド。既定のデータ源 = Benchforge/データセット/。

データ規約（README.md 参照）:
  inbox/answers.csv            : q_id,answer   （正解・複数解答は "a,d" のようにカンマ）
  inbox/questions/<qid>.yaml   : 問題文（任意・統計には必須でない）
  inbox/questions/images/<qid>_img<NN>.jpg : 画像。★ファイル数 = その問題の画像枚数。
  results/trials.jsonl         : 解析結果（1行=1構成×1問。config_id,q_id,pred,gold,correct,latency_sec）
"""
from __future__ import annotations

import base64
import csv
import io
import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

from app.config import APP_ROOT
from app.services.results_reader import wilson_ci

DATASETS_ROOT = APP_ROOT / "データセット"   # この直下にサブフォルダで複数データセットを置く


def list_datasets() -> list[str]:
    """DATASETS_ROOT 直下の各サブフォルダ = 1データセット（inbox か results を持つもの）。"""
    if not DATASETS_ROOT.is_dir():
        return []
    out = []
    for d in sorted(DATASETS_ROOT.iterdir()):
        if d.is_dir() and ((d / "inbox").is_dir() or (d / "results").is_dir()):
            out.append(d.name)
    return out


def dataset_dir(name: str | None = None) -> Path:
    """データセット名 → パス。name 省略時は最初のデータセット（無ければ従来の直下互換）。
    ★パストラバーサル対策: 解決後に DATASETS_ROOT 配下でなければ既定にフォールバック
    （Windowsの大小・macOSのNFC/NFD差も resolve() 後の contained 判定で吸収）。"""
    if name:
        name = name.strip()
        cand = (DATASETS_ROOT / name)
        try:
            root = DATASETS_ROOT.resolve()
            rc = cand.resolve()
            if rc == root or root in rc.parents:
                return cand
        except OSError:
            pass
        # 不正名（../ 等）は既定へ
    ds = list_datasets()
    return (DATASETS_ROOT / ds[0]) if ds else DATASETS_ROOT


# ── IN-BOX を読む ───────────────────────────────────────────
def load_answers(ds: Path) -> dict[str, list[str]]:
    fp = ds / "inbox" / "answers.csv"
    out: dict[str, list[str]] = {}
    if not fp.is_file():
        return out
    with open(fp, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            q = (row.get("q_id") or "").strip()
            ans = [a.strip().lower() for a in re.split(r"[,\s、，/]+", row.get("answer") or "") if a.strip()]
            if q:
                out[q] = sorted(ans)
    return out


def image_counts(ds: Path) -> dict[str, int]:
    """命名規則 <qid>_img<NN>.jpg のファイル数から、問題ごとの画像枚数を導出。"""
    d = ds / "inbox" / "questions" / "images"
    counts: dict[str, int] = defaultdict(int)
    if not d.is_dir():
        return {}
    for p in d.iterdir():
        m = re.match(r"^(.+)_img(\d+)\.(jpg|jpeg|png)$", p.name, re.IGNORECASE)
        if m:
            counts[m.group(1)] += 1
    return dict(counts)


def load_results(ds: Path) -> list[dict]:
    fp = ds / "results" / "trials.jsonl"
    rows: list[dict] = []
    if not fp.is_file():
        return rows
    for line in fp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ── サンプル問題（実演タブ用・自前 IN-BOX から）──────────────
def list_questions(ds: Path | None = None) -> list[str]:
    ds = ds or dataset_dir()
    d = ds / "inbox" / "questions"
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


def demo_sample(qid: str, ds: Path | None = None) -> dict | None:
    """データセット の1問を実演タブ用フォーマットで返す（画像は data-URI・gold は answers.csv）。"""
    ds = ds or dataset_dir()
    fp = ds / "inbox" / "questions" / f"{qid}.yaml"
    if not fp.is_file():
        return None
    doc = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
    qdir = fp.parent
    images, caps = [], []
    for im in doc.get("images", []) or []:
        rel = im.get("file", "")
        ip = (qdir / rel)
        if ip.is_file():
            ext = ip.suffix.lstrip(".").lower() or "jpg"
            b64 = base64.b64encode(ip.read_bytes()).decode("utf-8")
            images.append(f"data:image/{ext};base64,{b64}")
            caps.append(im.get("caption", ""))
    cap_block = ""
    if caps:
        cap_block = "\n".join(["【画像と説明（画像の順番に対応）】"]
                              + [f"  画像{i}: {c}" for i, c in enumerate(caps, 1)])
    gold = load_answers(ds).get(qid, [])
    return {"id": qid, "title": f"{qid}（同梱サンプル）", "question": doc.get("question", ""),
            "note": "データセット の IN-BOX 問題", "gold": ",".join(gold),
            "captions": cap_block, "images": images}


# ── 統計（リーダーボード＋画像枚数スライス）──────────────────
def _img_bucket(n: int) -> str:
    if n <= 0:
        return "画像なし"
    return "画像1枚" if n == 1 else "画像2枚以上"


def compute_stats(ds: Path | None = None) -> dict:
    ds = ds or dataset_dir()
    answers = load_answers(ds)
    imgc = image_counts(ds)
    rows = load_results(ds)
    per: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        c, q = r.get("config_id"), r.get("q_id")
        if not c or not q or q not in answers:
            continue
        per[c][q] = r
    # リーダーボード
    leaderboard = []
    for c in sorted(per):
        qs = list(per[c])
        n = len(qs)
        k = sum(1 for q in qs if per[c][q].get("correct"))
        lats = [per[c][q].get("latency_sec") for q in qs if per[c][q].get("latency_sec") is not None]
        lo, hi = wilson_ci(k, n)
        leaderboard.append({
            "config_id": c, "n": n, "correct": k, "acc": (k / n) if n else 0.0,
            "ci": [round(lo, 4), round(hi, 4)],
            "latency_median": round(sorted(lats)[len(lats) // 2], 1) if lats else None,
            "latency_sum": round(sum(lats), 1) if lats else None,
        })
    leaderboard.sort(key=lambda x: x["acc"], reverse=True)
    # 画像枚数スライス（命名規則から導出＝配布版でも self-contained）
    slices = []
    for c in sorted(per):
        buckets: dict[str, list[str]] = defaultdict(list)
        for q in per[c]:
            buckets[_img_bucket(imgc.get(q, 0))].append(q)
        for bkt in ("画像1枚", "画像2枚以上", "画像なし"):
            if bkt in buckets:
                qs = buckets[bkt]; n = len(qs)
                k = sum(1 for q in qs if per[c][q].get("correct"))
                slices.append({"config_id": c, "bucket": bkt, "n": n,
                               "correct": k, "acc": (k / n) if n else 0.0})
    return {"dataset": str(ds), "n_questions": len(answers), "n_configs": len(per),
            "image_counts": imgc, "leaderboard": leaderboard, "slices": slices}


# ── CSV 出力（#4）───────────────────────────────────────────
def stats_csv(ds: Path | None = None) -> str:
    st = compute_stats(ds)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# Benchforge standalone stats", st["dataset"],
                f"questions={st['n_questions']}", f"configs={st['n_configs']}"])
    w.writerow([])
    w.writerow(["[leaderboard]"])
    w.writerow(["config_id", "accuracy", "correct", "n", "ci_low", "ci_high",
                "latency_median_sec", "latency_sum_sec"])
    for r in st["leaderboard"]:
        w.writerow([r["config_id"], f"{r['acc']*100:.1f}%", r["correct"], r["n"],
                    f"{r['ci'][0]*100:.1f}%", f"{r['ci'][1]*100:.1f}%",
                    r["latency_median"], r["latency_sum"]])
    w.writerow([])
    w.writerow(["[slice: image_count]"])
    w.writerow(["config_id", "bucket", "accuracy", "correct", "n"])
    for r in st["slices"]:
        w.writerow([r["config_id"], r["bucket"], f"{r['acc']*100:.1f}%", r["correct"], r["n"]])
    return buf.getvalue()
