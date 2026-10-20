# -*- coding: utf-8 -*-
"""夜間ランの実適用・制御（分離原則のサンクション例外）。

Benchforge は radbench コードを import しないが、ラン制御に限り **radbench の制御サーフェス**
にだけ書く: plans/tonight_plan.json（適用）/ runtime/PAUSE（一時停止）/ systemctl --user
（起動・停止・タイマー）。models.yaml は read-only で参照（キー照合のみ）。

⚠ 走行中ランの停止は破壊的 → フロントで確認必須。起動は active 中は拒否。
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path

import yaml

SERVICE = "radbench-trial.service"
TIMER = "radbench-trial.timer"


def _root(results_dir: Path) -> Path:
    return Path(results_dir).parent                  # results/ の親 = project root


def _plan_path(results_dir: Path) -> Path:
    return _root(results_dir) / "plans" / "tonight_plan.json"


def _pause_path(results_dir: Path) -> Path:
    return _root(results_dir) / "runtime" / "PAUSE"


def _models_yaml(results_dir: Path) -> Path:
    return _root(results_dir) / "config" / "models.yaml"


def _prompts_yaml(results_dir: Path) -> Path:
    return _root(results_dir) / "config" / "prompts" / "prompts.yaml"


def radbench_prompt_ids(results_dir: Path) -> set:
    """本体側に存在する prompt_id の集合。読めなければ空（＝検査をスキップ）。"""
    try:
        d = yaml.safe_load(_prompts_yaml(results_dir).read_text(encoding="utf-8")) or {}
        ps = d.get("prompts", d)
        return set(ps.keys()) if isinstance(ps, dict) else set()
    except Exception:  # noqa: BLE001
        return set()


def _systemctl(*args) -> tuple[int, str]:
    try:
        r = subprocess.run(["systemctl", "--user", *args],
                           capture_output=True, text=True, timeout=20)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def radbench_model_keys(results_dir: Path) -> set:
    try:
        d = yaml.safe_load(_models_yaml(results_dir).read_text(encoding="utf-8")) or {}
        ms = d.get("models", d)
        return set(ms.keys()) if isinstance(ms, dict) else set()
    except Exception:  # noqa: BLE001
        return set()


def _tag_to_key(tag: str) -> str:
    return re.sub(r"[:.\-]", "_", tag)


def translate_plan(plan: dict, keys: set, prompt_ids: set | None = None) -> tuple[dict, list]:
    """plan 内 stage.model(ollamaタグ)を radbench キーへ変換。未登録は warnings に。

    ★prompt_id も検査する（2026-08-06 に実害）: Benchforge 内蔵プロンプトは `mc`/`findings`/
    `reason`/`judge` という名前だが、**本体側の prompt_id は別名**のことがある（radbench は
    `mc_v1`/`findings_vf1`/`reason_rf1`/`arbiter_v1`）。名前が無いと本体は**空のプロンプトで実行し、
    全問「解答なし」＝正答率0%になるのに error は付かない**（＝成功したように見える）。
    起動前にここで気づけるようにする。
    """
    p = copy.deepcopy(plan)
    warns = []
    for c in p.get("configs", []):
        for st in c.get("stages", []):
            m = st.get("model")
            if m:
                k = _tag_to_key(m)
                if keys and k not in keys:
                    warns.append(f"モデル {m} → {k}（models.yaml に未登録・要確認）")
                st["model"] = k
            pid = st.get("prompt_id")
            if pid and prompt_ids and pid not in prompt_ids:
                cand = [x for x in sorted(prompt_ids) if x.startswith(pid)]
                hint = f" 候補: {', '.join(cand[:3])}" if cand else ""
                warns.append(f"★プロンプト '{pid}' が本体側に存在しません。"
                             f"このまま実行すると全問「解答なし」（正答率0%）になります。{hint}")
    return p, warns


def status(results_dir: Path) -> dict:
    _, svc = _systemctl("is-active", SERVICE)
    _, tmr = _systemctl("is-active", TIMER)
    _, tenabled = _systemctl("is-enabled", TIMER)
    return {"service": svc, "timer_active": tmr, "timer_enabled": tenabled,
            "paused": _pause_path(results_dir).exists(),
            "plan_exists": _plan_path(results_dir).is_file()}


def apply_plan(results_dir: Path, plan: dict) -> dict:
    """plan を radbench の plans/tonight_plan.json に書く（モデルをキーへ変換）。"""
    keys = radbench_model_keys(results_dir)
    tp, warns = translate_plan(plan, keys, radbench_prompt_ids(results_dir))
    fp = _plan_path(results_dir)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(tp, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "written": str(fp), "warnings": warns, "plan": tp}


def start(results_dir: Path) -> dict:
    # oneshot 実行中は is-active が "activating" を返す。両方を実行中扱いにする。
    if status(results_dir)["service"] in ("active", "activating"):
        return {"ok": False, "msg": "既にラン実行中です（二重起動しません）"}
    if not _plan_path(results_dir).is_file():
        return {"ok": False, "msg": "plans/tonight_plan.json がありません。先に「適用」してください"}
    # run_trial は数分〜数時間かかる。--no-block を付けないと systemctl start が
    # ラン完了までブロックし、クライアントが timeout（=失敗に見える）。投入だけ行い、
    # 進捗は status（activating/active）で確認する設計にする。
    rc, out = _systemctl("start", "--no-block", SERVICE)
    if rc != 0:
        return {"ok": False, "msg": "起動に失敗しました: " + (out or "不明なエラー")}
    running = status(results_dir)["service"] in ("active", "activating")
    return {"ok": True, "msg": "起動しました（バックグラウンドで実行中）。進捗は『状態更新』で確認してください"
            if running else "起動を要求しました。『状態更新』で実行中になるか確認してください"}


def stop(results_dir: Path) -> dict:
    rc, out = _systemctl("stop", SERVICE)
    return {"ok": rc == 0, "msg": out or "停止しました"}


def pause(results_dir: Path) -> dict:
    p = _pause_path(results_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    return {"ok": True, "msg": "PAUSE を設置（次の問題境界で待機）"}


def resume(results_dir: Path) -> dict:
    p = _pause_path(results_dir)
    if p.exists():
        p.unlink()
    return {"ok": True, "msg": "PAUSE を解除（再開）"}


def set_timer(results_dir: Path, oncalendar: str) -> dict:
    """radbench-trial.timer を作成/更新して有効化（毎晩 OnCalendar で自動起動）。"""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / TIMER).write_text(
        "[Unit]\nDescription=radbench nightly trial timer (Benchforge管理)\n\n"
        f"[Timer]\nOnCalendar={oncalendar}\nPersistent=true\nUnit={SERVICE}\n\n"
        "[Install]\nWantedBy=timers.target\n", encoding="utf-8")
    _systemctl("daemon-reload")
    rc, out = _systemctl("enable", "--now", TIMER)
    return {"ok": rc == 0, "msg": out or f"タイマー設定: {oncalendar}", "oncalendar": oncalendar}


def clear_timer(results_dir: Path) -> dict:
    rc, out = _systemctl("disable", "--now", TIMER)
    return {"ok": rc == 0, "msg": out or "タイマー解除"}
