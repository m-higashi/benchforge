"""結果(trials)を集計し、LLM に渡す統計テキストと、Wiki風総論を書かせるプロンプトを作る。
Benchforge 自前 ollama で生成する（radbench 非依存）。"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from app.services import results_reader as RR

PERIOD_LABEL = {"all": "全体", "week": "過去1週間", "month": "過去1ヶ月"}


def _run_in_period(run_id, period: str) -> bool:
    """run_id(先頭YYYY-MM-DD)が期間内か。all/不明は含める。"""
    if period in (None, "", "all"):
        return True
    try:
        d = date.fromisoformat((run_id or "")[:10])
    except Exception:  # noqa: BLE001
        return True
    days = 7 if period == "week" else 30 if period == "month" else 36500
    return d >= date.today() - timedelta(days=days)


def _stage_label(stages) -> str:
    """stages(型リスト or config_stages)からアンサンブル戦略ラベルを作る。"""
    types = []
    for s in stages or []:
        types.append(s.get("type") if isinstance(s, dict) else s)
    base = "direct"
    if "vision_findings" in types and "reason" in types:
        base = "findings→reason"
    if "vote" in types:
        base = "vote(アンサンブル)"
    return base


def build_stats(results_dir: Path, period: str = "all") -> dict:
    """最新ラン優先で (config_id, version) 単位に集計し、統計 dict と LLM 用 text を返す。
    period: all/week/month で対象ランを期間フィルタ（run_id の日付基準）。"""
    items = [g for g in RR.aggregate_benchmarks(results_dir)
             if _run_in_period(g.get("run_id"), period)]
    # (config_id, version) ごとに最新 run を採用
    latest: dict[tuple, dict] = {}
    for g in items:
        key = (g["config_id"], g["version"])
        if key not in latest or (g["run_id"] or "") > (latest[key]["run_id"] or ""):
            latest[key] = g
    configs = sorted(latest.values(), key=lambda g: g["accuracy"], reverse=True)

    # モデル別・戦略別の平均
    by_model: dict[str, list] = {}
    by_strategy: dict[str, list] = {}
    for g in configs:
        if not g["scored"]:
            continue
        for m in (g.get("models") or []):
            by_model.setdefault(m, []).append(g["accuracy"])
        lbl = _stage_label(g.get("config_stages") or g.get("stages"))
        by_strategy.setdefault(lbl, []).append(g["accuracy"])

    def avg(xs):
        return sum(xs) / len(xs) if xs else 0.0

    lines = ["# 結果統計（機械生成・総論の素データ）", ""]
    versions = sorted({g["version"] for g in configs})
    lines.append(f"- 集計期間: {PERIOD_LABEL.get(period, period)}")
    lines.append(f"- バージョン: {', '.join(versions)}")
    lines.append(f"- 構成数: {len(configs)}")
    lines += ["", "## 構成別 正答率（高い順）",
              "| 構成 | 正答率 | 正答/採点 | 戦略 | モデル | 量子化 | backend | think | temp | version |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for g in configs:
        lines.append(f"| {g['config_id']} | {g['accuracy']*100:.1f}% | "
                     f"{g['correct']}/{g['scored']} | {_stage_label(g.get('config_stages') or g.get('stages'))} | "
                     f"{','.join(g.get('models') or [])} | {g.get('quant', '—')} | {g.get('backend', 'ollama')} | "
                     f"{g.get('think', '—')} | {g.get('temperature')} | {g['version']} |")
    lines += ["", "## モデル別 平均正答率（構成横断・参考）"]
    for m, xs in sorted(by_model.items(), key=lambda kv: avg(kv[1]), reverse=True):
        lines.append(f"- {m}: {avg(xs)*100:.1f}%（{len(xs)}構成）")
    lines += ["", "## アンサンブル戦略別 平均正答率"]
    for s, xs in sorted(by_strategy.items(), key=lambda kv: avg(kv[1]), reverse=True):
        lines.append(f"- {s}: {avg(xs)*100:.1f}%（{len(xs)}構成）")

    return {"text": "\n".join(lines), "n_configs": len(configs),
            "versions": versions, "period": period}


def build_prompt(stats_text: str):
    system = ("あなたは試験問題ベンチマークの結果を分析し、解説記事風の総論を書く"
              "分析者です。与えられた結果統計のみに基づき、誇張せず、数値根拠を添えて簡潔にまとめます。"
              "出力は日本語の Markdown。")
    user = (
        "以下の結果統計から、Wiki風の総論を Markdown で書いてください。見出し構成:\n"
        "## 全体傾向 / ## モデル別の傾向 / ## アンサンブル戦略の傾向 / "
        "## パラメータ(temp等)の影響 / ## 注目点と次の仮説\n\n"
        "注意:\n- 統計に無いことは書かない（推測で数値を作らない）\n"
        "- 3〜4問差は誤差として扱い、過度な結論を避ける\n"
        "- 各主張に数値(正答率や構成名)を添える\n\n"
        f"=== 結果統計 ===\n{stats_text}")
    return system, user
