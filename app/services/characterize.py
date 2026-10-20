"""特性評価サービス（失敗分析・誤ラベル疑い・run_id横断マージ・Holm有意差・タグ）。

Benchforge 設計原則を厳守: radbench のコードは import しない。radbench の results/ を
読み取り専用で参照するだけ。書き込みは Benchforge/data/ のみ（タグ = data/tags.json）。
統計は自前（wilson_ci / mcnemar_exact_p は results_reader から再利用・scipy非依存）。

Claude と一緒にやった解析（tools/failure_analysis.py / irt_eval.py / classical 集約 / 有意差＋Holm）
を GUI から Claude 抜きで再現するための土台。
"""
from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from app.services.results_reader import wilson_ci, mcnemar_exact_p, _read_trials
from app.services import naming
from app.services import standalone as SA

# question_attributes.csv の軸（radbench tools/question_attributes.py 由来・読取専用）
ATTR_AXES = ["qtype", "label_value", "n_select", "region", "n_images", "stem_len"]

# データセットの指定: "" or "radbench" = radbench の results/、それ以外 = データセット/<name>
def _is_radbench(dataset: str | None) -> bool:
    return not dataset or dataset == "radbench"


# ── 属性: radbench は question_attributes.csv、標準データセットは命名規則から導出 ──
def load_attributes(results_dir: Path, dataset: str | None = None) -> dict[str, dict]:
    if not _is_radbench(dataset):
        # 標準データセット: 画像枚数を命名規則(<qid>_img<NN>)から導出（手ラベル不要）
        ds = SA.dataset_dir(dataset)
        imgc = SA.image_counts(ds)
        out = {}
        for q, n in imgc.items():
            out[q] = {"n_images": ("画像1枚" if n == 1 else "画像2枚以上" if n >= 2 else "画像なし")}
        return out
    fp = results_dir / "question_attributes.csv"
    out: dict[str, dict] = {}
    if not fp.is_file():
        return out
    with open(fp, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            out[row["q_id"]] = row
    return out


# ── タグ（Benchforge 自前・データセット別に data/tags/<dataset>.json）────────
def _ds_key(dataset: str | None) -> str:
    """タグ/split の保存ファイル名。★必ずサニタイズする＝外部から `?dataset=../..` を
    渡されると data/tags/ の外に書けてしまうため（dataset_dir() と同じ扱いに揃える）。"""
    return "radbench" if _is_radbench(dataset) else naming.sanitize(dataset, "dataset")


def _tags_path(app_root: Path, dataset: str | None = None) -> Path:
    d = app_root / "data" / "tags"
    d.mkdir(parents=True, exist_ok=True)
    # 旧 data/tags.json（radbench・単一）からの後方互換
    legacy = app_root / "data" / "tags.json"
    p = d / f"{_ds_key(dataset)}.json"
    if _is_radbench(dataset) and legacy.is_file() and not p.is_file():
        return legacy
    return p


def load_tags(app_root: Path, dataset: str | None = None) -> dict[str, list[str]]:
    fp = _tags_path(app_root, dataset)
    if not fp.is_file():
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def set_tag(app_root: Path, q_id: str, tag: str, add: bool, dataset: str | None = None) -> dict[str, list[str]]:
    """q_id にタグを付与/除去して保存。tag は小さな識別名（例: '画像1枚'）。"""
    tag = (tag or "").strip()
    tags = load_tags(app_root, dataset)
    cur = set(tags.get(q_id, []))
    if add and tag:
        cur.add(tag)
    else:
        cur.discard(tag)
    if cur:
        tags[q_id] = sorted(cur)
    else:
        tags.pop(q_id, None)
    _tags_path(app_root, dataset).write_text(json.dumps(tags, ensure_ascii=False, indent=2), encoding="utf-8")
    return tags


def _strip_bom(s: str) -> str:
    """★Excel が付ける BOM を落とす（2026-08-25）。
    付いたままだと先頭セルが "\ufeffq_id" になり、ヘッダ検出が偶然でしか働かない。"""
    return s.lstrip("\ufeff")


def _backup_before_replace(path: Path) -> Path | None:
    """★置き換えは破壊的なので、直前の状態を1つ残す（2026-08-25）。
    利用者が『取り込んだら消えた』と気づいたときに戻せる最後の砦。"""
    if not path.is_file():
        return None
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return bak


def import_tags_csv(app_root: Path, csv_text: str, dataset: str | None = None,
                    mode: str = "merge") -> dict:
    """CSV からタグを一括取込。
    書式: ヘッダ q_id,tag（tag は ; か , か / 区切りで複数可）。1問に複数行でも可。

    mode="merge"（既定）: 既存タグを保持して追加する（従来の挙動）。
    mode="replace": ★**CSV の内容で置き換える**。CSV に無いタグは消える。
      ⇒ 「書き出す → 編集する → 取り込む」という往復を成立させるためのモード（2026-08-25 追加）。
      以前は merge しかなく、**行を消した CSV を取り込んでもタグが消えなかった**ため、
      利用者の「編集して取り込む」という意図と挙動が食い違っていた。
      破壊的なので、直前の状態を .bak に退避してから書き換える。"""
    import io
    tags = load_tags(app_root, dataset)
    before = {q: list(v) for q, v in tags.items()}
    if mode == "replace":
        tags = {}
    reader = csv.reader(io.StringIO(_strip_bom(csv_text)))
    rows = list(reader)
    if not rows:
        return {"ok": False, "added": 0, "tags": tags}
    # ヘッダ検出（q_id/tag を含む行があれば以降をデータに）
    start = 0
    head = [c.strip().lower() for c in rows[0]]
    if "q_id" in head or "qid" in head or "tag" in head or "tags" in head:
        start = 1
    added = 0
    for r in rows[start:]:
        if len(r) < 2:
            continue
        q = r[0].strip()
        raw = r[1].strip()
        if not q or not raw:
            continue
        cur = set(tags.get(q, []))
        for t in re.split(r"[;,/、，]", raw):
            t = t.strip()
            if t:
                if t not in cur:
                    added += 1
                cur.add(t)
        tags[q] = sorted(cur)
    fp = _tags_path(app_root, dataset)
    if mode == "replace":
        _backup_before_replace(fp)
    fp.write_text(json.dumps(tags, ensure_ascii=False, indent=2), encoding="utf-8")
    removed = sum(len(set(before.get(q, [])) - set(tags.get(q, []))) for q in before)
    return {"ok": True, "added": added, "removed": removed, "mode": mode,
            "tags": tags, "names": all_tag_names(tags)}


def import_tags_file(app_root: Path, path: Path, dataset: str | None = None,
                     mode: str = "merge") -> dict:
    if not path.is_file():
        return {"ok": False, "error": f"file not found: {path}", "added": 0}
    return import_tags_csv(app_root, path.read_text(encoding="utf-8"), dataset, mode)


# ── 正解ラベルの確認記録（誤ラベル疑いのレビュー・データセット別）────────
def _review_path(app_root: Path, dataset: str | None = None) -> Path:
    d = app_root / "data" / "review"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_ds_key(dataset)}.json"


# ★状態名から "gold" を外した（2026-08-07）。"gold" は開発者の用語で、利用者に伝わらない。
#   既存の保存データは読み取り時に読み替える（保存し直しは不要＝壊さない）。
_STATUS_ALIAS = {"gold正しい": "正しい"}


def load_review(app_root: Path, dataset: str | None = None) -> dict:
    fp = _review_path(app_root, dataset)
    if not fp.is_file():
        return {}
    try:
        rv = json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    for d in rv.values():
        if isinstance(d, dict) and d.get("status") in _STATUS_ALIAS:
            d["status"] = _STATUS_ALIAS[d["status"]]
    return rv


def set_review(app_root: Path, q_id: str, status: str, memo: str, dataset: str | None = None) -> dict:
    """q_id のレビュー状態を保存。status: '未確認'|'正しい'|'要修正'。"""
    rv = load_review(app_root, dataset)
    status = _STATUS_ALIAS.get(status, status)
    if status in ("", "未確認") and not (memo or "").strip():
        rv.pop(q_id, None)
    else:
        rv[q_id] = {"status": status or "未確認", "memo": (memo or "").strip()}
    _review_path(app_root, dataset).write_text(json.dumps(rv, ensure_ascii=False, indent=2), encoding="utf-8")
    return rv


def review_csv(app_root: Path, dataset: str | None = None, only_fix: bool = False) -> str:
    import csv as _csv
    import io
    rv = load_review(app_root, dataset)
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["q_id", "status", "memo"])
    for q, d in sorted(rv.items()):
        if only_fix and d.get("status") != "要修正":
            continue
        w.writerow([q, d.get("status", ""), d.get("memo", "")])
    return buf.getvalue()


def all_tag_names(tags: dict[str, list[str]]) -> list[str]:
    names: set[str] = set()
    for v in tags.values():
        names.update(v)
    return sorted(names)


# ── split（問題サブセット）管理：タグ同様に data/splits.json で持つ ──────
def _splits_path(app_root: Path, dataset: str | None = None) -> Path:
    d = app_root / "data" / "splits"
    d.mkdir(parents=True, exist_ok=True)
    legacy = app_root / "data" / "splits.json"
    p = d / f"{_ds_key(dataset)}.json"
    if _is_radbench(dataset) and legacy.is_file() and not p.is_file():
        return legacy
    return p


def load_splits(app_root: Path, dataset: str | None = None) -> dict[str, list[str]]:
    fp = _splits_path(app_root, dataset)
    if not fp.is_file():
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _parse_qids(raw: str) -> list[str]:
    """カンマ/空白/改行/読点区切りの q_id 羅列を正規化（区切りが - でも _ でも同じ問題として扱う）。"""
    out = []
    for tok in re.split(r"[\s,;/、，；]+", raw or ""):
        tok = tok.strip()
        if not tok:
            continue
        m = re.match(r"^(\d{4})[-_](\d+)$", tok)
        out.append(f"{int(m.group(1))}-{int(m.group(2))}" if m else tok)
    # 重複除去・順序保持
    seen, res = set(), []
    for q in out:
        if q not in seen:
            seen.add(q); res.append(q)
    return res


def save_split(app_root: Path, name: str, qids_raw: str, dataset: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "split 名を入力してください"}
    splits = load_splits(app_root, dataset)
    qids = _parse_qids(qids_raw)
    splits[name] = qids
    _splits_path(app_root, dataset).write_text(json.dumps(splits, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "name": name, "n": len(qids), "splits": splits}


def delete_split(app_root: Path, name: str, dataset: str | None = None) -> dict:
    splits = load_splits(app_root, dataset)
    splits.pop(name, None)
    _splits_path(app_root, dataset).write_text(json.dumps(splits, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "splits": splits}


def import_splits_csv(app_root: Path, csv_text: str, dataset: str | None = None,
                      mode: str = "merge") -> dict:
    """CSV から問題グループを一括登録。
    書式: ヘッダ split,q_id（q_id は ; か , か 空白 区切りで複数可・同じ名前は複数行でもマージ）。
    例:  dev,s001;s002
         test,s003

    mode="merge"（既定）: 既存に追加する（従来の挙動）。
    mode="replace": ★**CSV の内容で置き換える**。CSV に無いグループ・問題は消える。
      ⇒ 「書き出す → 編集する → 取り込む」の往復を成立させるため（2026-08-25 追加）。
      破壊的なので直前の状態を .bak に退避する。"""
    import io
    splits = load_splits(app_root, dataset)
    before = {k: list(v) for k, v in splits.items()}
    if mode == "replace":
        splits = {}
    reader = csv.reader(io.StringIO(_strip_bom(csv_text)))
    rows = list(reader)
    if not rows:
        return {"ok": False, "added": 0, "splits": splits}
    start = 0
    head = [c.strip().lower() for c in rows[0]]
    if "split" in head or "q_id" in head or "qids" in head:
        start = 1
    added = 0
    for r in rows[start:]:
        if len(r) < 2:
            continue
        name = r[0].strip()
        raw = r[1].strip()
        if not name or not raw:
            continue
        cur = list(splits.get(name, []))
        seen = set(cur)
        for q in _parse_qids(raw):
            if q not in seen:
                cur.append(q); seen.add(q); added += 1
        splits[name] = cur
    fp = _splits_path(app_root, dataset)
    if mode == "replace":
        _backup_before_replace(fp)
    fp.write_text(json.dumps(splits, ensure_ascii=False, indent=2), encoding="utf-8")
    removed = sum(1 for k in before if k not in splits)
    return {"ok": True, "added": added, "removed_groups": removed, "mode": mode,
            "splits": splits}


def import_splits_file(app_root: Path, path: Path, dataset: str | None = None,
                       mode: str = "merge") -> dict:
    if not path.is_file():
        return {"ok": False, "error": f"file not found: {path}", "added": 0}
    return import_splits_csv(app_root, path.read_text(encoding="utf-8"), dataset)


def split_yaml_snippet(app_root: Path, name: str, dataset: str | None = None) -> str:
    """radbench paths.yaml の split_qids に貼れる YAML 断片を生成（Benchforge は radbench に書かないので手貼り用）。"""
    splits = load_splits(app_root, dataset)
    qids = splits.get(name, [])
    lines = [f"  {name}:"]
    for q in qids:
        y, n = (q.split("-") + ["", ""])[:2]
        lines.append(f'    - "{y}-{n}"')
    return "\n".join(lines) + "\n"


# ── run_id 横断マージ（q_id 単位で最新 run を採用）──────────────────
def merged_results(results_dir: Path, since: str = "2026-07-13", dataset: str | None = None):
    """{config_id: {q_id: {correct,pred,gold,latency}}} を返す。
    radbench: results/trials.jsonl を run_id マージ。標準データセット: データセット/<name>/results/trials.jsonl。
    同じ (config_id, q_id) が複数 run にあれば run_id 文字列が最大（＝最新）の行を採用。gold 無しは除外。"""
    if not _is_radbench(dataset):
        rows = _read_trials(SA.dataset_dir(dataset) / "results")
        since = ""   # 標準データセットは run_id/epoch 概念がないので since フィルタ無効
    else:
        rows = _read_trials(results_dir)
    latest: dict[tuple, tuple] = {}   # (config,qid) -> (run_id, row)
    for r in rows:
        rid = r.get("run_id") or ""
        if since and rid < since:
            continue
        if r.get("gold") in (None, [], ""):
            continue
        cfg, qid = r.get("config_id"), r.get("q_id")
        if not cfg or not qid:
            continue
        key = (cfg, qid)
        if key not in latest or rid > latest[key][0]:
            latest[key] = (rid, r)
    per: dict[str, dict] = defaultdict(dict)
    runs_used: dict[str, set] = defaultdict(set)
    for (cfg, qid), (rid, r) in latest.items():
        per[cfg][qid] = {
            "correct": bool(r.get("correct")),
            "pred": r.get("pred"), "gold": r.get("gold"),
            "latency": r.get("latency_sec"),
        }
        runs_used[cfg].add(rid)
    meta = {c: {"n": len(per[c]), "runs": sorted(runs_used[c])} for c in per}
    return per, meta


def config_list(results_dir: Path, since: str = "2026-07-13", dataset: str | None = None) -> list[dict]:
    per, meta = merged_results(results_dir, since, dataset)
    out = []
    for c in sorted(per):
        n = meta[c]["n"]
        k = sum(1 for v in per[c].values() if v["correct"])
        # ★qset = 解いた問題集合の署名（2026-08-25 追加）。
        #   これまで「比較できる構成か」を問題数 n だけで判定していたが、
        #   **n が同じでも中身が違えば比較は成立しない**。署名で厳密に突き合わせる。
        import hashlib
        qset = hashlib.sha1("\n".join(sorted(per[c])).encode()).hexdigest()[:8]
        out.append({"config_id": c, "n": n, "correct": k,
                    "acc": (k / n) if n else 0.0, "runs": meta[c]["runs"],
                    "qset": qset})
    out.sort(key=lambda x: x["acc"], reverse=True)
    return out


# ── 属性＋タグの軸を1問ぶんに合成 ───────────────────────────────
def _axes_for(qid: str, attrs: dict, tags: dict) -> dict[str, str]:
    d = {ax: (attrs.get(qid, {}).get(ax) or "（不明）") for ax in ATTR_AXES}
    for t in tags.get(qid, []):
        d[f"タグ:{t}"] = "該当"
    return d


# ── 失敗分析: 属性別正答率＋救済/破綻 ──────────────────────────
def attribute_slices(results_dir: Path, app_root: Path, configs: list[str],
                     baseline: str, since: str = "2026-07-13", dataset: str | None = None) -> dict:
    per, _ = merged_results(results_dir, since, dataset)
    attrs = load_attributes(results_dir, dataset)
    tags = load_tags(app_root, dataset)
    configs = [c for c in configs if c in per]
    if not configs:
        return {"axes": [], "error": "指定構成が見つかりません"}
    common = set.intersection(*[set(per[c]) for c in configs]) if configs else set()
    base = baseline if baseline in per else configs[0]
    tag_axes = [f"タグ:{t}" for t in all_tag_names(tags)]
    axes_names = ATTR_AXES + tag_axes
    result_axes = []
    for ax in axes_names:
        buckets: dict[str, list[str]] = defaultdict(list)
        for q in common:
            val = _axes_for(q, attrs, tags).get(ax)
            if val is None:
                continue
            buckets[val].append(q)
        rows = []
        for bkt, qs in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            n = len(qs)
            row = {"bucket": bkt, "n": n}
            for c in configs:
                k = sum(1 for q in qs if per[c][q]["correct"])
                row[c] = (k / n) if n else 0.0
            # baseline に対する救済/破綻（先頭の非baseline構成）
            row["cfgs"] = {c: (sum(1 for q in qs if per[c][q]["correct"]) / n if n else 0.0) for c in configs}
            rows.append(row)
        result_axes.append({"axis": ax, "rows": rows})
    return {"configs": configs, "baseline": base, "n_common": len(common), "axes": result_axes}


# ── 難問コア（選択構成が全滅）＋属性偏り ───────────────────────
def hard_core(results_dir: Path, app_root: Path, configs: list[str],
              since: str = "2026-07-13", dataset: str | None = None) -> dict:
    per, _ = merged_results(results_dir, since, dataset)
    attrs = load_attributes(results_dir, dataset)
    tags = load_tags(app_root, dataset)
    configs = [c for c in configs if c in per]
    if not configs:
        return {"error": "指定構成が見つかりません"}
    common = set.intersection(*[set(per[c]) for c in configs])
    allfail = sorted(q for q in common if not any(per[c][q]["correct"] for c in configs))
    allpass = sum(1 for q in common if all(per[c][q]["correct"] for c in configs))
    # 属性偏り（難問コアでの構成比 vs 全体）
    bias = []
    for ax in ATTR_AXES + [f"タグ:{t}" for t in all_tag_names(tags)]:
        tot = Counter(_axes_for(q, attrs, tags).get(ax) for q in common)
        fail = Counter(_axes_for(q, attrs, tags).get(ax) for q in allfail)
        buckets = []
        for bkt in sorted(tot, key=lambda b: -tot[b]):
            if bkt is None:
                continue
            sa = 100 * tot[bkt] / len(common) if common else 0
            sf = 100 * fail[bkt] / len(allfail) if allfail else 0
            buckets.append({"bucket": bkt, "share_core": round(sf, 0), "share_all": round(sa, 0),
                            "delta": round(sf - sa, 0)})
        buckets = [b for b in buckets if abs(b["delta"]) >= 6]
        if buckets:
            bias.append({"axis": ax, "buckets": sorted(buckets, key=lambda b: -b["delta"])})
    return {"configs": configs, "n_common": len(common), "n_core": len(allfail),
            "n_allpass": allpass, "core_qids": allfail, "bias": bias}


# ── 救済/破綻（構成 vs baseline）────────────────────────────────
def rescue_break(results_dir: Path, app_root: Path, config: str, baseline: str,
                 since: str = "2026-07-13", dataset: str | None = None) -> dict:
    per, _ = merged_results(results_dir, since, dataset)
    attrs = load_attributes(results_dir, dataset)
    if config not in per or baseline not in per:
        return {"error": "構成が見つかりません"}
    common = set(per[config]) & set(per[baseline])
    rescue = [q for q in common if per[config][q]["correct"] and not per[baseline][q]["correct"]]
    brk = [q for q in common if per[baseline][q]["correct"] and not per[config][q]["correct"]]
    def dist(qs):
        return dict(Counter(attrs.get(q, {}).get("region", "（不明）") for q in qs))
    return {"config": config, "baseline": baseline, "n_common": len(common),
            "rescue": len(rescue), "break": len(brk), "net": len(rescue) - len(brk),
            "rescue_region": dist(rescue), "break_region": dist(brk),
            "rescue_qids": sorted(rescue), "break_qids": sorted(brk)}


# ── 誤ラベル疑い（point-biserial 負＝強い構成ほど gold と違う）──────
def mislabel_suspects(results_dir: Path, ref_config: str = "arbiter_ff",
                      since: str = "2026-07-13", min_pb: float = -0.05,
                      configs: list[str] | None = None, dataset: str | None = None) -> dict:
    per, _ = merged_results(results_dir, since, dataset)
    if not per:
        return {"suspects": [], "n_configs": 0}
    configs = [c for c in (configs or sorted(per)) if c in per]
    if len(configs) < 3:
        return {"suspects": [], "n_configs": len(configs),
                "note": "point-biserial は3構成以上・できれば多様な系統で。"}
    common = set.intersection(*[set(per[c]) for c in configs])
    common = sorted(common)
    if not common:
        return {"suspects": []}
    # 各構成の総得点（common上の正答数）と、問題ごとの正誤ベクトル
    totals = {c: sum(1 for q in common if per[c][q]["correct"]) for c in configs}
    tvals = [totals[c] for c in configs]
    tmean = sum(tvals) / len(tvals)
    tsd = math.sqrt(sum((t - tmean) ** 2 for t in tvals) / len(tvals)) or 1e-9
    suspects = []
    for q in common:
        item = [1 if per[c][q]["correct"] else 0 for c in configs]
        p = sum(item) / len(item)              # 全構成での正答率
        imean = p
        isd = math.sqrt(p * (1 - p)) or 1e-9
        # point-biserial ≒ 項目正誤と総得点の相関
        cov = sum((item[i] - imean) * (tvals[i] - tmean) for i in range(len(configs))) / len(configs)
        pb = cov / (isd * tsd)
        if pb <= min_pb:
            ref = per.get(ref_config, {}).get(q, {})
            suspects.append({"q_id": q, "pb": round(pb, 2), "all_acc": round(100 * p),
                             "gold": ref.get("gold"), "ref_pred": ref.get("pred")})
    suspects.sort(key=lambda s: s["pb"])
    return {"ref_config": ref_config, "n_common": len(common),
            "n_configs": len(configs), "suspects": suspects}


# ── Holm 補正つき総当たり有意差 ──────────────────────────────
def significance_holm(results_dir: Path, configs: list[str], since: str = "2026-07-13",
                      dataset: str | None = None) -> dict:
    per, _ = merged_results(results_dir, since, dataset)
    configs = [c for c in configs if c in per]
    if len(configs) < 2:
        return {"rows": [], "pairs": [], "error": "2構成以上を選んでください"}
    common = set.intersection(*[set(per[c]) for c in configs])
    n = len(common)
    rows = []
    for c in configs:
        k = sum(1 for q in common if per[c][q]["correct"])
        lo, hi = wilson_ci(k, n)
        rows.append({"config_id": c, "acc": (k / n) if n else 0.0, "ci": [lo, hi],
                     "correct": k, "scored": n})
    rows.sort(key=lambda r: r["acc"], reverse=True)
    # 総当たり McNemar
    raw = []
    for i in range(len(configs)):
        for j in range(i + 1, len(configs)):
            a, b = configs[i], configs[j]
            da = sum(1 for q in common if per[a][q]["correct"] and not per[b][q]["correct"])
            db = sum(1 for q in common if per[b][q]["correct"] and not per[a][q]["correct"])
            p = mcnemar_exact_p(da, db)
            raw.append({"a": a, "b": b, "disc_a": da, "disc_b": db, "p": p})
    # Holm 補正
    order = sorted(range(len(raw)), key=lambda k: raw[k]["p"])
    m = len(raw)
    prev = 0.0
    for rank, idx in enumerate(order):
        h = min(1.0, max(prev, (m - rank) * raw[idx]["p"]))
        prev = h
        raw[idx]["p_holm"] = h
        raw[idx]["sig"] = "有意" if h < 0.05 else "誤差圏"
    return {"n_common": n, "rows": rows, "pairs": raw}
