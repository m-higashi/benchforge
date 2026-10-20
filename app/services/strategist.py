"""strategist — 結果統計＋総論を読み、次に試す実験プランを提案する。

統計を入力に LLM がプラン案を生成し、
良ければ保存する。Benchforge 自前 ollama で完結（radbench 非依存・分離原則）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.services import summary_builder as SB

# LLM に渡すプラン JSON のスキーマ例（Benchforge プラン形式＝stage.model は ollama タグ）
PLAN_SCHEMA_HINT = """{
  "date": "TBD",
  "_purpose": "この提案の狙いを日本語1〜2文で",
  "split": "dev",
  "time_budget": {"wiki_max_min": 0, "trial_min": 480, "per_question_sec": 1200},
  "max_configs": 3,
  "configs": [
    {
      "config_id": "一意な構成名",
      "_note": "この構成の狙い",
      "temperature": 0.0,
      "stages": [{"type": "direct_mc", "model": "<ollamaタグ>", "prompt_id": "mc"}]
    }
  ]
}"""


def build_context(results_dir: Path, summary_path: Path, models: list[str],
                  period: str = "all", summary_md: str | None = None) -> dict:
    """period で統計を期間絞り込み。summary_md を渡せばその総論を文脈に使う
    （未指定なら summary_path＝最新総論を読む）。"""
    stats = SB.build_stats(results_dir, period)
    if summary_md is None:
        summary_md = summary_path.read_text(encoding="utf-8") if summary_path.is_file() else ""
    return {"stats_text": stats["text"], "n_configs": stats["n_configs"],
            "summary_md": summary_md, "models": models}


def build_prompt(ctx: dict, note: str = "") -> tuple[str, str]:
    models = ", ".join(ctx["models"]) or "(取得不可)"
    system = (
        "あなたは試験問題ベンチマーク（画像を含む多肢選択問題）の実験を設計する戦略家です。"
        "目標は正答率の向上。与えられた結果統計と総論『のみ』に基づき、次に試す価値が最も高い"
        "構成プランを1つ提案します。誇張せず数値根拠を添え、3〜4問差は誤差として扱います。\n"
        "使える手立て: think=on(推論モード/stageに think:true), self-consistency(temperature>0で同一構成を"
        "サンプリングし多数決), 多様な複数モデルの vote(多数決), プロンプト改良。"
        "stage.type は direct_mc/vision_findings/reason/vote。\n"
        "出力は日本語で、まず【提案理由】を数値根拠付きで簡潔に述べ、最後に必ず ```json コードブロックで"
        "下記スキーマに沿うプランを1つだけ出力してください。model は『利用可能モデル』のタグから選ぶこと。"
    )
    user = (
        f"利用可能モデル(ollamaタグ): {models}\n\n"
        f"プランJSONスキーマ:\n```json\n{PLAN_SCHEMA_HINT}\n```\n\n"
        + (f"【ユーザの方針メモ（優先して考慮）】\n{note}\n\n" if note.strip() else "")
        + f"=== 結果統計 ===\n{ctx['stats_text']}\n\n"
        + (f"=== これまでの総論 ===\n{ctx['summary_md']}\n" if ctx["summary_md"] else "（総論は未生成）\n")
    )
    return system, user


def parse_plan(text: str) -> tuple[dict | None, str | None]:
    """LLM 出力から末尾の ```json ブロックを取り出して検証する。(plan, error)。"""
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = blocks[-1] if blocks else None
    if raw is None:
        m = re.search(r"\{.*\}", text, re.DOTALL)   # フォールバック: 最初の { 〜 最後の }
        raw = m.group(0) if m else None
    if raw is None:
        return None, "プランの JSON ブロックが見つかりませんでした"
    try:
        plan = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        return None, f"JSON 解析に失敗: {e}"
    if not isinstance(plan, dict) or not isinstance(plan.get("configs"), list) or not plan["configs"]:
        return None, "configs 配列が空、または形式が不正です"
    return plan, None
