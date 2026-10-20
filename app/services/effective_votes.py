# -*- coding: utf-8 -*-
"""実効独立票（n_eff）— 候補プールの「票の水増し」を1つの数値で表す（読み取り専用）。

Benchforge 分離原則: radbench は import せず results/ のみ参照。統計も自前（標準ライブラリのみ）。

考え方:
  複数の構成を並べて多数決や合議をしても、同じ問題で揃って間違えるなら
  「独立した意見」はその数だけ無い。誤りの相関から実質的な票数を推定する。

    φ（ファイ係数） = 2構成の「誤りの並び」の相関（二値の Pearson 相関）
    φ̄              = 全ペアの平均
    n_eff           = k / (1 + (k-1)·φ̄)   ＝相関のある k 票と同じ不確かさになる独立票の本数
    上限            = 1/φ̄                  ＝k→∞ の極限（候補を無限に増やしても超えられない）

出典: Guneet Kohli, "Nine Judges, Two Effective Votes: Correlated Errors Undermine LLM
      Evaluation Panels", arXiv:2605.29800, 2026-05-28. https://arxiv.org/abs/2605.29800
      同論文の 9 判定役パネル（φ̄=0.391）を同じ式に入れると n_eff=2.18 が再現されることを確認済み。
"""
from __future__ import annotations

import itertools
import math
import random

from pathlib import Path

from . import characterize as CH


def phi(a: list[int], b: list[int]) -> float | None:
    """二値ベクトルの Pearson 相関（φ係数）。定数ベクトル（全問正解/全問誤り）は定義できず None。"""
    n = len(a)
    if n == 0:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / math.sqrt(va * vb)


def _phi_bar(err: dict[str, dict[str, int]], keys: list[str], qids: list[str]) -> float | None:
    vals = []
    for i, j in itertools.combinations(keys, 2):
        r = phi([err[i][q] for q in qids], [err[j][q] for q in qids])
        if r is not None:
            vals.append(r)
    return sum(vals) / len(vals) if vals else None


def _n_eff(k: int, pbar: float) -> float:
    return k / (1 + (k - 1) * pbar)


def _ci(vals: list[float]) -> list[float]:
    v = sorted(vals)
    return [v[int(0.025 * len(v))], v[int(0.975 * len(v))]]


def analyze(results_dir: Path, configs: list[str], since: str = "2026-07-13",
            dataset: str | None = None, n_boot: int = 1000) -> dict:
    """構成群の n_eff 一式。configs は2つ以上。共通して採点された問題だけを使う。"""
    per, _ = CH.merged_results(results_dir, since, dataset)
    configs = [c for c in configs if c in per]
    if len(configs) < 2:
        return {"error": "構成を2つ以上選んでください（共通の問題で誤りの相関を測るため）"}
    common = sorted(set.intersection(*[set(per[c]) for c in configs]))
    if len(common) < 10:
        return {"error": f"共通して採点された問題が {len(common)} 問しかありません（10問以上必要）"}

    err = {c: {q: (0 if per[c][q]["correct"] else 1) for q in common} for c in configs}
    keys = [c for c in configs if len({err[c][q] for q in common}) > 1]
    dropped = [c for c in configs if c not in keys]
    if len(keys) < 2:
        return {"error": "全問正解または全問不正解の構成が多く、相関を計算できません"}

    pairs = []
    for i, j in itertools.combinations(keys, 2):
        r = phi([err[i][q] for q in common], [err[j][q] for q in common])
        if r is None:
            continue
        both = sum(1 for q in common if err[i][q] and err[j][q])
        pairs.append({"a": i, "b": j, "phi": r, "both_wrong": both})
    pbar = sum(p["phi"] for p in pairs) / len(pairs)
    k = len(keys)
    neff = _n_eff(k, pbar)

    # ブートストラップ（問題を復元抽出）
    ci = None
    if n_boot:
        rng = random.Random(42)
        ph, ne, ce = [], [], []
        for _ in range(n_boot):
            samp = [rng.choice(common) for _ in common]
            pb = _phi_bar(err, keys, samp)
            if pb is None or pb <= 0:
                continue
            ph.append(pb)
            ne.append(_n_eff(k, pb))
            ce.append(1 / pb)
        if ph:
            ci = {"phi_bar": _ci(ph), "n_eff": _ci(ne), "ceiling": _ci(ce), "n_boot": len(ph)}

    accs = {c: 1 - sum(err[c][q] for q in common) / len(common) for c in keys}
    double_fault = sum(1 for q in common if all(err[c][q] for c in keys))
    oracle = sum(1 for q in common if any(err[c][q] == 0 for c in keys))

    # 候補を増やしたときの n_eff（最良構成から貪欲に n_eff を最大化する順で追加）
    pmap = {(p["a"], p["b"]): p["phi"] for p in pairs}
    pmap.update({(p["b"], p["a"]): p["phi"] for p in pairs})
    curve = []
    sel = [max(accs, key=accs.get)]
    while len(sel) < k:
        best = None
        for c in keys:
            if c in sel:
                continue
            cand = sel + [c]
            ps = [pmap.get((a, b)) for a, b in itertools.combinations(cand, 2)]
            ps = [p for p in ps if p is not None]
            if not ps:
                continue
            ne = _n_eff(len(cand), sum(ps) / len(ps))
            if best is None or ne > best[1]:
                best = (c, ne)
        if best is None:
            break
        sel.append(best[0])
        curve.append({"k": len(sel), "n_eff": best[1], "added": best[0]})

    vals = sorted(p["phi"] for p in pairs)
    return {
        "configs": keys, "dropped": dropped, "n_questions": len(common), "k": k,
        "phi_bar": pbar, "n_eff": neff, "ceiling": 1 / pbar if pbar > 0 else None,
        "ci": ci,
        "pairs": sorted(pairs, key=lambda p: -p["phi"]),
        "accs": accs,
        "double_fault": double_fault, "double_fault_rate": double_fault / len(common),
        "oracle": oracle, "oracle_rate": oracle / len(common),
        "curve": curve,
        "phi_quartiles": [vals[len(vals) // 4], vals[len(vals) // 2], vals[3 * len(vals) // 4]],
        "n_negative_pairs": sum(1 for v in vals if v < 0), "n_pairs": len(vals),
    }
