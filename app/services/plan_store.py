"""作成した構成(プラン)を Benchforge/data/plans に保存・読込（radbench には書かない）。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import naming


def _safe_name(name: str) -> str:
    """読み取り経路の防御用（外部から来た名前でパスを組ませない）。
    ⚠ 保存名を決めるのに使わない＝別名が同じファイルに衝突して黙って上書きされるため。"""
    return naming.sanitize(name, "plan")


SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "sample_plans"


def seed_samples(plans_dir: Path) -> int:
    """同梱のサンプル構成を data/plans に置く（初回だけ・既存は絶対に上書きしない）。
    ★これが無いと配布版は「保存済みの構成」が空で、利用者は白紙から組む羽目になる。
      同梱の成績データ（model_A / ensemble_AB）に対応する構成が見えないという指摘に対応
      （2026-08-07）。利用者が同名で保存したものは尊重する＝存在すれば触らない。"""
    if not SAMPLES_DIR.is_dir():
        return 0
    plans_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in sorted(SAMPLES_DIR.glob("*.json")):
        dst = plans_dir / src.name
        if not dst.exists():
            dst.write_bytes(src.read_bytes())
            n += 1
    return n


def list_plans(plans_dir: Path) -> list[dict]:
    plans_dir.mkdir(parents=True, exist_ok=True)
    seed_samples(plans_dir)
    out = []
    for fp in sorted(plans_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.append({
            "name": fp.stem,
            "configs": len(doc.get("configs", [])),
            "split": doc.get("split"),
            "updated": datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds"),
        })
    return out


def load_plan(plans_dir: Path, name: str) -> dict | None:
    fp = plans_dir / f"{_safe_name(name)}.json"
    if not fp.is_file():
        return None
    return json.loads(fp.read_text(encoding="utf-8"))


def save_plan(plans_dir: Path, name: str, plan: dict) -> str:
    """⚠ 使えない文字は **ValueError で弾く**（黙って `_` に置換しない）。
    置換すると「検証1」と「実験1」がどちらも `_1.json` になり、後の保存が前を消す。"""
    safe = naming.validate(name, "プラン名")
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan = dict(plan)
    plan["_saved_at"] = datetime.now().isoformat(timespec="seconds")
    (plans_dir / f"{safe}.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return safe
