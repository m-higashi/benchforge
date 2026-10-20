"""プロンプトライブラリ（Benchforge 自前・data/prompts.json）。初回は既定を書き出す。

プレースホルダ: {question} {captions} {findings} {candidates}
解答ステージ（direct_mc / reason / judge）は末尾に「解答: X」を要求する契約。
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULTS: dict[str, dict] = {
    "mc": {
        "description": "直接記号回答（direct_mc 向け・最も基本）",
        "system": "あなたは試験問題を解くアシスタントです。"
                  "提示された画像と設問・選択肢を読み、最も適切な選択肢を選びます。",
        "user": "以下の画像と設問に答えてください。\n{captions}\n\n"
                "{question}\n\n"
                "推論は簡潔に。最終行に必ず「解答: X」の形式で記号のみ"
                "（複数正解なら 解答: d,e）を書いてください。",
    },
    "findings": {
        "description": "画像の観察結果を文章にする（vision_findings 向け）",
        "system": "あなたは提示された画像を観察し、気づいた点を、結論を断定せず"
                  "客観的に記述します。",
        "user": "次の画像について、観察される特徴を箇条書きで簡潔に述べてください。\n"
                "{captions}\n\n設問の文脈:\n{question}",
    },
    "reason": {
        "description": "観察結果＋設問から根拠を述べて選ぶ（reason・findings の後段）",
        "system": "あなたは試験問題を解くアシスタントです。観察結果と設問・選択肢から、"
                  "根拠を述べて最も適切な選択肢を1つ（複数正解なら該当全て）選びます。",
        "user": "観察結果:\n{findings}\n\n設問:\n{question}\n\n"
                "根拠を簡潔に述べた後、最終行に必ず「解答: X」（複数は 解答: d,e）を書いてください。",
    },
    "judge": {
        "description": "合議の審査役（複数の回答案を比較して最終解答を決定）",
        "system": "あなたは複数の回答案を比較して最終解答を決める審査役です。"
                  "各案の根拠の妥当性を吟味し、多数意見に安易に流されず、最も適切な選択肢を決定します。",
        "user": "設問:\n{question}\n\n各回答案:\n{candidates}\n\n"
                "各案を比較し、最も妥当な解答を選んでください。判断の根拠を簡潔に述べた後、"
                "最終行に必ず「解答: X」（複数は 解答: d,e）を書いてください。",
    },
}


def load_all(path: Path) -> dict:
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        return dict(DEFAULTS)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return dict(DEFAULTS)


def save_one(path: Path, pid: str, system: str, user: str, description: str = "") -> str:
    pid = (pid or "").strip()
    if not pid:
        raise ValueError("prompt id required")
    d = load_all(path)
    d[pid] = {"system": system or "", "user": user or "", "description": description or ""}
    path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return pid


def delete_one(path: Path, pid: str) -> bool:
    """プロンプトを1件消す。改名（保存→旧を削除）の後半で使う。
    既定のプロンプトは消せない＝消すと構成が壊れるため。"""
    data = load_all(path)
    if pid in DEFAULTS or pid not in data:
        return False
    data.pop(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True
