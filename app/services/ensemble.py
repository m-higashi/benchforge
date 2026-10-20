# -*- coding: utf-8 -*-
"""アンサンブル(vote/合議)構成の個体別正誤を生ログから解析する（読み取り専用）。

Benchforge 分離原則: radbench は import せず results/ のみ参照。
メンバー(構成に含まれる各モデル)は生ログ raw_output の `[stage:model]` ブロックから
動的に発見する＝アンサンブルのモデル数 N が変わっても自動対応する。

最終(合議)の正誤は trials の `correct` を信頼し、集約方式(vote/mv/…)に依存しない。
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path

from . import results_reader as RR

# radbench.scoring.parse_prediction と同等の抽出（分離のため自前複製）
_ANSWER_LINE = re.compile(
    r"(?:最終解答|解答|答え|answer)\s*[:：]\s*([a-e\s,、，/]+)", re.IGNORECASE)


def parse_prediction(text: str) -> list[str]:
    """モデル自由出力から選択記号集合を抽出。明示「解答: X」優先（複数あれば最後）。"""
    t = unicodedata.normalize("NFKC", text or "")
    ms = list(_ANSWER_LINE.finditer(t))
    if ms:
        return sorted(set(re.findall(r"[a-e]", ms[-1].group(1).lower())))
    for ln in reversed([l.strip() for l in t.splitlines() if l.strip()][-3:]):
        toks = re.findall(r"(?<![a-z])[a-e](?![a-z])", ln.lower())
        if toks:
            return sorted(set(toks))
    return []


#: 段の種類を人が読める名前にする（内訳の見出し用）
_STAGE_LABEL = {"direct_mc": "候補", "reason": "推論", "judge": "審査役",
                "arbiter": "審査役", "arbiter_gate": "審査役", "vote": "多数決"}

#: 解答を出さない段。**個体として数えない**（2026-10-06）。
#  所見記述の段は解答を書かないのが正常な動作だが、内訳では
#  「正答率 2.8%・棄権90」のように出てしまい、壊れているように見える。
NON_ANSWER_STAGES = {"vision_findings", "findings", "caption", "summarize"}


def parse_members(raw: str) -> list[tuple[str, list[str]]]:
    """raw_output → [(メンバー名, pred)]。`[stage:model]` のうち model 非空をメンバーとみなす
    （集約ステージ `[vote]`/`[mv]` は model 空なので自然に除外）。ブロック区切りは `===`。

    ★2026-10-06: **同じモデルを複数段に使う構成**（自己合議。候補を3回出して審査役が選ぶ等）では、
      モデル名だけだと全段が1個体に潰れて内訳にならなかった。段の種類と出現順で区別する。
      例: 候補1 / 候補2 / 候補3 / 所見 / 推論（qwen38_flash_next）
      1モデル1段の従来構成では名前は変わらない（モデル名のまま）。
    """
    found: list[tuple[str, str, list[str]]] = []      # (stage, model, pred)
    for part in re.split(r"\n=+\n", raw or ""):
        m = re.match(r"\s*\[([^:\]]+):?([^\]]*)\]\s*(.*)", part, re.DOTALL)
        if not m:
            continue
        model = m.group(2).strip()
        stage = m.group(1).strip()
        if model and stage not in NON_ANSWER_STAGES:
            found.append((stage, model, parse_prediction(m.group(3))))

    multi = len({mo for _, mo, _ in found}) < len(found)   # 同じモデルが2回以上出る
    seen: Counter = Counter()
    out: list[tuple[str, list[str]]] = []
    for stage, model, pred in found:
        if not multi:
            out.append((model, pred))
            continue
        seen[stage] += 1
        label = _STAGE_LABEL.get(stage, stage)
        same_stage = sum(1 for s, _, _ in found if s == stage)
        name = f"{label}{seen[stage]}" if same_stage > 1 else label
        out.append((f"{name}（{model}）", pred))
    return out


def list_ensembles(results_dir: Path, limit: int = 80) -> list[dict]:
    """アンサンブル(メンバー2以上)構成の一覧。プルダウン用。"""
    out = []
    for e in RR.best(results_dir, limit=limit):
        if len(e.get("models") or []) >= 2:
            out.append({"run_id": e["run_id"], "config_id": e["config_id"],
                        "version": e.get("version", ""), "accuracy": e.get("accuracy", 0.0),
                        "models": e.get("models") or [], "scored": e.get("scored", 0)})
    return out


def _config_stages(results_dir: Path, run_id: str, config_id: str) -> list:
    """指定 (run_id, config_id) の最初の trial 行から config_stages を取る（per-member think 用）。"""
    import json
    tf = results_dir / "trials.jsonl"
    if not tf.is_file():
        return []
    for line in tf.open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if d.get("run_id") == run_id and d.get("config_id") == config_id:
            return d.get("config_stages") or []
    return []


def _models_meta(results_dir: Path) -> dict:
    """radbench config/models.yaml(read-only参照) から {model_key: {quant, backend}}。"""
    import yaml
    p = results_dir.parent / "config" / "models.yaml"
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    ms = d.get("models", d)
    out = {}
    if isinstance(ms, dict):
        for k, v in ms.items():
            if isinstance(v, dict):
                backend = v.get("backend") or ("ollama" if v.get("ollama_tag") else "—")
                out[k] = {"quant": v.get("quant") or "—", "backend": backend}
    return out


def breakdown(results_dir: Path, run_id: str, config_id: str) -> dict:
    """個体別正誤の解析結果を返す。"""
    rows = RR.benchmark_detail(results_dir, run_id, config_id)
    raw = RR.raw_outputs(results_dir, run_id, config_id)

    members: list[str] = []
    mem_correct, mem_total, mem_abstain = Counter(), Counter(), Counter()
    vote_win, mem_win = Counter(), Counter()      # vote正×member誤 / vote誤×member正
    dyn = Counter()
    vote_correct = scored = 0
    table = []

    for r in rows:
        qid = r.get("q_id")
        gold = [str(x).lower() for x in (r.get("gold") or [])]
        if not gold:
            continue
        mems = parse_members((raw.get(qid) or {}).get("raw_output", ""))
        if not mems:
            continue
        scored += 1
        goldset = set(gold)
        vc = bool(r.get("correct"))               # 合議の最終正誤は trials を信頼
        vote_correct += vc
        per = []
        for model, pred in mems:
            if model not in members:
                members.append(model)
            mem_total[model] += 1
            if not pred:
                mem_abstain[model] += 1
            ok = (set(pred) == goldset)
            mem_correct[model] += ok
            per.append({"model": model, "pred": pred, "correct": ok})
            if vc and not ok:
                vote_win[model] += 1
            if (not vc) and ok:
                mem_win[model] += 1
        oks = [p["correct"] for p in per]
        if all(oks):
            dyn["all_correct"] += 1
        elif not any(oks):
            dyn["all_wrong"] += 1
        else:
            dyn["rescued" if vc else "broke"] += 1
        table.append({"q_id": qid, "gold": gold,
                      "members": {p["model"]: {"pred": p["pred"], "correct": p["correct"]}
                                  for p in per},
                      "vote_pred": r.get("pred") or [], "vote_correct": vc})

    # per-member の説明: 量子化 / backend / think
    mm = _models_meta(results_dir)
    think_map = {}
    for s in _config_stages(results_dir, run_id, config_id):
        if isinstance(s, dict) and s.get("model"):
            t = s.get("think")
            think_map[s["model"]] = ("on" if t in (True, "true")
                                     else "off" if t in (False, "false") else "—")
    member_stats = [{
        "model": m, "correct": mem_correct[m], "total": mem_total[m],
        "abstain": mem_abstain[m],
        "accuracy": (mem_correct[m] / mem_total[m]) if mem_total[m] else 0.0,
        "vote_win": vote_win[m], "mem_win": mem_win[m],
        "quant": mm.get(m, {}).get("quant", "—"),
        "backend": mm.get(m, {}).get("backend", "ollama"),
        "think": think_map.get(m, "—"),
    } for m in members]

    return {
        "run_id": run_id, "config_id": config_id, "scored": scored,
        "members": members, "member_stats": member_stats,
        "vote": {"correct": vote_correct, "scored": scored,
                 "accuracy": (vote_correct / scored) if scored else 0.0},
        "dynamics": {k: dyn.get(k, 0)
                     for k in ("all_correct", "all_wrong", "rescued", "broke")},
        "rows": table,
    }
