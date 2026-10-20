# -*- coding: utf-8 -*-
"""選抜バイアス（勝者の呪い）の診断（読み取り専用）。

Benchforge 分離原則: radbench は import せず results/ のみ参照。統計も自前（標準ライブラリのみ）。

考え方:
  多数の構成を同じ問題群で測り、最も成績の良かったものを採る——という当たり前の手順が、
  選ばれた構成の成績を系統的に過大評価させる。たまたま調子の良かった個体が選ばれやすいため。
  「本当に優れている」のか「たくさん試したから高い値が出た」のかを見分ける。

出典: Yang Xu, Jiefu Zhang, Haixiang Sun, Zihan Zhou, Tianyu Cao, Vaneet Aggarwal.
      "Towards Reliable LLM Evaluation: Correcting the Winner's Curse in Adaptive
      Benchmarking." arXiv:2605.05973, 2026-05. https://arxiv.org/abs/2605.05973

提供する3つの診断:
  [1] 最大値バイアス — 「全構成の実力が基準構成と同じ」という仮定の下で、K 構成の最大値が
      どこまで届くかをシミュレート。構成同士は似た間違い方をする（相関する）ため、
      実効独立構成数 K_eff = K/(1+(K-1)·φ̄) でも計算し、両者で挟んで評価する。
  [2] 順位の安定性 — 問題を復元抽出して、どの構成が何%の確率で「最良」になるか。
  [3] Holm 補正 — 基準構成との総当たり比較（characterize.significance_holm と同じ考え方）。
"""
from __future__ import annotations

import collections
import itertools
import math
import random
from math import comb
from pathlib import Path

from . import characterize as CH


def _phi(a: list[int], b: list[int]) -> float | None:
    """二値ベクトルの Pearson 相関（φ係数）。定数ベクトルは定義できず None。"""
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def _mcnemar_p(per, qs, a: str, b: str) -> tuple[int, int, float]:
    b01 = sum(1 for q in qs if not per[a][q]["correct"] and per[b][q]["correct"])
    b10 = sum(1 for q in qs if per[a][q]["correct"] and not per[b][q]["correct"])
    n = b01 + b10
    if n == 0:
        return b01, b10, 1.0
    k = min(b01, b10)
    return b01, b10, min(1.0, sum(comb(n, i) for i in range(k + 1)) / (2 ** n) * 2)


def _max_sim(k_draws: int, p0: float, n: int, trials: int, rng: random.Random) -> list[float]:
    """真の正答率が全構成 p0 のとき、k_draws 個の観測値の最大値の分布。"""
    out = []
    ki = max(1, int(round(k_draws)))
    for _ in range(trials):
        best = 0.0
        for _ in range(ki):
            v = sum(1 for _ in range(n) if rng.random() < p0) / n * 100
            if v > best:
                best = v
        out.append(best)
    out.sort()
    return out


def analyze(results_dir: Path, configs: list[str], baseline: str = "",
            since: str = "2026-07-13", dataset: str | None = None,
            n_boot: int = 1000, n_sim: int = 1500) -> dict:
    """選抜バイアス診断一式。configs は3つ以上（最大値の議論が意味を持つため）。"""
    per, _ = CH.merged_results(results_dir, since, dataset)
    configs = [c for c in configs if c in per]
    if len(configs) < 3:
        return {"error": "構成を3つ以上選んでください（「多数から最良を選ぶ」状況を診断するため）"}
    common = sorted(set.intersection(*[set(per[c]) for c in configs]))
    if len(common) < 20:
        return {"error": f"共通して採点された問題が {len(common)} 問しかありません（20問以上必要）"}

    n = len(common)
    acc = {c: sum(1 for q in common if per[c][q]["correct"]) for c in configs}
    best_cfg = max(acc, key=acc.get)
    # ★帰無「全構成の実力が同じ」の共通正答率 p0 は、選ばれた構成の平均を使う（最尤推定）。
    #   ⚠最下位や最上位を基準にすると土台が低すぎ/高すぎて診断が壊れる
    #   （最下位を既定にしていた初版は、帰無データでも「偶然では説明できない」と誤判定した）。
    p0 = sum(acc.values()) / len(acc) / n
    # Holm 比較の基準構成: 指定がなければ中央値の構成（最下位を基準にすると差が誇張されるため）
    ordered = sorted(acc, key=acc.get)
    base = baseline if baseline in acc else ordered[len(ordered) // 2]
    rng = random.Random(42)

    # [1] 最大値バイアス
    err = {c: {q: (0 if per[c][q]["correct"] else 1) for q in common} for c in configs}
    keys = [c for c in configs if len({err[c][q] for q in common}) > 1]
    phis = []
    for i, j in itertools.combinations(keys, 2):
        r = _phi([err[i][q] for q in common], [err[j][q] for q in common])
        if r is not None:
            phis.append(r)
    K = len(configs)
    pbar = sum(phis) / len(phis) if phis else 0.0
    keff = K / (1 + (K - 1) * pbar) if pbar > 0 else K
    best_acc = 100 * acc[best_cfg] / n
    sims = []
    for kx, label in ((K, "独立と仮定"), (keff, "相関を考慮")):
        mx = _max_sim(kx, p0, n, n_sim, rng)
        sims.append({
            "label": label, "k": kx,
            "mean": sum(mx) / len(mx), "p95": mx[int(0.95 * len(mx))],
            "exceed": sum(1 for x in mx if x >= best_acc) / len(mx),
        })

    # [2] 順位の安定性
    win = collections.Counter()
    ranks = collections.defaultdict(list)
    for _ in range(n_boot):
        samp = [rng.choice(common) for _ in common]
        a = {c: sum(1 for q in samp if per[c][q]["correct"]) for c in configs}
        order = sorted(configs, key=lambda c: -a[c])
        win[order[0]] += 1
        for i, c in enumerate(order):
            ranks[c].append(i + 1)
    stability = []
    for c in sorted(configs, key=lambda x: -acc[x]):
        r = sorted(ranks[c])
        stability.append({
            "config": c, "acc": 100 * acc[c] / n, "correct": acc[c],
            "win_rate": win[c] / n_boot,
            "rank_lo": r[int(0.025 * len(r))], "rank_hi": r[int(0.975 * len(r))],
        })

    # [3] Holm 補正（基準構成との総当たり）
    pv = {}
    for c in configs:
        if c == base:
            continue
        b01, b10, p = _mcnemar_p(per, common, base, c)
        pv[c] = {"rescued": b01, "broken": b10, "p": p}
    srt = sorted(pv.items(), key=lambda x: x[1]["p"])
    m, prev = len(srt), 0.0
    holm = []
    for i, (c, d) in enumerate(srt):
        adj = min(1.0, max(prev, (m - i) * d["p"]))
        prev = adj
        holm.append({"config": c, **d, "holm": adj, "sig": adj < 0.05})

    return {
        "n_questions": n, "k": K, "baseline": base, "baseline_acc": 100 * acc[base] / n, "null_acc": 100 * p0,
        "best": best_cfg, "best_acc": best_acc,
        "phi_bar": pbar, "k_eff": keff, "sims": sims,
        "stability": stability, "holm": holm,
    }
