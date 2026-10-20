# -*- coding: utf-8 -*-
"""夜間ラン設定（回すデータ / モデル / タイマー）の組み立て・保存。

Benchforge 分離原則: 書き込みは **Benchforge/data/nightly/ のみ**。radbench の plans/ や
systemd には触れない（適用は画面に表示するコマンドで人間/ラン idle 時に行う）。
出力プランは radbench の tonight_plan 形式に合わせる（人間がそのままコピーして使える）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _ndir(data_dir: Path) -> Path:
    d = data_dir / "nightly"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sample_dir(data_dir: Path) -> Path:
    return data_dir / "samples" / "nightly"


def sample_info(data_dir: Path) -> dict:
    sd = sample_dir(data_dir)
    qs = list((sd / "questions").rglob("*.yaml")) if sd.is_dir() else []
    return {"exists": sd.is_dir(), "dir": str(sd), "questions": len(qs),
            "dataset_dir": str(sd / "questions"),
            "answers_csv": str(sd / "answers.csv"),
            "label_path": str(sd / "label_sample.csv"),
            "plan": str(sd / "tonight_plan.sample.json")}


def oncalendar(time_hhmm: str, days: list[str]) -> str:
    """systemd OnCalendar 文字列を作る。days 空 or 7曜全部 = 毎日。"""
    try:
        hh, mm = (time_hhmm or "21:00").split(":")[:2]
        hh, mm = int(hh), int(mm)
    except Exception:  # noqa: BLE001
        hh, mm = 21, 0
    day = ""
    if days and 0 < len(days) < 7:
        order = {d: i for i, d in enumerate(WEEKDAYS)}
        day = ",".join(sorted(set(days), key=lambda d: order.get(d, 9))) + " "
    return f"{day}*-*-* {hh:02d}:{mm:02d}:00 Asia/Tokyo"   # 入力時刻はJST（systemd255のTZ明示）


def assemble_plan(form: dict) -> dict:
    """フォーム → radbench tonight_plan 形式。モデル列 + prompt + vote有無 から configs 自動生成。"""
    models = [m for m in (form.get("models") or []) if m]
    prompt_id = form.get("prompt_id") or "mc"
    temp = float(form.get("temperature", 0.0) or 0.0)

    # サンプリング候補集合の制御（任意）。空/未指定なら None＝ollama 既定に委ねる（キーを出さない）。
    # ⚠ temperature==0 では top_k/top_p は無効（argmax 確定）。効かせるには temperature>0 が要る。
    def _num(v, cast):
        if v is None or v == "":
            return None
        try:
            return cast(v)
        except (TypeError, ValueError):
            return None
    top_k = _num(form.get("top_k"), int)
    top_p = _num(form.get("top_p"), float)

    def _sampling(cfg: dict) -> dict:
        if top_k is not None:
            cfg["top_k"] = top_k
        if top_p is not None:
            cfg["top_p"] = top_p
        return cfg

    configs = []
    for m in models:
        key = m.split(":")[0].replace(".", "_").replace("-", "_")
        configs.append(_sampling({"config_id": f"base_{key}",
                        "stages": [{"type": "direct_mc", "model": m, "prompt_id": prompt_id}],
                        "temperature": temp}))
    if form.get("add_vote") and len(models) >= 2:
        stages = [{"type": "direct_mc", "model": m, "prompt_id": prompt_id} for m in models]
        stages.append({"type": "vote"})
        vid = "vote_" + "_".join(m.split(":")[0][:6] for m in models)
        configs.append(_sampling({"config_id": vid, "stages": stages, "temperature": temp}))
    plan: dict = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "_purpose": form.get("purpose") or "（Benchforge 夜間ラン設定タブで生成）",
        "split": form.get("split") or "dev",
        "time_budget": {
            "wiki_max_min": int(form.get("wiki_max_min", 0) or 0),
            "trial_min": int(form.get("trial_min", 600) or 600),
            "per_question_sec": int(form.get("per_question_sec", 1200) or 1200),
        },
        "max_configs": int(form.get("max_configs") or (len(configs) or 1)),
        "configs": configs,
    }
    for k in ("dataset_dir", "label_path", "answers_csv"):
        if form.get(k):
            plan[k] = form[k]
    return plan


def preview(form: dict) -> dict:
    return {"plan": assemble_plan(form),
            "oncalendar": oncalendar(form.get("time", "21:00"), form.get("days") or [])}


def save(data_dir: Path, form: dict) -> dict:
    nd = _ndir(data_dir)
    plan = assemble_plan(form)
    cal = oncalendar(form.get("time", "21:00"), form.get("days") or [])
    cfg = {"saved_at": datetime.now().isoformat(timespec="seconds"), "form": form, "oncalendar": cal}
    (nd / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    (nd / "tonight_plan.preview.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
    return {"plan": plan, "oncalendar": cal,
            "saved": str(nd / "config.json"),
            "plan_file": str(nd / "tonight_plan.preview.json")}


def load(data_dir: Path) -> Optional[dict]:
    p = _ndir(data_dir) / "config.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return None
